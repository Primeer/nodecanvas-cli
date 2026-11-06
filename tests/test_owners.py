import json


def test_owners_of_graph(ncg):
    code, out, _ = ncg("owners", "Patrol_BT")
    assert code == 0
    # GuardOwner is a project class deriving from BehaviourTreeOwner: found through the schema
    assert "Assets/Prefabs/Guard.prefab:Guard  GuardOwner" in out
    assert "bb: Blackboard: $Home dist; parent AssetBlackboard Assets/Blackboards/Shared.asset: $Reach" in out
    assert "$Patience = 3  (int)" in out and "$Spare = 1.5  (float)" in out
    assert "= Guard (GuardOwner)" in out  # object override resolved to a local component


def test_owners_prefab_instance_and_subgraph(ncg):
    _, out, _ = ncg("owners", "Bot_FSM")
    assert "Assets/Scenes/World.unity  PrefabInstance of Guard overrides _graph" in out
    _, out, _ = ncg("owners", "Sub_BT")
    assert "subgraph of: Bot_BT #8, Bot_FSM #0" in out
    _, out, _ = ncg("owners", "Talk_DT")
    assert "not assigned to any owner or parent graph" in out
    _, out, _ = ncg("owners", "Sentry")
    assert "BehaviourTreeOwner  (bound graph)" in out


def test_owners_all(ncg):
    code, out, _ = ncg("owners", "--all")
    assert code == 0
    lines = {line.split()[0]: line for line in out.splitlines()}
    assert "Guard.prefab:Guard (GuardOwner)" in lines["Patrol_BT"]
    assert "not assigned in assets" in lines["Talk_DT"]
    assert "subgraph of Bot_FSM #1" in lines["Bot_BT"]
    assert "World.unity:Runner (FSMOwner) has no graph assigned" in out
    data = json.loads(ncg("owners", "--all", "--json")[1])
    assert {g["graph"] for g in data["graphs"] if g["unassigned"]} == {
        "Assets/Graphs/Broken_BT.asset",
        "Assets/Graphs/Talk_DT.asset",
    }


def test_blackboards(ncg):
    _, out, _ = ncg("blackboards")
    assert (
        "Assets/Prefabs/Guard.prefab:Guard  Blackboard  parent=Assets/Blackboards/Shared.asset\n"
        "  $Home dist: float = 4.0" in out
    )
    assert 'Assets/Scenes/World.unity:Globals  GlobalBlackboard  identifier="Global"\n  $Alarm: bool' in out
    assert "Assets/Blackboards/Shared.asset  AssetBlackboard\n  $Reach: float = 2.5" in out
    _, out, _ = ncg("blackboards", "--global")
    assert "Guard.prefab" not in out and "Globals" in out
    data = json.loads(ncg("blackboards", "--json")[1])
    assert any(b["kind"] == "global" and "Alarm" in b["variables"] for b in data)


def test_vars_and_show_external_sources(ncg):
    _, out, _ = ncg("vars", "Patrol_BT")
    assert "$Global/Alarm: bool  global: Assets/Scenes/World.unity:Globals  used: #1" in out
    assert "$Home dist: float  owner: Assets/Prefabs/Guard.prefab:Guard (Blackboard)  used: #2" in out
    assert "$Reach: float  owner: Assets/Blackboards/Shared.asset (AssetBlackboard)  used: #4" in out
    assert '$World/Panic: ?  MISSING (no GlobalBlackboard "World" in the project)' in out
    assert "$Patience overridden by Assets/Prefabs/Guard.prefab:Guard = 3" in out
    _, out, _ = ncg("show", "Patrol_BT")
    assert "external: $Global/Alarm(global)  $Home dist(owner)  $World/Panic(missing)  $Reach(owner)" in out
    assert "#2 $Home dist = $Patience" in out and "#3 $World/Panic! = true" in out


