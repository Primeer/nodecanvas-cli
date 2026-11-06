"""How NodeCanvas passes a value of one type where another is expected.

``relation`` mirrors ``ParadoxNotion.TypeConverter.Get``, which binds blackboard variables to parameters and
subgraph mappings. It never compares short names: two classes called ``Pawn`` are different types. Being
convertible is not the same as working: between two components NodeCanvas calls ``GetComponent`` on the same
GameObject, so two unrelated ``Pawn`` components "convert" to null, and Unity reports nothing. ``severity`` says
how much each case matters; it is the only place that decides this.
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple, Optional, Set

from .csharp import ClassDecl
from .schema import bare_name, generic_args, simple_name

ERROR, WARNING, INFO = "error", "warning", "info"

_RANK = {
    "System.SByte": 1,
    "System.Byte": 1,
    "System.Int16": 2,
    "System.UInt16": 2,
    "System.Int32": 3,
    "System.UInt32": 3,
    "System.Int64": 4,
    "System.UInt64": 4,
    "System.Single": 5,
    "System.Double": 6,
    "System.Decimal": 6,
}
CONVERTIBLE = {*_RANK, "System.Boolean", "System.Char", "System.String", "System.DateTime"}
UNITY_VALUE_TYPES = {
    "Vector2",
    "Vector3",
    "Vector4",
    "Quaternion",
    "Color",
    "Color32",
    "Rect",
    "Bounds",
    "LayerMask",
    "Vector2Int",
    "Vector3Int",
    "Ray",
    "RaycastHit",
    "RaycastHit2D",
    "ContactPoint",
}
UNITY_ENUMS = {"Space", "KeyCode", "ForceMode", "ForceMode2D", "QueryTriggerInteraction"}
UNITY_COMPONENTS = {
    "Component",
    "Behaviour",
    "MonoBehaviour",
    "Transform",
    "RectTransform",
    "Collider",
    "BoxCollider",
    "SphereCollider",
    "CapsuleCollider",
    "MeshCollider",
    "CharacterController",
    "Rigidbody",
    "Rigidbody2D",
    "Collider2D",
    "BoxCollider2D",
    "CircleCollider2D",
    "CapsuleCollider2D",
    "PolygonCollider2D",
    "EdgeCollider2D",
    "Camera",
    "Light",
    "Renderer",
    "MeshRenderer",
    "SkinnedMeshRenderer",
    "SpriteRenderer",
    "LineRenderer",
    "TrailRenderer",
    "MeshFilter",
    "Animator",
    "Animation",
    "AudioSource",
    "AudioListener",
    "ParticleSystem",
    "Canvas",
    "CanvasGroup",
    "CanvasRenderer",
    "Terrain",
    "VideoPlayer",
    "Joint",
    "HingeJoint",
    "FixedJoint",
    "SpringJoint",
    "ConfigurableJoint",
    "TextMesh",
}
UNITY_ASSETS = {
    "Object",
    "ScriptableObject",
    "Sprite",
    "Texture",
    "Texture2D",
    "Material",
    "Mesh",
    "AudioClip",
    "AnimationClip",
    "TextAsset",
    "Font",
    "RuntimeAnimatorController",
    "AnimatorOverrideController",
    "Avatar",
    "AvatarMask",
    "PhysicMaterial",
    "PhysicsMaterial",
    "TerrainData",
    "VideoClip",
    "Shader",
}


class Rel(Enum):
    SAME = "same"
    ASSIGNABLE = "assignable"  # subclass, implemented interface, object
    WIDENS = "widens"  # int -> float, anything -> string: nothing is lost that matters
    CONVERTS = "converts"  # NodeCanvas converts, the meaning may change (bool <-> float, Vector3 -> Vector2)
    LOOKUP = "lookup"  # GetComponent / .transform.position: the usual way to pass a GameObject around
    UNRELATED = "unrelated"  # GetComponent between two components of the project with no relation
    NONE = "none"  # no conversion at all: the parameter stays unbound
    UNKNOWN = "unknown"  # a type outside the sources: nothing can be said


class _Kind(NamedTuple):
    kind: str  # object / convertible / value / gameobject / component / asset / interface / class / unknown
    decl: Optional[ClassDecl] = None
    known: frozenset = frozenset()  # full names of the type and its bases in the sources
    external: frozenset = frozenset()  # Unity bases outside the sources, expanded (MonoBehaviour -> Object)
    uncertain: bool = False  # has a base class outside the sources, which may make it a component


def _unity_up(name: str) -> Set[str]:
    if name == "MonoBehaviour":
        return {name, "Behaviour", "Component", "Object"}
    if name == "Behaviour":
        return {name, "Component", "Object"}
    if name in UNITY_COMPONENTS:
        return {name, "Component", "Object"}
    if name in UNITY_ASSETS or name == "GameObject":
        return {name, "Object"}
    return {name}


def _classify(schema, t: str) -> _Kind:
    if t in ("System.Object", "object"):
        return _Kind("object")
    if t in CONVERTIBLE:
        return _Kind("convertible")
    if t.startswith("UnityEngine."):
        name = t[len("UnityEngine.") :]
        if name == "GameObject":
            return _Kind("gameobject", external=frozenset(_unity_up(name)))
        if name in UNITY_COMPONENTS:
            return _Kind("component", external=frozenset(_unity_up(name)))
        if name in UNITY_ASSETS:
            return _Kind("asset", external=frozenset(_unity_up(name)))
        if name in UNITY_VALUE_TYPES:
            return _Kind("value")
        if name in UNITY_ENUMS:
            return _Kind("convertible")
    decl = schema.find(t)
    if decl is None:
        return _Kind("unknown")
    if decl.kind == "enum":
        return _Kind("convertible", decl)
    known, external = schema.ancestors(decl)
    unity = {e for e in external if e in UNITY_COMPONENTS or e in UNITY_ASSETS}
    up = frozenset(n for e in unity for n in _unity_up(e))
    other = {e for e in external - unity if not (e[:1] == "I" and e[1:2].isupper()) and e not in ("object", "Object")}
    if decl.kind == "interface":
        kind = "interface"
    elif unity & UNITY_COMPONENTS:
        kind = "component"
    elif unity:
        kind = "asset"
    else:
        kind = "class"
    return _Kind(kind, decl, frozenset(known), up, bool(other))


def _is_composite(t: str) -> bool:
    return "`" in t or t.endswith("[]") or "<" in t


def relation(schema, src: str, dst: str) -> Rel:
    """What NodeCanvas does with a value of type ``src`` (a CLR name) where ``dst`` is expected."""
    a, b = bare_name(src), bare_name(dst)
    if a == b:
        return Rel.SAME
    ka, kb = _classify(schema, a), _classify(schema, b)
    if kb.kind == "object":
        return Rel.ASSIGNABLE
    if b == "System.String":
        return Rel.WIDENS
    if _is_composite(a) or _is_composite(b):
        return _relation_composite(schema, a, b)
    if ka.kind == "unknown" or kb.kind == "unknown":
        return Rel.UNKNOWN
    if kb.decl is not None and kb.decl.full_name in ka.known:
        return Rel.ASSIGNABLE
    if kb.decl is None and simple_name(b) in ka.external:
        return Rel.ASSIGNABLE
    return _relation_convert(a, b, ka, kb)


def _relation_composite(schema, a: str, b: str) -> Rel:
    """Lists and other generics are invariant: ``List<A>`` and ``List<B>`` have nothing in common."""
    if "`" in a and "`" in b and a.split("[")[0] == b.split("[")[0]:
        pairs = list(zip(generic_args(a), generic_args(b)))
        known = all(_classify(schema, bare_name(x)).kind != "unknown" for pair in pairs for x in pair)
        if known and any(bare_name(x) != bare_name(y) for x, y in pairs):
            return Rel.NONE
    return Rel.UNKNOWN


def _relation_convert(a: str, b: str, ka: _Kind, kb: _Kind) -> Rel:
    if ka.kind == "convertible" and kb.kind == "convertible":
        return Rel.WIDENS if _RANK.get(a, 99) < _RANK.get(b, 0) else Rel.CONVERTS
    scene_obj = ka.kind in ("gameobject", "component")
    if b == "System.Boolean" and (scene_obj or ka.kind == "asset"):
        return Rel.CONVERTS
    if scene_obj and b in ("UnityEngine.Vector3", "UnityEngine.Quaternion"):
        return Rel.LOOKUP
    if ka.kind == "gameobject" and kb.kind == "component":
        return Rel.LOOKUP
    if ka.kind == "component" and kb.kind == "gameobject":
        return Rel.LOOKUP
    if scene_obj and kb.kind == "interface":
        return Rel.LOOKUP
    both_project = ka.decl is not None and kb.decl is not None
    if ka.kind == "component" and kb.kind == "component":
        related = both_project and ka.decl.full_name in kb.known
        return Rel.UNRELATED if both_project and not related else Rel.LOOKUP
    vectors = {"UnityEngine.Vector2", "UnityEngine.Vector3", "UnityEngine.Quaternion"}
    if a in vectors and b in vectors and "UnityEngine.Vector3" in (a, b):
        return Rel.CONVERTS
    if ka.uncertain or kb.uncertain:  # a base outside the sources may make it a component
        may_be_component = {"component", "class"}
        if both_project and ka.kind in may_be_component and kb.kind in may_be_component:
            certainly_not = (ka.kind == "class" and not ka.uncertain) or (kb.kind == "class" and not kb.uncertain)
            return Rel.NONE if certainly_not else Rel.UNRELATED
        return Rel.UNKNOWN
    if ka.kind == "asset" and kb.kind == "asset" and not both_project:
        return Rel.UNKNOWN  # Unity's own asset hierarchy (Texture2D -> Texture) is not modelled
    return Rel.NONE


def severity(rel: Rel, src: str, dst: str) -> Optional[str]:
    """How much a binding with this relation matters; ``None``: not worth mentioning."""
    if rel is Rel.NONE:
        return ERROR
    if rel is Rel.UNRELATED:
        return ERROR if simple_name(bare_name(src)) == simple_name(bare_name(dst)) else WARNING
    if rel is Rel.CONVERTS:
        return WARNING
    if rel is Rel.LOOKUP:
        return INFO
    return None


REASONS = {
    Rel.NONE: "no conversion exists, the value never gets through",
    Rel.UNRELATED: "unrelated components, NodeCanvas would call GetComponent and get null "
    "unless the GameObject has both",
    Rel.CONVERTS: "NodeCanvas converts the value, its meaning may change",
    Rel.LOOKUP: "NodeCanvas converts through the GameObject (GetComponent, transform.position)",
}


def is_assignable(schema, src: Optional[str], dst: str) -> bool:
    """A variable of ``src`` can be passed as ``dst`` as it is: the same type or a subclass."""
    return bool(src) and relation(schema, src, dst) in (Rel.SAME, Rel.ASSIGNABLE)
