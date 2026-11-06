"""Walking a graph's serialized JSON together with the C# types of its fields.

The JSON alone does not tell an int from an index into ``_objectReferences``, nor what type a ``BBParameter`` of a
generic task expects. The walker follows the field declarations (substituting generic parameters along the
inheritance chain: ``SetVariable<T>``, ``class Foo : Bar<Pawn>``) and records

* object slots: ``(holder, key)`` of every int that indexes ``_objectReferences``;
* ``maybe``: ints in fields whose type it cannot tell, which may be such indices, so nothing may rely on them
  being free;
* bound parameters: parameters with a variable, with the type they expect.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

from .container import ObjectRef
from .csharp import ClassDecl
from .model import NESTED_KEYS, Graph
from .render import UNITY_OBJECT_BASES, _bb_inner
from .schema import bare_name, generic_args, generic_text_args, simple_name
from .typenames import ALIASES
from .typerel import UNITY_VALUE_TYPES

WRITE_FIELDS = {
    "saveAs",
    "returnValue",
    "current",
    "storeIndex",
    "boolVariable",
    "floatVariable",
    "intVariable",
    "saveIndexAs",
    "saveDistanceAs",
    "saveObjectAs",
    "savePositionAs",
    "saveTo",
}
_PARAM_TYPES = ("BBParameter", "BBObjectParameter", "BBMappingParameter", "TaskAgentParameter")
_CLR_PRIMITIVES = set(ALIASES.values())
# Top-level graph fields that never hold object references.
_NO_REFS = {"type", "nodes", "connections", "canvasGroups", "localBlackboard", "translation", "zoomFactor", "comments"}
_SKIP_KEYS = {"$type", "$id", "$version", "$ref"}


def is_write_field(key: Any, owner_type: str) -> bool:
    """Whether a parameter in this field stores into its variable rather than reads from it."""
    return key in WRITE_FIELDS or (key == "valueA" and simple_name(owner_type).startswith("Set"))


@dataclass
class BoundParam:
    """A parameter bound to a blackboard variable."""

    param: dict
    expected: Optional[str]  # the CLR type it works with; None when unknown
    field: Any  # the field (or list index) holding it
    path: Tuple  # from the walked root
    owner_type: str  # $type of the object declaring the field


class Usage(NamedTuple):
    slots: List[Tuple[Any, Any]]
    maybe: Set[int]
    unsafe: bool  # something could not be read at all: treat every index as used


class _Walker:
    def __init__(self, ws):
        self.ws = ws
        self.schema = ws.schema
        self.slots: Dict[Tuple[int, Any], Tuple[Any, Any]] = {}
        self.maybe: Set[int] = set()
        self.params: List[BoundParam] = []
        self.unsafe = False
        self._envs: Dict[str, Dict[str, Dict[str, str]]] = {}

    # ------------------------------------------------------------------ types

    def _is_object(self, t: Optional[str], ctx: Optional[ClassDecl]) -> bool:
        if not t or "<" in t or "`" in t or t.endswith("[]"):
            return False
        name = t.split(".")[-1]
        if name in UNITY_OBJECT_BASES:
            return True
        decl = self.schema.find(t) or self.schema.find_simple(name, ctx)
        return decl is not None and any(
            any(re.sub(r"<.*>", "", b).split(".")[-1] in UNITY_OBJECT_BASES for b in c.bases)
            for c in self.schema.chain(decl)
        )

    def _is_plain(self, t: Optional[str], ctx: Optional[ClassDecl]) -> bool:
        """Types whose ints are plain numbers: primitives and enums."""
        if not t:
            return False
        if t in ALIASES or t in _CLR_PRIMITIVES:
            return True
        decl = self.schema.find(t) if "." in t else None
        if decl is not None and decl.kind == "enum":
            return True
        return self.schema.enum_for(t, ctx) is not None

    def _generic_env(self, decl: ClassDecl, type_name: str) -> Dict[str, Dict[str, str]]:
        """``class full name -> {type parameter: CLR type}`` along the inheritance chain of a concrete type."""
        if type_name in self._envs:
            return self._envs[type_name]
        out: Dict[str, Dict[str, str]] = {}
        cur: Optional[ClassDecl] = decl
        values = generic_args(type_name)
        while cur is not None and cur.full_name not in out:
            env = dict(zip(cur.type_params, values))
            out[cur.full_name] = env
            base = next(
                (
                    (b, found)
                    for b in cur.bases
                    if (found := self.schema.resolve_base(cur, b)) is not None and found.kind != "interface"
                ),
                None,
            )
            if base is None:
                break
            values = [env.get(a) or self.ws.types.full(a, cur) for a in generic_text_args(base[0])]
            cur = base[1]
        self._envs[type_name] = out
        return out

    @staticmethod
    def _subst(text: Optional[str], env: Optional[Dict[str, str]]) -> Optional[str]:
        if not text or not env:
            return text
        if text in env:
            return env[text]
        return re.sub(r"\b\w+\b", lambda m: env.get(m.group(0), m.group(0)), text)

    def _expected(self, v: dict, t: Optional[str], ctx: Optional[ClassDecl]) -> Optional[str]:
        if v.get("_type"):
            return bare_name(v["_type"])
        boxed = generic_args(v.get("$type") or "")
        inner = boxed[0] if boxed and "BBParameter" in v.get("$type", "") else _bb_inner(t)
        if not inner:
            return None
        if "`" in inner or "." in inner.split("<")[0]:
            return bare_name(inner) if "`" in inner else self.ws.types.full(inner, ctx)
        if ctx is not None and set(re.findall(r"\b\w+\b", inner)) & set(ctx.type_params):
            return None  # an open generic parameter, nothing says what it stands for
        return self.ws.types.full(inner, ctx)

    # ------------------------------------------------------------------ walking

    def visit(
        self,
        v: Any,
        t: Optional[str],
        ctx: Optional[ClassDecl],
        holder: Any,
        key: Any,
        path: Tuple,
        owner_type: str,
    ) -> None:
        if v is None or isinstance(v, (bool, float, str)):
            return
        if isinstance(v, int):
            if self._is_object(t, ctx):
                self.slots[(id(holder), key)] = (holder, key)
            elif not self._is_plain(t, ctx):
                self.maybe.add(v)
            return
        if isinstance(v, list):
            et = _element(t)
            for i, x in enumerate(v):
                self.visit(x, et, ctx, v, i, path + (i,), owner_type)
            return
        if "$content" in v:
            self.visit(v["$content"], v.get("$type") or t, ctx, v, "$content", path + ("$content",), owner_type)
            return
        if self._is_param(v, t):
            expected = self._expected(v, t, ctx)
            if v.get("_targetVariableID"):
                self.params.append(BoundParam(v, expected, key, path, owner_type))
            if "_value" in v:
                self.visit(v["_value"], expected, ctx, v, "_value", path + ("_value",), owner_type)
            return
        self._visit_object(v, t, ctx, path, owner_type)

    @staticmethod
    def _is_param(v: dict, t: Optional[str]) -> bool:
        if "_targetVariableID" in v or "_targetSubGraphVariableID" in v:
            return True
        if (t or "").startswith(_PARAM_TYPES) or simple_name(v.get("$type") or "") in _PARAM_TYPES:
            return True
        return "$type" not in v and "_value" in v and set(v) <= {"_value", "_type", "_name"}

    def _visit_object(self, v: dict, t: Optional[str], ctx: Optional[ClassDecl], path: Tuple, owner_type: str) -> None:
        dt = v.get("$type") or t
        if dt and simple_name(dt) in UNITY_VALUE_TYPES:
            return
        decl = self.schema.find(dt) if dt else None
        if decl is None and dt:
            decl = self.schema.find_simple(simple_name(dt).split("<")[0], ctx)
        fields: Dict[str, Tuple[Any, ClassDecl]] = {}
        envs: Dict[str, Dict[str, str]] = {}
        if decl is not None:
            fields = {n: (f, c) for n, f, c in self.schema.ordered_fields(decl.full_name)}
            envs = self._generic_env(decl, v.get("$type") or decl.full_name)
        own_type = v.get("$type") or owner_type
        for k, x in v.items():
            if k in _SKIP_KEYS:
                continue
            if k == "_recoveryState":
                self._recovery(x)
                continue
            if k in NESTED_KEYS and isinstance(x, dict) and isinstance(x.get("_value"), int):
                self.slots[(id(x), "_value")] = (x, "_value")
                continue
            f = fields.get(k)
            if f is not None:
                ftype = self._subst(re.sub(r"\s+", "", f[0].type), envs.get(f[1].full_name))
                self.visit(x, ftype, f[1], v, k, path + (k,), own_type)
            else:
                self.visit(x, None, ctx, v, k, path + (k,), own_type)

    def _recovery(self, text: Any) -> None:
        """A Missing wrapper keeps its original JSON as a string, with object indices of its own."""
        if not isinstance(text, str) or not text:
            return
        try:
            data = json.loads(text)
        except ValueError:
            self.unsafe = True
            return
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, int) and not isinstance(x, bool):
                self.maybe.add(x)

    def variable(self, var: dict) -> None:
        self._recovery(var.get("_recoveryState"))
        if "_value" in var:
            args = generic_args(var.get("$type", ""))
            inner = bare_name(args[0]) if args else None
            self.visit(var["_value"], inner, None, var, "_value", ("_value",), var.get("$type", ""))


def _element(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    m = re.match(r"^(?:List|IList|IEnumerable)<(.+)>$", t)
    if m:
        return m.group(1)
    if t.endswith("[]"):
        return t[:-2]
    if t.startswith(("System.Collections.Generic.List`", "System.Collections.Generic.IList`")):
        args = generic_args(t)
        return bare_name(args[0]) if args else None
    return None


# ---------------------------------------------------------------------------- queries


def node_ref_slots(ws, node: dict) -> List[Tuple[Any, Any]]:
    """``(holder, key)`` of every int in a node or connection that indexes the graph's ``_objectReferences``."""
    w = _Walker(ws)
    w.visit(node, None, None, None, None, (), node.get("$type", ""))
    return list(w.slots.values())


