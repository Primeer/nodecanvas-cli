"""Who references a graph asset from outside the graph system: any field of any ScriptableObject, prefab or scene.

A project may hand graphs out through its own data (``BotData.fsm``), so a graph with no GraphOwner and no parent
graph is not necessarily dead. Unity keeps such a reference as ``{fileID: 11400000, guid: …, type: 2}`` in the YAML
of the referencing object (``m_AssetGUID`` for an Addressables reference), so a text scan finds them all.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .container import ObjectRef, _documents, _value_of
from .project import CONTAINER_SUFFIXES, Cache

_GUID_RE = re.compile(rb"[Gg][Uu][Ii][Dd]: ([0-9a-f]{32})")
_FILE_ID_RE = re.compile(r"fileID: (-?\d+)")
# Object lists of graph containers: an entry there is a use only if the graph really uses it.
GRAPH_REF_LISTS = ("_objectReferences", "_boundGraphObjectReferences")
_CACHE_KEY = "\0graph guids"  # what the entries were computed for: another set of graphs means another scan


@dataclass
class AssetRef:
    path: str
    obj: str  # the referencing object: "Guard (BotBrain)", "(BotData)", "PrefabInstance"
    field: str  # dotted path of the field

    @property
    def label(self) -> str:
        return f"{self.path} {self.obj}.{self.field}" if self.obj else f"{self.path} {self.field}"


def yaml_path(lines: List[str], i: int, start: int) -> str:
    """Dotted field path of line ``i`` inside the document starting at ``start``, from Unity's YAML indentation
    (list items sit at the indentation of their key)."""

    def split(text: str):
        item = text.startswith("- ")
        body = text[2:] if item else text
        key = body.split(":", 1)[0].strip() if ":" in body and not body.startswith("{") else None
        return item, key

    text = lines[i].lstrip(" ")
    col = len(lines[i]) - len(text)
    in_item, key = split(text)
    parts = [key] if key else []
    for j in range(i - 1, start, -1):
        text = lines[j].lstrip(" ")
        ind = len(lines[j]) - len(text)
        if not text or ind == 0:
            continue
        item, key = split(text)
        if in_item:  # the key of this list is at the same indentation
            if ind <= col and not item and key:
                parts.append(key)
                col, in_item = ind, False
            continue
        if item and ind + 2 == col:  # a sibling on the list item's own line
            col, in_item = ind, True
        elif ind < col and key:
            parts.append(key)
            col, in_item = ind, item
    return ".".join(reversed(parts))


def _object_of(ws, lines: List[str], doc, go_names: Dict[str, str]) -> str:
    start, end, cls, _ = doc
    if cls == "1001":
        return "PrefabInstance"
    script = _value_of(lines, start, end, "m_Script") or ""
    name = ""
    if "guid" in script:
        path = ws.ref_path(ObjectRef.parse(script))
        name = Path(path).stem if path else ""
    if not name:
        name = (_value_of(lines, start, end, "m_EditorClassIdentifier") or "").split("::")[-1].split(".")[-1]
    m = _FILE_ID_RE.search(_value_of(lines, start, end, "m_GameObject") or "")
    go = go_names.get(m.group(1), "") if m and m.group(1) != "0" else ""
    return f"{go} ({name})" if go and name else go or (f"({name})" if name else "")


def _scan_file(ws, rel: str, text: str, guids: set) -> List[list]:
    lines = text.split("\n")
    docs = _documents(lines)
    go_names = {fid: _value_of(lines, s, e, "m_Name") or "" for s, e, cls, fid in docs if cls == "1"}
    out = []
    for doc in docs:
        start, end, _, _ = doc
        for i in range(start, end):
            m = _GUID_RE.search(lines[i].encode("utf-8", "replace"))
            if m is None or m.group(1).decode() not in guids:
                continue
            guid = m.group(1).decode()
            field = yaml_path(lines, i, start)
            if field.endswith("objectReference"):  # a prefab override names its property on the line above
                prop = _value_of(lines, max(start, i - 2), i, "propertyPath")
                field = prop or field
            out.append([guid, _object_of(ws, lines, doc, go_names), field])
    return out


def graph_refs(ws) -> Dict[str, List[AssetRef]]:
    """``graph guid -> references`` from every ``.asset`` / ``.prefab`` / ``.unity`` (computed once per run)."""
    cached = getattr(ws, "_asset_refs", None)
    if cached is not None:
        return cached
    paths = {ws.project.path_to_guid(s.path): s.path for s in ws.slots if not s.bound}
    paths.pop(None, None)
    guids = set(paths)
    digest = hashlib.sha1(",".join(sorted(guids)).encode()).hexdigest()[:12]
    cache = Cache(ws.project.root, "graph-refs")
    if cache.data.get(_CACHE_KEY) != digest:
        cache.data = {_CACHE_KEY: digest}
        cache.dirty = True
    out: Dict[str, List[AssetRef]] = {}
    seen = []
    needles = [g.encode() for g in guids]
    for f in ws.project.iter_files(CONTAINER_SUFFIXES):
        rel = ws.project.rel(f)
        seen.append(rel)
        st = f.stat()
        hits = cache.get(rel, st)
        if hits is None:
            data = f.read_bytes()
            hits = []
            if any(n in data for n in needles):
                hits = _scan_file(ws, rel, data.decode("utf-8", "replace").replace("\r\n", "\n"), guids)
            cache.put(rel, st, hits)
        for guid, obj, field in hits:
            if paths.get(guid) != rel:  # a graph naming itself is not a use
                out.setdefault(guid, []).append(AssetRef(rel, obj, field))
    cache.prune(seen + [_CACHE_KEY])
    cache.save()
    ws._asset_refs = out
    return out


def _used_in_graphs(ws, ref: AssetRef, guid: str) -> bool:
    """An entry in another graph's object list counts only if that graph uses it for something other than a
    subgraph (which `owners` shows as a parent): a stale entry is no use at all."""
    from .model import NESTED_KEYS
    from .typedwalk import object_ref_usage

    for slot in ws.slots:
        if slot.path != ref.path:
            continue
        g = ws.load(slot)
        if any(
            isinstance(n.get(k), dict) and (g.ref(n[k].get("_value")) or ObjectRef(0)).guid == guid
            for n in g.nodes
            for k in NESTED_KEYS
        ):
            continue  # shown as "subgraph of"
        usage = object_ref_usage(ws, g)
        used = {h[k] for h, k in usage.slots} | usage.maybe
        if usage.unsafe or any(r.guid == guid and i in used for i, r in enumerate(g.refs)):
            return True
    return False


def refs_to(ws, path: str) -> List[AssetRef]:
    """References to the graph asset at ``path`` that `owners` shows nowhere else."""
    from .owners import index

    guid = ws.project.path_to_guid(path)
    idx = index(ws)
    runs_it = {o.path for o in idx.owners if o.graph is not None and o.graph.guid == guid}
    runs_it |= {i.path for i in idx.instances if i.graph is not None and i.graph.guid == guid}
    out = []
    for ref in graph_refs(ws).get(guid, []):
        field = ref.field.split(".")[-1]
        if field == "_graph" and ref.path in runs_it:
            continue
        if field in GRAPH_REF_LISTS and not _used_in_graphs(ws, ref, guid):
            continue
        out.append(ref)
    return out
