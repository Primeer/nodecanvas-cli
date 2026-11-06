"""Type schema built lazily from the project's C# sources.

NodeCanvas ships as source, so the project itself describes every task and node type:
serialized fields, their defaults, enums, display names. Files are located through a cheap
"declared type names" index and fully scanned only when a type is actually needed.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .csharp import ClassDecl, FieldDecl, scan
from .project import Cache, Project

DECL_FORMAT = 7
_DECL_NAMES_RE = re.compile(rb"\b(?:class|struct|enum|interface|record)\s+@?(\w+)")
_GENERIC_ARGS_RE = re.compile(r"`(\d+)\[\[.*\]\]$")

# Printed by their short name even if the project declares a type with the same name.
_NEVER_FULL = {
    f"System.{n}" for n in ("Boolean", "Single", "Double", "Int32", "Int64", "String", "Byte", "Char", "UInt32")
}
# Roots of the NodeCanvas type hierarchy, by simple name.
ROLE_ROOTS = {
    "ActionTask": "action",
    "ConditionTask": "condition",
    "BTComposite": "composite",
    "BTDecorator": "decorator",
    "BTNode": "bt-node",
    "FSMState": "fsm-state",
    "FSMNode": "fsm-node",
    "DTNode": "dt-node",
    "Connection": "connection",
    "Graph": "graph",
    "Task": "task",
    "Node": "node",
}


def _type_key(type_name: str) -> str:
    """``Ns.SetVariable`1[[T, Asm]]`` -> ``Ns.SetVariable`1``; drops assembly qualifiers."""
    t = type_name.strip()
    m = _GENERIC_ARGS_RE.search(t)
    if m:
        t = t[: m.start()] + f"`{m.group(1)}"
    return t


def generic_args(type_name: str) -> List[str]:
    """``List`1[[Ns.Pawn, Asm, ...]]`` -> ``["Ns.Pawn"]`` (assembly qualifiers dropped, nesting kept)."""
    i = type_name.find("[")
    if i < 0 or "`" not in type_name[:i]:
        return []
    body = type_name[i + 1 : type_name.rfind("]")]
    args, depth, cur = [], 0, []
    for ch in body:
        if ch == "[":
            depth += 1
            if depth == 1:
                cur = []
                continue
        elif ch == "]":
            depth -= 1
            if depth == 0:
                args.append(_strip_assembly("".join(cur)))
                continue
        if depth >= 1:
            cur.append(ch)
    return args


def _strip_assembly(qualified: str) -> str:
    depth = 0
    for k, ch in enumerate(qualified):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            return qualified[:k].strip()
    return qualified.strip()


def bare_name(t: str) -> str:
    """Full type name without assembly qualifiers, also inside generic arguments."""
    args = generic_args(t)
    if not args:
        return _strip_assembly(t)
    return t[: t.find("[")] + "[" + ",".join(f"[{bare_name(a)}]" for a in args) + "]"


def generic_text_args(text: str) -> List[str]:
    """``Foo<A, List<B>>`` -> ``["A", "List<B>"]`` (C# syntax, top-level commas only)."""
    if "<" not in text or not text.rstrip().endswith(">"):
        return []
    body = text[text.index("<") + 1 : text.rindex(">")]
    out, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "<"
        depth -= ch == ">"
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [x for x in out if x]


def simple_name(type_name: str) -> str:
    t = _type_key(type_name)
    t = t.split("`")[0]
    return re.split(r"[.+]", t)[-1]


def short_generic(type_name: str) -> str:
    """Readable form: ``SetVariable<Pawn>``."""
    base = simple_name(type_name)
    args = generic_args(type_name)
    return f"{base}<{', '.join(short_generic(a) for a in args)}>" if args else base


