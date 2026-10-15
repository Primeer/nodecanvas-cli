# Changelog

## 0.2.0 — 2026-10-16

- Claude Code plugin: `bin/nodecanvas-cli` puts the command on the agent's PATH (Claude Code 2.1.91+),
  so plugin users no longer call the launcher by its full path.
- Launcher: works through symlinks, skips interpreters older than Python 3.9 (`NODECANVAS_PYTHON` overrides),
  and keeps LF line endings on Windows checkouts (`.gitattributes`).
- `apply`: `var-set` without `value` (e.g. only `public`) keeps the current value instead of failing with
  a TypeError.
- `apply`: the before/after diff gets a `vars:` section with added, removed, renamed, retyped and revalued
  blackboard variables and public/private changes.
- `new --from --private-vars` and `copy --public-vars` take optional, repeatable name globs
  (`--private-vars "Under attack *"`) to set visibility per variable; a glob that matches nothing is reported.
  The MCP `new_graph` and `copy` tools get `private_vars` / `public_vars`.
- `new`, `extract`: with `--project` (and in the MCP server), a relative path started outside the project is taken
  from the project root instead of failing with "must be inside the Unity project".
- `lint`: `name-mismatch` warning when a graph asset's `m_Name` differs from its file name, which Unity
  otherwise reports only on import.
- MCP server: concurrent tool calls no longer mix up or lose each other's output.
- Saving several graphs of one scene/prefab (`refactor`, `dt import`, `dt export --assign-uids`) no longer
  fails half-way after the first graph changed the file's line count.
- `copy`, `extract`, `inline`, `new --from`: object references inside nodes and tasks (a statement's audio,
  `GameObject`/`Material`/… fields and parameters) are remapped to the destination's `_objectReferences`.
- Long dialogue texts with double spaces keep them: lines are no longer folded after a space.
- Graphs with `Infinity`/`NaN` float values can be edited.
- Non-ASCII `[Name]`, `[Category]`, `[Description]` strings in C# sources are read correctly.
- `apply`: a `MultipleChoiceNode`'s choices follow their connections on `disconnect`, `remove`, `move` and
  `connect`; `choice-add` without `to` adds an unlinked choice; new `choice-remove` op.
- Bad input (invalid `nodecanvas-cli.json`, a missing ops file, a graph name equal to a folder, `refactor
  retarget` without `Type.Method`) ends in `error:` instead of a traceback; `diff --rev` refuses option-like
  revisions.
- `scaffold --dir` with `--project` resolves a relative folder from the project root.
- Enum initializers in C# sources are evaluated without `eval` and with bounded values.
- New `$type`s name the right Unity module for audio, physics, 2D physics, animation, particle, UI, text,
  terrain and video types (not always `UnityEngine.CoreModule`), so Unity does not rewrite them on save.

## 0.1.0 — 2026-10-12

First public release.

- `list`, `show`, `vars`, `node`, `diff`: compact views of Behaviour Trees, FSMs and Dialogue Trees in graph
  assets and in graphs bound to GraphOwners in prefabs and scenes.
- `refs`, `why`, `paths`, `stats`: reverse lookup of members, subtrees, variables and task types across all
  graphs; conditions on the way to a node; every call of a member or write of a variable.
- `lint`: missing types, dangling blackboard references, broken reflection bindings, subgraph mappings,
  connection limits, unreachable nodes, owner and global variables.
- `apply`: atomic, validated batches of edit operations with short value forms and `--dry-run` diffs;
  `new`, `extract`, `inline`, `copy`, `refactor rename-member|rename-type|retarget`, `scaffold`.
- `dt export` / `dt import` / `dt lint`: dialogue lines and translations to CSV/JSON and back.
- `owners`, `blackboards`, `types`, `members`.
- `verify --unity`: load the edited graphs in the open Unity Editor and report what NodeCanvas sees.
- `mcp`: stdio MCP server (`nodecanvas-cli[mcp]`, Python 3.10+).
- Claude Code plugin with a skill and a bundled launcher.
- Byte-exact round-trip of unchanged graphs; Unity's YAML folding and FullSerializer key order on save.
