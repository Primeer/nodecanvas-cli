"""C# type text -> CLR names as FullSerializer writes them.

``float`` -> ``System.Single``; ``List<Pawn>`` -> ``System.Collections.Generic.List`1[[Ns.Pawn, Asm, Version=…]]``.
Assembly-qualified forms are harvested from existing graphs first, then derived from ``.asmdef`` files.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .csharp import ClassDecl
from .model import walk

ALIASES = {
    "bool": "System.Boolean",
    "float": "System.Single",
    "double": "System.Double",
    "int": "System.Int32",
    "long": "System.Int64",
    "short": "System.Int16",
    "byte": "System.Byte",
    "sbyte": "System.SByte",
    "uint": "System.UInt32",
    "ulong": "System.UInt64",
    "ushort": "System.UInt16",
    "char": "System.Char",
    "string": "System.String",
    "object": "System.Object",
    "void": "System.Void",
    "decimal": "System.Decimal",
}
SYSTEM_SIMPLE = {v.split(".")[-1]: v for v in ALIASES.values()}
UNITY_CORE = {
    "Vector2",
    "Vector3",
    "Vector4",
    "Vector2Int",
    "Vector3Int",
    "Quaternion",
    "Color",
    "Color32",
    "Rect",
    "Bounds",
    "LayerMask",
    "GameObject",
    "Transform",
    "Component",
    "Behaviour",
    "MonoBehaviour",
    "ScriptableObject",
    "Object",
    "Sprite",
    "Texture",
    "Texture2D",
    "Material",
    "Mesh",
    "AnimationCurve",
    "Gradient",
    "Camera",
    "Light",
    "Renderer",
    "Collider",
    "Rigidbody",
    "AudioClip",
    "AudioSource",
    "Animator",
    "AnimationClip",
    "TextAsset",
    "Space",
    "KeyCode",
    "Ray",
    "RaycastHit",
}
# UnityEngine types outside UnityEngine.CoreModule (Unity 2017.2+ module split); Unity rewrites any other
# assembly name on the next save, so writing the right one keeps diffs clean
UNITY_MODULES = {
    **dict.fromkeys(("AudioClip", "AudioSource", "AudioListener", "AudioReverbZone"), "UnityEngine.AudioModule"),
    **dict.fromkeys(
        (
            "Rigidbody",
            "Collider",
            "BoxCollider",
            "SphereCollider",
            "CapsuleCollider",
            "MeshCollider",
            "CharacterController",
            "RaycastHit",
            "PhysicMaterial",
            "PhysicsMaterial",
            "ForceMode",
            "QueryTriggerInteraction",
            "Collision",
            "ContactPoint",
            "Joint",
            "HingeJoint",
            "FixedJoint",
            "SpringJoint",
            "ConfigurableJoint",
        ),
        "UnityEngine.PhysicsModule",
    ),
    **dict.fromkeys(
        (
            "Rigidbody2D",
            "Collider2D",
            "BoxCollider2D",
            "CircleCollider2D",
            "CapsuleCollider2D",
            "PolygonCollider2D",
            "EdgeCollider2D",
            "RaycastHit2D",
            "Collision2D",
            "ForceMode2D",
            "ContactFilter2D",
        ),
        "UnityEngine.Physics2DModule",
    ),
    **dict.fromkeys(
        (
            "Animator",
            "AnimationClip",
            "RuntimeAnimatorController",
            "AnimatorOverrideController",
            "Avatar",
            "Animation",
            "AnimationState",
            "AvatarMask",
        ),
        "UnityEngine.AnimationModule",
    ),
    "ParticleSystem": "UnityEngine.ParticleSystemModule",
    **dict.fromkeys(("Canvas", "CanvasGroup", "CanvasRenderer"), "UnityEngine.UIModule"),
    **dict.fromkeys(("Font", "TextMesh"), "UnityEngine.TextRenderingModule"),
    **dict.fromkeys(("Terrain", "TerrainData"), "UnityEngine.TerrainModule"),
    **dict.fromkeys(("VideoClip", "VideoPlayer"), "UnityEngine.VideoModule"),
}
GENERIC_SYSTEM = {
    "List": "System.Collections.Generic.List",
    "Dictionary": "System.Collections.Generic.Dictionary",
    "HashSet": "System.Collections.Generic.HashSet",
    "IList": "System.Collections.Generic.IList",
    "IEnumerable": "System.Collections.Generic.IEnumerable",
}
MSCORLIB = "mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089"
_QUALIFIED_RE = re.compile(
    r"\[([\w.+`]+(?:\[\[.*?\]\])?), ([\w.\-]+), Version=[\d.]+, Culture=\w+, PublicKeyToken=\w+\]"
)


class TypeNameError(Exception):
    """A type name typed by a person does not identify exactly one type."""


class TypeNames:
    def __init__(self, ws):
        self.ws = ws
        self._qualified: Optional[Dict[str, str]] = None
        self._asm: Dict[str, Optional[str]] = {}

    @property
    def qualified(self) -> Dict[str, str]:
        if self._qualified is None:
            q: Dict[str, str] = {}
            for g in self.ws.all_graphs():
                for _, d in walk(g.data):
                    for k in ("$type", "_type", "_missingType"):
                        v = d.get(k)
                        if isinstance(v, str) and "[[" in v:
                            for m in _QUALIFIED_RE.finditer(v):
                                full = m.group(0)[1:-1]
                                q.setdefault(m.group(1), full)
            self._qualified = q
        return self._qualified

    def _assembly_of(self, decl: ClassDecl) -> str:
        rel = decl.file
        if rel in self._asm:
            return self._asm[rel] or "Assembly-CSharp"
        root = self.ws.project.root
        p = (root / rel).parent
        name = None
        while True:
            defs = sorted(p.glob("*.asmdef"))
            if defs:
                try:
                    name = json.loads(defs[0].read_text(encoding="utf-8-sig")).get("name")
                except (OSError, ValueError):
                    name = None
                break
            if p == root or p.parent == p or p.name in ("Assets", "Packages"):
                break
            p = p.parent
        if name is None:
            parts = Path(rel).parts
            if "Editor" in parts:
                name = "Assembly-CSharp-Editor-firstpass" if "Plugins" in parts else "Assembly-CSharp-Editor"
            elif "Plugins" in parts or "Standard Assets" in parts:
                name = "Assembly-CSharp-firstpass"
        self._asm[rel] = name
        return name or "Assembly-CSharp"

    def full(self, text: str, ctx: Optional[ClassDecl] = None) -> str:
        """CLR full name (generic arguments assembly-qualified, as FullSerializer writes ``$type``)."""
        t = re.sub(r"\s+", "", text)
        if t in ALIASES:
            return ALIASES[t]
        if t.endswith("[]"):
            return self.full(t[:-2], ctx) + "[]"
        if t.endswith("?"):
            return f"System.Nullable`1[[{self.qualify(t[:-1], ctx)}]]"
        m = re.match(r"^([\w.]+)<(.+)>$", t)
        if m:
            base, args = m.group(1), _split_args(m.group(2))
            base_full = GENERIC_SYSTEM.get(base)
            if base_full is None:
                decl = self.ws.schema.find_simple(base.split(".")[-1], ctx, len(args))
                base_full = decl.full_name.split("`")[0] if decl else base
            return f"{base_full}`{len(args)}[" + ",".join(f"[{self.qualify(a, ctx)}]" for a in args) + "]"
        if "." in t and self.ws.schema.find(t) is not None:
            return self.ws.schema.find(t).full_name
        simple = t.split(".")[-1]
        if simple in SYSTEM_SIMPLE and (t == simple or t.startswith("System.")):
            return SYSTEM_SIMPLE[simple]
        decl = self.ws.schema.find_simple(simple, ctx)
        known_unity = simple in UNITY_CORE or simple in UNITY_MODULES
        if decl is not None and not (known_unity and ctx is not None and not self.ws.schema.visible(decl, ctx)):
            return decl.full_name
        if known_unity:
            return f"UnityEngine.{simple}"
        return t

    def qualify(self, text: str, ctx: Optional[ClassDecl] = None) -> str:
        return self.assembly_qualified(self.full(text, ctx))

    # ------------------------------------------------------------------ names typed by a person

    def user(self, text: str) -> str:
        """CLR full name of a type a person named (``--var``, ``var-add``, ``SetVariable<Pawn>``). Unlike ``full``,
        which reads code whose context settles the meaning, this never guesses: a name that fits several types
        or none is a ``TypeNameError`` listing the candidates."""
        t = re.sub(r"\s+", "", text)
        if t in ALIASES:
            return ALIASES[t]
        if "`" in t:  # already a CLR name (from `node --raw` or a serialized spec)
            return t
        if t.endswith("[]"):
            return self.user(t[:-2]) + "[]"
        if t.endswith("?"):
            return f"System.Nullable`1[[{self.user_qualified(t[:-1])}]]"
        m = re.match(r"^([\w.+]+)<(.+)>$", t)
        if m:
            base, args = m.group(1), _split_args(m.group(2))
            full = GENERIC_SYSTEM.get(base) or self._user_generic(base, len(args))
            return f"{full}`{len(args)}[" + ",".join(f"[{self.user_qualified(a)}]" for a in args) + "]"
        return self._user_leaf(t)

    def user_qualified(self, text: str) -> str:
        return self.assembly_qualified(self.user(text))

    def _user_generic(self, base: str, arity: int) -> str:
        names = [c.full_name.split("`")[0] for c in self.ws.schema.lookup(base, arity)]
        if len(names) > 1:
            raise TypeNameError(_ambiguous(f"{base}<>", names))
        if names:
            return names[0]
        if "." in base:
            return base
        raise TypeNameError(self._not_found(base))

    def _user_leaf(self, t: str) -> str:
        simple = t.split(".")[-1]
        names = [c.full_name for c in self.ws.schema.lookup(t, 0)]
        outside = []
        if simple in SYSTEM_SIMPLE:
            outside.append(SYSTEM_SIMPLE[simple])
        if simple in UNITY_CORE or simple in UNITY_MODULES:
            outside.append(f"UnityEngine.{simple}")
        for full in outside:
            if t == full:
                return full
            if "." not in t and full not in names:
                names.append(full)
        if len(names) == 1:
            return names[0]
        if names:
            raise TypeNameError(_ambiguous(t, names))
        if "." not in t:
            raise TypeNameError(self._not_found(t))
        same = [c.full_name for c in self.ws.schema.lookup(simple, 0)]
        if same:
            raise TypeNameError(f"no type {t!r}; types named {simple}: {', '.join(same)}")
        return t  # a qualified name outside the sources (TMPro.TMP_Text)

    def _not_found(self, name: str) -> str:
        pool = list(self.ws.schema.names) + sorted(UNITY_CORE | set(UNITY_MODULES))
        close = difflib.get_close_matches(name, pool, n=3, cutoff=0.7)
        hint = f"; did you mean {', '.join(close)}?" if close else ""
        return f"type {name!r} not found in the project's sources{hint} (use the full name for a type outside them)"

    def assembly_qualified(self, full: str) -> str:
        """``Ns.Pawn, Asm, Version=…`` for a CLR full name."""
        if full in self.qualified:
            return self.qualified[full]
        if full.startswith("System."):
            return f"{full}, {MSCORLIB}"
        if full.startswith("UnityEngine."):
            module = UNITY_MODULES.get(full[len("UnityEngine.") :], "UnityEngine.CoreModule")
            return f"{full}, {module}, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
        decl = self.ws.schema.find(full)
        asm = self._assembly_of(decl) if decl is not None else "Assembly-CSharp"
        return f"{full}, {asm}, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"


def _ambiguous(name: str, names: List[str]) -> str:
    return f"type {name!r} is ambiguous, use the full name: " + ", ".join(names)


def _split_args(s: str):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out if x.strip()]