class Schema:
    def __init__(self, project: Project):
        self.project = project
        self._names: Optional[Dict[str, List[str]]] = None
        self._scanned: Dict[str, List[ClassDecl]] = {}
        self._by_full: Dict[str, ClassDecl] = {}
        self._by_simple: Dict[str, List[ClassDecl]] = {}
        self._decl_cache = Cache(project.root, f"csharp-decls-v{DECL_FORMAT}")
        self._builtin_full: Dict[str, ClassDecl] = {}
        self._builtin_simple: Dict[str, List[ClassDecl]] = {}
        from .builtin import BUILTIN_FILE, STUBS

        for d in scan(STUBS, BUILTIN_FILE):
            self._builtin_full[d.full_name] = d
            self._builtin_simple.setdefault(d.name, []).append(d)
        self._names_cache = Cache(project.root, "csharp-names")

    @property
    def has_nodecanvas(self) -> bool:
        """Whether NodeCanvas itself is among the scanned sources (built-in declarations do not count)."""
        if not hasattr(self, "_has_nc"):
            self._load_source_simple("Node")
            self._has_nc = "NodeCanvas.Framework.Node" in self._by_full
        return self._has_nc

    def close(self) -> None:
        self._decl_cache.save()
        self._names_cache.save()

    def _source_files(self) -> Iterable[Path]:
        yield from self.project.iter_files((".cs",))
        cache_dir = self.project.root / "Library" / "PackageCache"
        if cache_dir.is_dir():
            for d in cache_dir.iterdir():
                low = d.name.lower()
                if "nodecanvas" in low or "paradoxnotion" in low:
                    for p in d.rglob("*.cs"):
                        yield p

    @property
    def names(self) -> Dict[str, List[str]]:
        if self._names is None:
            names: Dict[str, List[str]] = {}
            seen = []
            for p in self._source_files():
                key = self.project.rel(p)
                seen.append(key)
                st = p.stat()
                found = self._names_cache.get(key, st)
                if found is None:
                    try:
                        found = sorted({m.group(1).decode() for m in _DECL_NAMES_RE.finditer(p.read_bytes())})
                    except OSError:
                        found = []
                    self._names_cache.put(key, st, found)
                for n in found:
                    names.setdefault(n, []).append(key)
            self._names_cache.prune(seen)
            self._names = names
        return self._names

    def _scan_file(self, rel: str) -> List[ClassDecl]:
        if rel in self._scanned:
            return self._scanned[rel]
        p = self.project.abs(rel)
        decls: List[ClassDecl] = []
        try:
            st = p.stat()
            cached = self._decl_cache.get(rel, st)
            if cached is not None:
                decls = [_decl_from_dict(d) for d in cached]
            else:
                decls = scan(p.read_text(encoding="utf-8-sig", errors="replace"), rel)
                self._decl_cache.put(rel, st, [asdict(d) for d in decls])
        except OSError:
            pass
        self._scanned[rel] = decls
        for d in decls:
            self._register(d)
        return decls

    def _register(self, d: ClassDecl) -> None:
        existing = self._by_full.get(d.full_name)
        if existing is None:
            merged = ClassDecl(
                **{
                    **d.__dict__,
                    "fields": list(d.fields),
                    "properties": dict(d.properties),
                    "methods": {k: [dict(x) for x in v] for k, v in d.methods.items()},
                    "bases": list(d.bases),
                    "attrs": dict(d.attrs),
                    "usings": list(d.usings),
                    "setters": list(d.setters),
                    "type_params": list(d.type_params),
                }
            )
            self._by_full[d.full_name] = merged
            self._by_simple.setdefault(d.name, []).append(merged)
            return
        existing.fields += [f for f in d.fields if all(f.name != g.name for g in existing.fields)]
        existing.properties.update(d.properties)
        existing.setters += [p for p in d.setters if p not in existing.setters]
        for k, v in d.methods.items():
            existing.methods.setdefault(k, []).extend(v)
        existing.bases += [b for b in d.bases if b not in existing.bases]
        existing.attrs.update({k: v for k, v in d.attrs.items() if k not in existing.attrs})
        existing.usings += [u for u in d.usings if u not in existing.usings]
        existing.is_abstract = existing.is_abstract or d.is_abstract
        existing.enum_members = existing.enum_members or d.enum_members

    def _load_source_simple(self, name: str) -> List[ClassDecl]:
        for rel in self.names.get(name, []):
            self._scan_file(rel)
        return self._by_simple.get(name, [])

    def _load_simple(self, name: str) -> List[ClassDecl]:
        """Declarations named ``name``: project sources first, built-in declarations as a fallback."""
        found = self._load_source_simple(name)
        return found if found else self._builtin_simple.get(name, [])

    def find(self, type_name: str) -> Optional[ClassDecl]:
        """Resolve a FullSerializer ``$type`` (or a plain C# type name) to its declaration."""
        key = _type_key(type_name)
        simple = simple_name(type_name)
        cands = self._load_simple(simple)
        if key in self._by_full:
            return self._by_full[key]
        if key in self._builtin_full:
            return self._builtin_full[key]
        arity = int(key.split("`")[1]) if "`" in key else None
        if "." in key or "+" in key:
            return None
        cands = [c for c in cands if arity is None or c.arity == arity]
        return cands[0] if len(cands) == 1 else None

    def lookup(self, text: str, arity: Optional[int] = None) -> List[ClassDecl]:
        """Every declaration a name typed by a person can mean: ``Pawn``, ``Pawns.Pawn``, ``Ns.Outer.Inner``,
        ``Ns.Outer+Inner``. A qualifier must match whole namespace parts; an exact full name wins."""
        t = text.strip().replace("+", ".")
        if "`" in t:
            t, _, n = t.partition("`")
            arity = int(n.split("[")[0]) if arity is None else arity

        def dotted(c: ClassDecl) -> str:
            return c.full_name.split("`")[0].replace("+", ".")

        simple = t.rsplit(".", 1)[-1]
        for pool in (self._load_simple(simple), self._builtin_simple.get(simple, [])):
            hits = [
                c
                for c in pool
                if (arity is None or c.arity == arity) and (dotted(c) == t or dotted(c).endswith("." + t))
            ]
            if hits:
                exact = [c for c in hits if dotted(c) == t]
                return exact or hits
        return []

    def visible(self, decl: ClassDecl, context: ClassDecl) -> bool:
        """Whether code inside ``context`` can name ``decl`` without a qualifier."""
        ns = context.namespace
        while ns:
            if decl.namespace == ns:
                return True
            ns = ns.rsplit(".", 1)[0] if "." in ns else ""
        return not decl.namespace or decl.namespace in context.usings

    def find_simple(
        self, name: str, context: Optional[ClassDecl] = None, arity: Optional[int] = None
    ) -> Optional[ClassDecl]:
        """The declaration C# code inside ``context`` means by ``name``: nested types first, then the namespaces
        from the innermost outwards, then usings. Without a context, or when none of these match, the first
        declaration of that name: names typed by a person go through ``lookup`` and ``TypeNames.user`` instead."""
        cands = [c for c in self._load_simple(name) if arity is None or c.arity == arity]
        if not cands:
            cands = [c for c in self._builtin_simple.get(name, []) if arity is None or c.arity == arity]
        if not cands:
            return None
        if context is not None:
            nested = [
                c
                for c in cands
                if c.namespace == context.namespace
                and c.outer[: len(context.outer) + 1] == context.outer + [context.name]
            ]
            if nested:
                return nested[0]
            ns = context.namespace
            while ns:
                here = [c for c in cands if c.namespace == ns]
                if here:
                    return here[0]
                ns = ns.rsplit(".", 1)[0] if "." in ns else ""
            in_scope = [c for c in cands if c.namespace in context.usings]
            if in_scope:
                return in_scope[0]
        return cands[0]

    def resolve_base(self, decl: ClassDecl, base: str) -> Optional[ClassDecl]:
        """The declaration of one entry of ``decl``'s base list; ``None`` when it is outside the sources."""
        from .builtin import BUILTIN_FILE

        bare = re.sub(r"<.*>", "", base).strip()
        name = bare.split(".")[-1]
        arity = len(generic_text_args(base))
        if "." in bare:
            hits = self.lookup(bare, arity)
            if len(hits) == 1:
                return hits[0]
        if decl.file == BUILTIN_FILE:
            found = next((c for c in self._builtin_simple.get(name, []) if c.arity == arity), None)
            if found is not None:
                return found
        return self.find_simple(name, decl, arity)

    def base_of(self, decl: ClassDecl) -> Optional[ClassDecl]:
        for b in decl.bases:
            found = self.resolve_base(decl, b)
            if found is not None and found.kind in ("class", "record", "struct"):
                return found
        return None

    def ancestors(self, decl: ClassDecl) -> Tuple[Set[str], Set[str]]:
        """Full names of ``decl``, its base classes and interfaces in the sources, and the short names of the
        bases outside the sources (``MonoBehaviour``, a package's base class)."""
        known: Set[str] = set()
        external: Set[str] = set()
        stack = [decl]
        while stack:
            c = stack.pop()
            if c.full_name in known:
                continue
            known.add(c.full_name)
            for b in c.bases:
                found = self.resolve_base(c, b)
                if found is not None:
                    stack.append(found)
                else:
                    external.add(re.sub(r"<.*>", "", b).strip().split(".")[-1])
        return known, external

    def display(self, type_name: str) -> str:
        """A readable name that does not mislead: ``SetVariable<Pawn>``, with the full name of every part
        whose short name is shared by several types of the project."""
        t = _strip_assembly(type_name) if "[" not in type_name.split("`")[0] else type_name
        key = _type_key(t)
        args = generic_args(t)
        base = simple_name(key)
        arity = int(key.split("`")[1]) if "`" in key else 0
        if key not in _NEVER_FULL:
            cands = [c for c in self._load_source_simple(base) if c.arity == arity]
            outside = key.startswith(("System.", "UnityEngine.")) and any(c.full_name != key for c in cands)
            if len(cands) > 1 or outside:
                base = key.split("`")[0]
        return f"{base}<{', '.join(self.display(a) for a in args)}>" if args else base

    def display_pair(self, a: str, b: str) -> Tuple[str, str]:
        """``display`` of two types that are about to be compared in a message: full names when these look alike."""
        da, db = self.display(a), self.display(b)
        return (bare_name(a), bare_name(b)) if da == db else (da, db)

    def chain(self, decl: ClassDecl) -> List[ClassDecl]:
        out, cur, guard = [], decl, 0
        while cur is not None and guard < 32:
            out.append(cur)
            cur = self.base_of(cur)
            guard += 1
        return out

    def role(self, type_name: str) -> Optional[str]:
        decl = self.find(type_name)
        if decl is None:
            return None
        for c in self.chain(decl):
            if c.name in ROLE_ROOTS and c.namespace.startswith(("NodeCanvas", "ParadoxNotion")):
                return ROLE_ROOTS[c.name]
        return None

    def fields(self, type_name: str) -> Dict[str, FieldDecl]:
        decl = self.find(type_name)
        out: Dict[str, FieldDecl] = {}
        if decl is None:
            return out
        for c in reversed(self.chain(decl)):
            for f in c.fields:
                if f.serialized:
                    out[f.serialized_name] = f
        return out

    def ordered_fields(self, type_name: str):
        """Serialized fields in FullSerializer order: most derived class first, declaration order."""
        decl = self.find(type_name)
        if decl is None:
            return []
        out, seen = [], set()
        for c in self.chain(decl):
            for f in c.fields:
                if f.serialized and f.serialized_name not in seen:
                    seen.add(f.serialized_name)
                    out.append((f.serialized_name, f, c))
        return out

    def field_owner(self, type_name: str, field_name: str) -> Optional[ClassDecl]:
        decl = self.find(type_name)
        if decl is None:
            return None
        for c in self.chain(decl):
            if any(f.serialized_name == field_name for f in c.fields):
                return c
        return None

    def enum_for(self, field_type: str, context: Optional[ClassDecl]) -> Optional[ClassDecl]:
        name = re.sub(r"\?$", "", field_type).split(".")[-1]
        if not re.match(r"^\w+$", name):
            return None
        ctxs = self.chain(context) if context else [None]
        for ctx in ctxs:
            d = self.find_simple(name, ctx)
            if d is not None and d.kind == "enum":
                return d
        return None

    def member_kinds(self, type_name: str, member: str) -> Optional[set]:
        """Which member kinds named ``member`` exist on the type or its source bases.

        Returns ``None`` when the type is not found in sources. Accessors ``get_X``/``set_X``
        resolve to properties.
        """
        decl = self.find(type_name)
        if decl is None:
            return None
        kinds = set()
        prop = member[4:] if member.startswith(("get_", "set_")) else None
        for c in self.chain(decl):
            if member in c.methods:
                kinds.add("method")
            if prop and prop in c.properties:
                kinds.add("property")
            if prop and any(f.name == prop for f in c.fields):
                kinds.add("field")
            if any(f.name == member for f in c.fields):
                kinds.add("field")
            if member in c.properties:
                kinds.add("property")
        return kinds

    def overloads(self, type_name: str, member: str, arity: Optional[int] = None) -> List[dict]:
        """Method signatures named ``member`` on the type and its source bases (``arity`` filters by count)."""
        decl = self.find(type_name)
        if decl is None:
            return []
        out = []
        for c in self.chain(decl):
            for o in c.methods.get(member, []):
                if arity is None or len(o["params"]) == arity:
                    out.append(o)
        return out

    def resolves(self, type_name: str, by_name: bool = False) -> bool:
        """Whether a (possibly generic, assembly-qualified) CLR type name can be found now.

        ``System.*`` / ``UnityEngine.*`` / ``Unity.*`` types are assumed to exist. With ``by_name``,
        a type missing under its full name also counts when its short name exists, as NodeCanvas
        resolves it on load (see ``renamed``).
        """
        return self._resolve(type_name, by_name, [])

    def renamed(self, type_name: str) -> Optional[List[Tuple[str, List[ClassDecl]]]]:
        """Parts of ``type_name`` that only resolve by short name: ``[(saved name, candidates)]``.

        NodeCanvas (``ReflectionTools.GetType``) falls back to a lookup that ignores the namespace
        when a serialized type name is not found, so a type whose namespace changed still loads.
        ``None`` when the name does not resolve even that way.
        """
        out: List[Tuple[str, List[ClassDecl]]] = []
        return out if self._resolve(type_name, True, out) else None

    def current_name(self, type_name: str) -> str:
        """``type_name`` with renamed parts replaced by the type NodeCanvas finds (when unambiguous)."""
        for old, cands in self.renamed(type_name) or []:
            if len(cands) == 1:
                type_name = type_name.replace(old, cands[0].full_name)
        return type_name

    def _resolve(self, type_name: str, by_name: bool, renamed: list) -> bool:
        base = _strip_assembly(type_name) if "[" not in type_name.split("`")[0] else type_name
        key = _type_key(base)
        if not key.startswith(("System.", "UnityEngine.", "Unity.")):
            if key.startswith(("NodeCanvas.", "ParadoxNotion.")) and not self.has_nodecanvas:
                pass
            elif self.find(key) is None:
                if not by_name:
                    return False
                arity = int(key.split("`")[1]) if "`" in key else 0
                cands = [c for c in self._load_simple(simple_name(key)) if c.arity == arity]
                if not cands:
                    return False
                renamed.append((key, cands))
        return all(self._resolve(a, by_name, renamed) for a in generic_args(type_name))

    def const_prop(self, type_name: str, prop: str) -> Optional[str]:
        decl = self.find(type_name)
        if decl is None:
            return None
        for c in self.chain(decl):
            if prop in c.const_props:
                return c.const_props[prop]
            if prop in c.properties:
                return None
        return None

    def chain_ends_in_unity(self, type_name: str) -> bool:
        """The source chain ends at a UnityEngine base whose public members nodecanvas-cli knows."""
        from .builtin import UNITY_BASES

        decl = self.find(type_name)
        if decl is None:
            return False
        last = self.chain(decl)[-1]
        return any(re.sub(r"<.*>", "", b).strip().split(".")[-1] in UNITY_BASES for b in last.bases)

    def chain_is_complete(self, type_name: str) -> bool:
        decl = self.find(type_name)
        if decl is None:
            return False
        last = self.chain(decl)[-1]
        return not any(
            re.sub(r"<.*>", "", b).strip() not in ("object", "System.Object") and not b.strip().startswith("I")
            for b in last.bases
        )

    def all_of_role(self, roles: Iterable[str]) -> List[ClassDecl]:
        roles = set(roles)
        hints = ("Task", "Node", "BTComposite", "BTDecorator", "FSMState", "DTNode", "Nested")
        cache = Cache(self.project.root, "csharp-hints")
        out, seen = [], []
        for p in self._source_files():
            rel = self.project.rel(p)
            seen.append(rel)
            st = p.stat()
            has = cache.get(rel, st)
            if has is None:
                try:
                    text = p.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                has = any(h in text for h in hints)
                cache.put(rel, st, has)
            if not has:
                continue
            for d in self._scan_file(rel):
                if d.kind == "class" and not d.is_abstract:
                    full = self._by_full.get(d.full_name, d)
                    if self.role(full.full_name) in roles and all(x is not full for x in out):
                        out.append(full)
        cache.prune(seen)
        cache.save()
        known = {d.full_name for d in out}
        out += [
            d
            for d in self._builtin_full.values()
            if d.full_name not in known and d.kind == "class" and not d.is_abstract and self.role(d.full_name) in roles
        ]
        return out


def _decl_from_dict(d: dict) -> ClassDecl:
    d = dict(d)
    fields = [FieldDecl(**f) for f in d.pop("fields")]
    d["enum_members"] = [tuple(x) for x in d.get("enum_members", [])]
    return ClassDecl(fields=fields, **d)
