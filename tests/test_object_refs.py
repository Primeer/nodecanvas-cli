import json

import pytest


def _apply(ncg, project, graph, ops, *flags):
    (project / "ops.json").write_text(json.dumps(ops))
    return ncg("apply", graph, project / "ops.json", *flags)


def _guid(project, name):
    meta = (project / f"Assets/Graphs/{name}.asset.meta").read_text()
    return next(line.split()[1] for line in meta.splitlines() if line.startswith("guid:"))


def _text(project, name="Bot_BT"):
    return (project / f"Assets/Graphs/{name}.asset").read_text()


def _graph(project, name="Bot_BT"):
    from nodecanvas_cli.workspace import Workspace

    return Workspace(project).graph(name)


def test_subgraph_set_leaves_no_trace_of_the_old_graph(ncg, project):
    old = _guid(project, "Sub_BT")
    assert old in _text(project)
    code, out, _ = _apply(
        ncg, project, "Bot_BT", [{"op": "subgraph-set", "node": "8", "graph": "Patrol_BT"}], "--dry-run"
    )
    assert code == 0 and "removed the unused object reference to Sub_BT" in out
    assert old in _text(project)  # dry run
    assert _apply(ncg, project, "Bot_BT", [{"op": "subgraph-set", "node": "8", "graph": "Patrol_BT"}])[0] == 0
    assert old not in _text(project) and _guid(project, "Patrol_BT") in _text(project)
    g = _graph(project)
    assert g.ref(g.node("8")["_subTree"]["_value"]).guid == _guid(project, "Patrol_BT")
    assert "Patrol_BT" in ncg("show", "Bot_BT")[1]


def test_all_references_gone_makes_an_empty_list(ncg, project):
    assert _apply(ncg, project, "Bot_BT", [{"op": "subgraph-set", "node": "8", "graph": None}])[0] == 0
    assert "_objectReferences: []" in _text(project)


