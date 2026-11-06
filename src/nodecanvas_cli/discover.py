"""Discovery commands: which task/node types exist, their fields, and members callable by reflection tasks."""

from __future__ import annotations

import json
import re

ROLE_GROUPS = {
    "action": {"action"},
    "condition": {"condition"},
    "bt": {"bt-node", "composite", "decorator"},
    "composite": {"composite"},
    "decorator": {"decorator"},
    "fsm": {"fsm-state", "fsm-node"},
    "dt": {"dt-node"},
}


def cmd_types(ws, a) -> int:
    roles = set()
    for r in a.role or ["action", "condition", "bt", "fsm", "dt"]:
        roles |= ROLE_GROUPS[r]
    decls = ws.schema.all_of_role(roles)
    q = (a.query or "").lower()
    rows = []
    for d in decls:
        disp = d.attr_str("Name") or ""
        cat = d.attr_str("Category") or ""
        if q and q not in d.name.lower() and q not in disp.lower() and q not in cat.lower():
            continue
        rows.append((ws.schema.role(d.full_name), cat, d))
    rows.sort(key=lambda r: (r[0] or "", r[1], r[2].name))
    exact = [
        r
        for r in rows
        if q
        and (r[2].name.lower() == q or (r[2].attr_str("Name") or "").replace(" ", "").lower() == q.replace(" ", ""))
    ]
    if exact and not a.json:
        rows = exact
    if a.json:
        print(
            json.dumps(
                [
                    {
                        "role": r,
                        "category": c,
                        "type": d.full_name,
                        "name": d.attr_str("Name"),
                        "description": d.attr_str("Description"),
                        **({"fields": _field_rows(ws, d)} if a.fields or exact or len(rows) == 1 else {}),
                    }
                    for r, c, d in rows
                ],
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    if not rows and a.query:
        for decl in ws.schema.lookup(a.query):
            bases = f" : {', '.join(decl.bases)}" if decl.bases else ""
            print(
                f"{decl.full_name}: {decl.kind}{bases} in {decl.file} — not a task or node type "
                f"(use `nodecanvas-cli members {decl.full_name.split('`')[0]}` for its members)"
            )
        if ws.schema.lookup(a.query):
            return 0
    if len(rows) == 1 or a.fields:
        for r, c, d in rows:
            _print_type(ws, d, r)
        return 0
    for r, c, d in rows:
        generic = f"<{','.join('T' * d.arity)}>" if d.arity else ""
        desc = (d.attr_str("Description") or "").split("\n")[0]
        print(f"{r:<10} {d.name}{generic:<4} {('[' + c + ']') if c else '':<24} {desc[:90]}")
    print(f"{len(rows)} types (use `nodecanvas-cli types <Name>` for fields)")
    return 0


def _print_type(ws, d, role) -> None:
    print(f"{d.full_name}  role={role}" + (f'  name="{d.attr_str("Name")}"' if d.attr_str("Name") else ""))
    if d.attr_str("Description"):
        print("  " + d.attr_str("Description").replace("\n", " ")[:300])
    for row in _field_rows(ws, d):
        extra = "  {" + ", ".join(row["enum"]) + "}" if row.get("enum") else ""
        init = f" = {row['default']}" if row.get("default") else ""
        label = f'  "{row["label"]}"' if row.get("label") else ""
        flags = "".join(f"  [{flag}]" for flag in row.get("flags", []))
        doc = "  — " + row["doc"] if row.get("doc") else ""
        print(f"  {row['name']}: {row['type']}{init}{extra}{label}{flags}{doc}")
    print()


_HIDDEN_FIELDS = {
    "_UID",
    "_name",
    "_tag",
    "_position",
    "_comment",
    "_isBreakpoint",
    "_collapsed",
    "_color",
    "_verboseLevel",
    "_isDisabled",
    "overrideAgent",
    "_infoCollapsed",
}
_FIELD_FLAGS = {"RequiredField": "required", "BlackboardOnly": "blackboard only"}


def _field_rows(ws, d) -> list:
    """The fields a person sets on this type: what they are, what they mean (``[Tooltip]``, ``[Name]``)."""
    rows = []
    for name, f, owner in ws.schema.ordered_fields(d.full_name):
        if name in _HIDDEN_FIELDS:
            continue
        row = {"name": name, "type": f.type}
        enum = ws.schema.enum_for(re.sub(r"^BBParameter<(.+)>$", r"\1", f.type), owner)
        if enum is not None:
            row["enum"] = list(enum.enum_values().values())
        if f.init:
            row["default"] = f.init
        if f.display_name:
            row["label"] = f.display_name
        flags = [text for attr, text in _FIELD_FLAGS.items() if attr in f.attrs]
        if flags:
            row["flags"] = flags
        if f.doc:
            row["doc"] = " ".join(f.doc.split())
        rows.append(row)
    return rows


def cmd_members(ws, a) -> int:
    cands = ws.schema.lookup(a.type)
    if len(cands) > 1:
        cands = [c for c in cands if c.arity == 0] or cands
    if len(cands) > 1:
        print(f"error: type {a.type!r} is ambiguous, use the full name: " + ", ".join(c.full_name for c in cands))
        return 1
    if not cands:
        print(f"error: type {a.type!r} not found in project sources")
        return 1
    decl = cands[0]
    q = (a.query or "").lower()
    print(f"{decl.full_name}" + (f" : {', '.join(decl.bases)}" if decl.bases else ""))
    seen = set()
    for c in ws.schema.chain(decl):
        for name, typ in c.properties.items():
            if (q and q not in name.lower()) or name in seen:
                continue
            seen.add(name)
            print(f"  prop   {typ} {name}" + (f"   [{c.name}]" if c is not decl else ""))
        for name, sigs in c.methods.items():
            if q and q not in name.lower():
                continue
            for s in sigs:
                if not a.all and "public" not in s.get("mods", []):
                    continue
                key = (name, tuple(s["params"]))
                if key in seen:
                    continue
                seen.add(key)
                static = "static " if "static" in s.get("mods", []) else ""
                print(
                    f"  method {static}{s['ret']} {name}({', '.join(s['params'])})"
                    + (f"   [{c.name}]" if c is not decl else "")
                )
    return 0


def register(sub) -> None:
    s = sub.add_parser("types", help="list task/node types available in the project, or show one type's fields")
    s.add_argument("query", nargs="?", help="filter by name, display name or category; an exact match prints fields")
    s.add_argument("--role", action="append", choices=sorted(ROLE_GROUPS))
    s.add_argument("--fields", action="store_true", help="print fields for every match")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_types)

    s = sub.add_parser("members", help="list properties and methods of a C# type (targets for call/check/get)")
    s.add_argument("type")
    s.add_argument("query", nargs="?")
    s.add_argument("--all", action="store_true", help="include non-public methods")
    s.set_defaults(func=cmd_members)