def variable_ref_slots(ws, var: dict) -> List[Tuple[Any, Any]]:
    """The same for the value of a blackboard variable."""
    w = _Walker(ws)
    w.variable(var)
    return list(w.slots.values())


def bound_params(ws, owner: dict) -> List[BoundParam]:
    """Parameters bound to a variable inside a node or connection, with the type each expects."""
    w = _Walker(ws)
    w.visit(owner, None, None, None, None, (), owner.get("$type", ""))
    return w.params


def object_ref_usage(ws, g: Graph) -> Usage:
    w = _Walker(ws)
    for owner in list(g.nodes) + list(g.connections):
        w.visit(owner, None, None, None, None, (), owner.get("$type", ""))
    for var in g.variables.values():
        w.variable(var)
    for k, v in g.data.items():
        if k not in _NO_REFS:
            w.visit(v, None, None, g.data, k, (k,), "")
    return Usage(list(w.slots.values()), w.maybe, w.unsafe)


def stale_refs(ws, g: Graph, usage: Optional[Usage] = None) -> List[int]:
    """Indices of ``_objectReferences`` entries nothing in the graph can be using."""
    usage = usage or object_ref_usage(ws, g)
    if usage.unsafe:
        return []
    used = {h[k] for h, k in usage.slots} | usage.maybe
    return [i for i, r in enumerate(g.refs) if not r.is_null and i not in used]


def prune_refs(ws, g: Graph) -> List[ObjectRef]:
    """Drop ``_objectReferences`` entries nothing uses, as NodeCanvas does when it saves; returns them.

    Indices are renumbered only when every use is known. With uses in fields of unknown types the dropped entries
    become nulls instead, so that no index can end up pointing at another object.
    """
    usage = object_ref_usage(ws, g)
    stale = stale_refs(ws, g, usage)
    if not stale:
        return []
    removed = [g.refs[i] for i in stale]
    if usage.maybe:
        for i in stale:
            g.refs[i] = ObjectRef(0)
        while g.refs and g.refs[-1].is_null:
            g.refs.pop()
        return removed
    used = {h[k] for h, k in usage.slots}
    # index 0 stays: FullSerializer omits a field holding 0, so it has to mean "no object"
    keep = [i for i in range(len(g.refs)) if i == 0 or i in used]
    remap = {old: new for new, old in enumerate(keep)}
    new_refs = [g.refs[i] for i in keep]
    for h, k in usage.slots:
        h[k] = remap.get(h[k], h[k])
    g.refs[:] = [] if len(new_refs) == 1 and new_refs[0].is_null else new_refs
    return removed