def test_lint_owner_checks(ncg):
    _, out, _ = ncg("lint", "Patrol_BT", "--min", "info", "--json")
    codes = {(i["at"], i["code"]) for i in json.loads(out)}
    assert ("#1", "global-var") in codes and ("#2", "owner-var") in codes and ("#4", "owner-var") in codes
    assert ("#3", "missing-global-var") in codes
    assert ("owner Assets/Prefabs/Guard.prefab:Guard", "exposed-override-type") in codes
    assert ("owner Assets/Prefabs/Guard.prefab:Guard", "exposed-override-stale") in codes
    assert not any(c == "unused-var" for _, c in codes)  # $Spare is overridden by the owner
    assert not any(c == "dangling-var" for _, c in codes)
    _, out, _ = ncg("lint")
    assert "owner-missing-graph  FSMOwner has no graph assigned" in out
    _, out, _ = ncg("lint", "Broken_BT")
    assert "dangling-var" in out and "owner-missing-graph" not in out


def test_apply_uses_external_variables(ncg, project):
    ops = [
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ActionNode", "_action": {"$type": "SetFloat", "valueA": "$Reach", "valueB": 3}},
        },
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ConditionNode", "_condition": {"$type": "CheckBoolean", "valueA": "$Global/Alarm"}},
        },
    ]
    (project / "ops.json").write_text(json.dumps(ops))
    code, out, err = ncg("apply", "Patrol_BT", project / "ops.json")
    assert code == 0, err
    _, out, _ = ncg("node", "Patrol_BT", "5", "6", "--raw")
    raw = json.loads(out)
    assert raw["5"]["node"]["_action"]["valueA"]["_name"] == "Reach"
    assert raw["6"]["node"]["_condition"]["valueA"]["_name"] == "Global/Alarm"
    assert "_targetVariableID" in raw["6"]["node"]["_condition"]["valueA"]  # the global variable's id
    _, out, _ = ncg("node", "Patrol_BT", "6")
    assert '"valueA": "$Global/Alarm"' in out
    (project / "bad.json").write_text(
        json.dumps(
            [
                {
                    "op": "add",
                    "parent": "0",
                    "node": {"$type": "ActionNode", "_action": {"$type": "SetFloat", "valueA": "$Nowhere"}},
                }
            ]
        )
    )
    code, _, err = ncg("apply", "Patrol_BT", project / "bad.json")
    assert code == 1 and "owners' blackboards" in err


BOT_DATA = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!114 &11400000
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 0}}
  m_Script: {{fileID: 11500000, guid: {script}, type: 3}}
  m_Name: Kai_BotData
  m_EditorClassIdentifier: Game::Demo.BotData
  nameKey: bot7_name
  behaviour:
    primary:
      fsm: {{fileID: 11400000, guid: {guid}, type: 2}}
  extras:
  - id: 1
    tree: {{fileID: 11400000, guid: {guid}, type: 2}}
  - {{fileID: 11400000, guid: {guid}, type: 2}}
  addressable:
    m_AssetGUID: {guid}
