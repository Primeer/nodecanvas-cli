# Changelog

## Unreleased

## 0.4.0 — 2026-11-16

- `verify --unity`, `layout --unity` and `calibrate --unity` work with the Unity CLI 1.0 and `com.unity.pipeline` 1.0,
  which removed `eval_file` and returns a command's result without the old wrapper: the snippet runs through
  `unity command script eval --file` with its timeout in seconds. A package older than 1.0 (read from
  `Packages/packages-lock.json`, or found by trying) still gets `eval_file`. A failed check in the Editor and a
  snippet that does not compile are reported as such (exit 1), no longer as "no Editor reachable". `unity status`
  is read in its current shape: Safe Mode lists compile errors with file and line, an Editor stopped on a modal
  dialog fails at once with the dialog's title and the `unity command editor dialog` hint, and an Editor that has
  not loaded the Pipeline package yet is waited for.
- Layout accounts for node size. The editor never saves a node's size, so it is estimated from the node's text: `info`
  of the tasks NodeCanvas ships, labels of built-in nodes, title and icon. Wide nodes no longer overlap their neighbours,
  and parents centre over their children. **Possibly breaking:** the default layout changes, and in
  `nodecanvas-cli.json` `level_gap` and `sibling_gap` now mean clear space between node edges (100 and 60 by default).
  `"sizing": "fixed"` keeps the old layout and meaning (200 and 300 between top-left corners).
- New `layout <graph> [--node N] [--unity] [--dry-run]` command and MCP tool. `--unity` takes exact sizes from the open
  Editor, for `.asset` graphs, and fails without one. `apply` always estimates, so an edit does not depend on Unity.
  Subtrees pack by their contours, so a shallow branch can sit above a deeper neighbour; `"sizing": "fixed"` keeps
  consecutive slots for leaves.
- New `calibrate --unity [--write]`: compares estimates with the Editor and stores `layout.char_width`.
- `lint`: `overlap` uses estimated node sizes instead of the 80×20 minimum, and `child-order` compares centres, which
  is what the editor sorts by.
- `lint`: new `stale-mapping-type`. A subgraph mapping stores the type of the subgraph's variable, and after that
  type changes (`var-set type`, or in C#) it stays old until the node is drawn in the editor, while NodeCanvas keeps
  converting values to it. An error when the cast to the variable's type cannot succeed, with a `fix:` op.
  `verify --unity` reports it too.
- `lint --changed` and `verify --changed` also check the direct parents of changed subgraphs: mapping checks live in
  the parent, so a subgraph-only change used to pass.
- `var-set type` names the parent graphs that map the variable.

## 0.3.0 — 2026-11-06

- `refactor rename-member`: several renames in one run — `Type.Old=New` pairs (`Type.Old(P1,P2)=New` picks an
  overload) and/or `--file` with one pair per line. One combined diff and lint pass, a per-pair summary,
  one save; if any pair fails nothing is written. Chains and swaps are rejected. The MCP
  `refactor_rename_member` tool takes `renames`.
- `lint --changed`: lint only graphs in files changed since `HEAD` (plus untracked), like
  `verify --unity --changed`; the MCP `lint` tool takes `changed`. `lint` and `verify` reject `--changed`
  together with graph names.
- `lint`: a type saved under a namespace that no longer exists, but found by its short name, is now a
  `stale-type-name` warning (with "ambiguous" when several types share the name) instead of a
  `missing-type` / `missing-var-type` error. NodeCanvas resolves such names by short name on load, so
  `verify --unity` and `lint` used to disagree. `show`/`vars` render these wrappers as `(recovered)`.
- `lint`: the summary line counts issues hidden by `--min` (`40 info hidden, --min info to show`).
- `verify --unity`: notes how many Missing wrappers in the file NodeCanvas restored on load.
- Type names typed by a person (`new --var`, `var-add`, `SetVariable<T>`, `call Type.Member`, `scaffold --agent`,
  `members`) never resolve by guess. A short name shared by several types is an error listing them, partial
  qualifiers work (`Arena.Pawn`), a wrong qualifier is no longer ignored, and an unknown short name is an error with
  suggestions instead of an invented `Name, Assembly-CSharp`. `refactor rename-member Pawn.X=Y` refuses when
  graphs call two types of that name, where it used to rename both. **Possibly breaking:** types outside the project's
  sources now need their full name (`UnityEngine.AI.NavMeshAgent`).
- New type model, following `ParadoxNotion.TypeConverter` and comparing full names. `lint`: new
  `param-type-mismatch` (task and node parameters against bound variables, with generic types substituted);
  `mapping-type-mismatch` checks each direction and can now be an error; `arg-type-mismatch` too. Two unrelated
  components with the same short name or types with no conversion are errors, so `apply`, `copy` and `new` refuse
  to introduce them without `--force`.
- `verify --unity` includes static lint errors and type-contract warnings: Unity reports neither, because such
  types "convert" to null through `GetComponent`.
- `verify --unity` waits for an Editor that is compiling or reloading scripts instead of failing with "no Editor
  reachable" right after `unity recompile`, repeats the check if a reload interrupts it, and reports Safe Mode with
  its compile errors and an open modal dialog as such.
- Messages, `vars`, `show` and `apply` diffs print full names for types whose short name is not unique.
- `_objectReferences`: entries nothing uses are removed on every save, so `subgraph-set` no longer leaves the old
  GUID behind. New `stale-object-ref` lint warning for files that already have such entries. Entries are never
  touched while a field of unknown type might be using them.
- `subgraph-set` moves mappings to the new graph's variables of the same name and type and removes the rest,
  instead of leaving stale ones.
- Fixed: `extract`, `inline`, `copy --create-vars` and `new --from` copied object values of variables with indices
  that meant another object, or nothing, in the destination graph.
- `owners` / `refs`: references to a graph from any field of any asset (`referenced by:
  Kai_BotData.asset (BotData).fsm`); such graphs are no longer reported as unassigned. Also finds Addressables
  references and object overrides in `m_Modifications`.
- `types` explains fields: `[Tooltip]`, `[Name]`, required / blackboard-only marks, also in `--json` (`fields`).
  The built-in declarations carry short explanations of the main nodes, and `types` lists them in projects
  without NodeCanvas sources.
- `apply`: `var-add` takes `after`, `before` or `index`; new `var-move`; `var-set` takes `type`.

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