def test_an_index_in_a_field_of_unknown_type_keeps_its_entry(ncg, project):
    assert _apply(ncg, project, "Bot_BT", [{"op": "subgraph-set", "node": "8", "graph": "Patrol_BT"}])[0] == 0
    g = _graph(project)
    keep = len(g.refs)
    ops = [
        {"op": "subgraph-set", "node": "8", "graph": "Sub_BT"},
        {"op": "set", "node": "8", "path": "_mystery", "raw": True, "value": keep - 1},
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    assert _guid(project, "Patrol_BT") in _text(project)  # _mystery may be holding that index


def test_nulling_keeps_indices_when_a_use_is_uncertain(ncg, project):
    from nodecanvas_cli.container import ObjectRef
    from nodecanvas_cli.typedwalk import prune_refs
    from nodecanvas_cli.workspace import Workspace

    ws = Workspace(project)
    g = ws.graph("Bot_BT")
    node = g.node("8")
    used = node["_subTree"]["_value"]
    g.refs[:] = [ObjectRef(0), ObjectRef(11400000, "a" * 32, 2), g.refs[used], ObjectRef(11400000, "b" * 32, 2)]
    node["_subTree"]["_value"] = 2
    node["_mystery"] = 7  # an int nothing can explain: indices must not move
    removed = prune_refs(ws, g)
    assert [r.guid for r in removed] == ["a" * 32, "b" * 32]
    assert g.refs[1].is_null and len(g.refs) == 3 and node["_subTree"]["_value"] == 2


def test_compacting_renumbers_when_every_use_is_known(ncg, project):
    from nodecanvas_cli.container import ObjectRef
    from nodecanvas_cli.typedwalk import prune_refs
    from nodecanvas_cli.workspace import Workspace

    ws = Workspace(project)
    g = ws.graph("Bot_BT")
    node = g.node("8")
    sub = g.refs[node["_subTree"]["_value"]]
    g.refs[:] = [ObjectRef(0), ObjectRef(11400000, "a" * 32, 2), sub]
    node["_subTree"]["_value"] = 2
    assert [r.guid for r in prune_refs(ws, g)] == ["a" * 32]
    assert [r.guid for r in g.refs] == [None, sub.guid] and node["_subTree"]["_value"] == 1


def test_object_value_of_a_variable_is_a_use(ncg, project):
    from nodecanvas_cli.typedwalk import stale_refs
    from nodecanvas_cli.workspace import Workspace

    ops = [{"op": "var-add", "name": "Tree", "type": "NodeCanvas.BehaviourTrees.BehaviourTree", "value": "Patrol_BT"}]
    assert _apply(ncg, project, "Sub_BT", ops)[0] == 0
    assert _guid(project, "Patrol_BT") in _text(project, "Sub_BT")
    ws = Workspace(project)
    assert stale_refs(ws, ws.graph("Sub_BT")) == []


def test_recovery_state_keeps_its_indices(ncg, project):
    from nodecanvas_cli.container import ObjectRef
    from nodecanvas_cli.typedwalk import stale_refs
    from nodecanvas_cli.workspace import Workspace

    ws = Workspace(project)
    g = ws.graph("Sub_BT")
    g.refs[:] = [ObjectRef(0), ObjectRef(11400000, "a" * 32, 2)]
    g.nodes[0]["_wrapped"] = {"_recoveryState": json.dumps({"target": {"_value": 1}})}
    assert stale_refs(ws, g) == []
    g.nodes[0]["_wrapped"]["_recoveryState"] = "{broken"
    assert stale_refs(ws, g) == []


def test_lint_reports_entries_already_in_a_file(ncg, project):
    path = project / "Assets/Graphs/Bot_BT.asset"
    lines = path.read_text().split("\n")
    i = next(k for k, line in enumerate(lines) if line.startswith("  _objectReferences:"))
    end = i + 1
    while lines[end].startswith("  - "):
        end += 1
    lines.insert(end, "  - {fileID: 11400000, guid: " + _guid(project, "Patrol_BT") + ", type: 2}")
    path.write_text("\n".join(lines))
    issues = json.loads(ncg("lint", "Bot_BT", "--json")[1])
    issue = next(i for i in issues if i["code"] == "stale-object-ref")
    assert "Patrol_BT" in issue["message"] and issue["severity"] == "warning"
    assert _apply(ncg, project, "Bot_BT", [{"op": "var-add", "name": "N", "type": "int"}])[0] == 0
    assert not [i for i in json.loads(ncg("lint", "Bot_BT", "--json")[1]) if i["code"] == "stale-object-ref"]


def test_subgraph_set_carries_the_mapping_by_name(ncg, project):
    sub = [{"op": "var-add", "name": "Shared", "type": "float", "public": True}]
    assert _apply(ncg, project, "Sub_BT", sub)[0] == 0
    assert _apply(ncg, project, "Patrol_BT", sub)[0] == 0
    extra = [{"op": "var-add", "name": "OnlyHere", "type": "float", "public": True}]
    assert _apply(ncg, project, "Sub_BT", extra)[0] == 0
    ops = [
        {"op": "var-add", "name": "Mine", "type": "float"},
        {"op": "map", "node": "8", "map": {"Shared": "<-$Mine", "OnlyHere": "<-$Mine"}},
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    code, out, _ = _apply(ncg, project, "Bot_BT", [{"op": "subgraph-set", "node": "8", "graph": "Patrol_BT"}])
    assert code == 0 and "$OnlyHere" in out and "removed" in out
    entries = _graph(project).node("8")["_variablesMap"]
    new_id = _graph(project, "Patrol_BT").var_by_name("Shared")["_id"]
    assert [m["_targetSubGraphVariableID"] for m in entries] == [new_id]
    assert not [i for i in json.loads(ncg("lint", "Bot_BT", "--json")[1]) if i["code"] == "stale-mapping"]


@pytest.mark.parametrize("command", ["copy", "new", "extract"])
def test_copied_object_variables_keep_their_object(ncg, project, command):
    ops = [
        {"op": "var-add", "name": "Tree", "type": "NodeCanvas.BehaviourTrees.BehaviourTree", "value": "Patrol_BT"},
        {
            "op": "add",
            "parent": "0",
            "id": "n",
            "node": {
                "$type": "ActionNode",
                "_action": {"$type": "SetVariable<NodeCanvas.BehaviourTrees.BehaviourTree>", "valueA": "$Tree"},
            },
        },
    ]
    assert _apply(ncg, project, "Sub_BT", ops)[0] == 0
    tree = ncg("show", "Sub_BT")[1]
    nid = next(line for line in tree.splitlines() if "$Tree" in line).split("#")[1].split()[0]
    if command == "copy":
        assert ncg("copy", "Sub_BT", nid, "Patrol_BT", "0", "--create-vars")[0] == 0
        name = "Patrol_BT"
    elif command == "new":
        assert ncg("new", "Assets/Graphs/Fresh.asset", "--kind", "bt", "--from", f"Sub_BT:{nid}")[0] == 0
        name = "Fresh"
    else:
        assert ncg("extract", "Sub_BT", nid, "Assets/Graphs/Fresh.asset")[0] == 0
        name = "Fresh"
    g = _graph(project, name)
    assert g.ref(g.var_by_name("Tree")["_value"]).guid == _guid(project, "Patrol_BT")