"""


def _graph_guid(project, name):
    meta = (project / f"Assets/Graphs/{name}.asset.meta").read_text()
    return next(line.split()[1] for line in meta.splitlines() if line.startswith("guid:"))


def _add_bot_data(project, name="Talk_DT"):
    (project / "Assets/Game/BotData.cs").write_text(
        "namespace Demo { public class BotData : UnityEngine.ScriptableObject { } }"
    )
    (project / "Assets/Game/BotData.cs.meta").write_text("fileFormatVersion: 2\nguid: " + "c" * 32 + "\n")
    text = BOT_DATA.format(script="c" * 32, guid=_graph_guid(project, name))
    (project / "Assets/Game/Kai_BotData.asset").write_text(text)
    return project / "Assets/Game/Kai_BotData.asset"


def test_owners_sees_references_from_scriptable_objects(ncg, project):
    _add_bot_data(project)
    code, out, _ = ncg("owners", "Talk_DT")
    assert code == 0 and "not referenced" not in out
    assert "referenced by: Assets/Game/Kai_BotData.asset (BotData).behaviour.primary.fsm" in out
    assert "(BotData).extras.tree" in out and "(BotData).extras\n" in out
    assert "(BotData).addressable.m_AssetGUID" in out
    data = json.loads(ncg("owners", "Talk_DT", "--json")[1])
    assert {"file": "Assets/Game/Kai_BotData.asset", "object": "(BotData)", "field": "behaviour.primary.fsm"} in data[
        "referenced_by"
    ]


def test_owners_all_counts_asset_references_as_a_use(ncg, project):
    _add_bot_data(project)
    data = json.loads(ncg("owners", "--all", "--json")[1])
    assert {g["graph"] for g in data["graphs"] if g["unassigned"]} == {"Assets/Graphs/Broken_BT.asset"}
    out = ncg("owners", "--all")[1]
    line = next(x for x in out.splitlines() if x.startswith("Talk_DT"))
    assert "referenced by Assets/Game/Kai_BotData.asset" in line


def test_refs_lists_asset_references(ncg, project):
    _add_bot_data(project)
    out = ncg("refs", "Talk_DT")[1]
    assert "referenced by: Assets/Game/Kai_BotData.asset (BotData).behaviour.primary.fsm" in out
    hits = json.loads(ncg("refs", "Talk_DT", "--json")[1])
    assert any(h.get("asset") == "Assets/Game/Kai_BotData.asset" for h in hits)
    assert "referenced by" not in ncg("refs", "Talk_DT", "--kind", "member")[1]


def test_asset_references_follow_file_changes(ncg, project):
    path = _add_bot_data(project)
    assert "referenced by:" in ncg("owners", "Talk_DT")[1]
    path.write_text(path.read_text().replace(_graph_guid(project, "Talk_DT"), "d" * 32) + "# edited\n")
    assert "referenced by:" not in ncg("owners", "Talk_DT")[1]


def test_prefab_fields_and_overrides_are_named(ncg, project):
    guid = _graph_guid(project, "Talk_DT")
    (project / "Assets/Prefabs/Holder.prefab").write_text(
        f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100
GameObject:
  m_Name: Holder
--- !u!114 &200
MonoBehaviour:
  m_GameObject: {{fileID: 100}}
  m_EditorClassIdentifier: Game::Demo.Brain
  dialogue: {{fileID: 11400000, guid: {guid}, type: 2}}
--- !u!1001 &300
PrefabInstance:
  m_Modification:
    m_Modifications:
    - target: {{fileID: 1}}
      propertyPath: speech
      objectReference: {{fileID: 11400000, guid: {guid}, type: 2}}
"""
    )
    out = ncg("owners", "Talk_DT")[1]
    assert "Assets/Prefabs/Holder.prefab Holder (Brain).dialogue" in out
    assert "Assets/Prefabs/Holder.prefab PrefabInstance.speech" in out


def test_a_stale_entry_in_another_graph_is_not_a_use(ncg, project):
    path = project / "Assets/Graphs/Bot_BT.asset"
    lines = path.read_text().split("\n")
    i = next(k for k, line in enumerate(lines) if line.startswith("  _objectReferences:"))
    lines.insert(i + 1, "  - {fileID: 11400000, guid: " + _graph_guid(project, "Talk_DT") + ", type: 2}")
    path.write_text("\n".join(lines))
    assert "referenced by:" not in ncg("owners", "Talk_DT")[1]


def test_a_field_called_graph_on_another_class_is_still_a_reference(ncg, project):
    path = _add_bot_data(project)
    path.write_text(path.read_text().replace("    primary:\n      fsm:", "    primary:\n      _graph:"))
    assert "(BotData).behaviour.primary._graph" in ncg("owners", "Talk_DT")[1]


def test_owner_fields_are_not_repeated_as_references(ncg):
    out = ncg("owners", "Patrol_BT")[1]
    assert "Assets/Prefabs/Guard.prefab:Guard  GuardOwner" in out and "referenced by:" not in out


def test_a_new_graph_is_found_in_files_scanned_before(ncg, project):
    path = _add_bot_data(project)
    assert "referenced by:" in ncg("owners", "Talk_DT")[1]
    assert ncg("new", "Assets/Graphs/Fresh.asset", "--kind", "bt")[0] == 0
    fresh = _graph_guid(project, "Fresh")
    # a file written before the graph existed, with the same size and time as when it was scanned
    import os

    st = path.stat()
    path.write_text(path.read_text().replace(_graph_guid(project, "Talk_DT"), fresh))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert "referenced by:" in ncg("owners", "Fresh")[1]
