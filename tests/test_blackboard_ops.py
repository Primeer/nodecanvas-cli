import json


def _apply(ncg, project, graph, ops, *flags):
    (project / "ops.json").write_text(json.dumps(ops))
    return ncg("apply", graph, project / "ops.json", *flags)


def _names(ncg, graph):
    return [line.split(":")[0].lstrip("$") for line in ncg("vars", graph)[1].splitlines() if line.startswith("$")]


def _raw(project, graph="Bot_BT"):
    from nodecanvas_cli.workspace import Workspace

    return Workspace(project).graph(graph)


def test_var_add_positions(ncg, project):
    first, second = _names(ncg, "Bot_BT")[:2]
    ops = [
        {"op": "var-add", "name": "ByAfter", "type": "float", "after": f"${first}"},
        {"op": "var-add", "name": "ByBefore", "type": "float", "before": first},
        {"op": "var-add", "name": "ByIndex", "type": "float", "index": 0},
        {"op": "var-add", "name": "AtEnd", "type": "float"},
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    names = _names(ncg, "Bot_BT")
    assert names[:5] == ["ByIndex", "ByBefore", first, "ByAfter", second] and names[-1] == "AtEnd"


def test_var_add_into_a_section(ncg, project):
    ops = [
        {"op": "var-add", "name": "Params", "type": "NodeCanvas.Framework.VariableSeperator", "index": 0},
        {"op": "var-add", "name": "Stop distance", "type": "float", "after": "Params"},
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    out = ncg("vars", "Bot_BT")[1].splitlines()
    assert out[0] == "-- Params --" and out[1].startswith("$Stop distance: float")


def test_var_position_errors(ncg, project):
    first = _names(ncg, "Bot_BT")[0]
    code, _, err = _apply(ncg, project, "Bot_BT", [{"op": "var-add", "name": "X", "type": "int", "after": "Nope"}])
    assert code == 1 and "no variable or section 'Nope'" in err and first in err
    ops = [{"op": "var-add", "name": "X", "type": "int", "after": first, "index": 0}]
    code, _, err = _apply(ncg, project, "Bot_BT", ops)
    assert code == 1 and "one of after, before, index" in err
    code, _, err = _apply(ncg, project, "Bot_BT", [{"op": "var-add", "name": "X", "type": "int", "index": 99}])
    assert code == 1 and "index must be" in err


def test_var_move_keeps_identity(ncg, project):
    names = _names(ncg, "Bot_BT")
    last, first = names[-1], names[0]
    vid = _raw(project).var_by_name(last)["_id"]
    assert _apply(ncg, project, "Bot_BT", [{"op": "var-move", "name": last, "before": first}])[0] == 0
    assert _names(ncg, "Bot_BT") == [last] + names[:-1]
    assert _raw(project).var_by_name(last)["_id"] == vid
    assert _apply(ncg, project, "Bot_BT", [{"op": "var-move", "name": last, "index": len(names) - 1}])[0] == 0
    assert _names(ncg, "Bot_BT") == names
    code, _, err = _apply(ncg, project, "Bot_BT", [{"op": "var-move", "name": last}])
    assert code == 1 and "needs after, before or index" in err


def test_var_set_type_keeps_id_and_references(ncg, project):
    (project / "Assets/Game/Kin.cs").write_text("namespace Kin { public class A { } public class B { } }")
    ops = [{"op": "var-add", "name": "K", "type": "Kin.A", "public": True}]
    assert _apply(ncg, project, "Sub_BT", ops)[0] == 0
    vid = _raw(project, "Sub_BT").var_by_name("K")["_id"]
    code, out, _ = _apply(ncg, project, "Sub_BT", [{"op": "var-set", "name": "K", "type": "Kin.B"}])
    assert code == 0 and "type A -> B" in out
    var = _raw(project, "Sub_BT").var_by_name("K")
    assert var["_id"] == vid and "Kin.B" in var["$type"] and list(var)[-1] == "$type"


def test_var_set_type_converts_or_drops_the_value(ncg, project):
    ops = [
        {"op": "var-add", "name": "N", "type": "int", "value": 3},
        {"op": "var-add", "name": "V", "type": "Vector3", "value": [1, 2, 3]},
        {"op": "var-add", "name": "W", "type": "float", "value": 2.5},
    ]
    assert _apply(ncg, project, "Sub_BT", ops)[0] == 0
    ops = [
        {"op": "var-set", "name": "N", "type": "float"},
        {"op": "var-set", "name": "V", "type": "float"},
        {"op": "var-set", "name": "W", "type": "int", "value": 7},
    ]
    code, out, _ = _apply(ncg, project, "Sub_BT", ops)
    assert code == 0 and "$V: the stored value does not fit the new type and was dropped" in out
    g = _raw(project, "Sub_BT")
    assert g.var_by_name("N")["_value"] == 3.0 and "System.Single" in g.var_by_name("N")["$type"]
    assert "_value" not in g.var_by_name("V")
    assert g.var_by_name("W")["_value"] == 7


def test_var_set_type_refuses_an_ambiguous_name(ncg, project):
    (project / "Assets/Game/Dup.cs").write_text(
        "namespace X { public class Dup { } } namespace Y { public class Dup { } }"
    )
    assert _apply(ncg, project, "Sub_BT", [{"op": "var-add", "name": "D", "type": "X.Dup"}])[0] == 0
    code, _, err = _apply(ncg, project, "Sub_BT", [{"op": "var-set", "name": "D", "type": "Dup"}])
    assert code == 1 and "ambiguous" in err and "X.Dup" in err and "Y.Dup" in err


def test_var_set_type_is_blocked_by_a_binding_it_breaks(ncg, project):
    (project / "Assets/Game/Kin.cs").write_text("namespace Kin { public class A { } public class B { } }")
    ops = [
        {"op": "var-add", "name": "K", "type": "Kin.A"},
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ActionNode", "_action": {"$type": "SetVariable<Kin.A>", "valueA": "$K"}},
        },
    ]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0
    code, out, err = _apply(ncg, project, "Bot_BT", [{"op": "var-set", "name": "K", "type": "Kin.B"}])
    assert code == 1 and "param-type-mismatch" in out and "not saved" in err
    assert "Kin.A" in _raw(project).var_by_name("K")["$type"]


def test_var_set_type_mentions_parent_graphs(ncg, project):
    assert _apply(ncg, project, "Sub_BT", [{"op": "var-add", "name": "P", "type": "int", "public": True}])[0] == 0
    code, out, _ = _apply(ncg, project, "Sub_BT", [{"op": "var-set", "name": "P", "type": "float"}])
    assert code == 0 and "parent graphs may map it" in out and "Bot_BT #8" in out


def test_var_set_type_recovers_a_missing_wrapper(ncg, project):
    assert _apply(ncg, project, "Sub_BT", [{"op": "var-add", "name": "M", "type": "int"}])[0] == 0
    g = _raw(project, "Sub_BT")
    var = g.var_by_name("M")
    var["$type"] = "NodeCanvas.Framework.Internal.MissingVariableType"
    var["_missingType"] = "Gone.Thing"
    var["_recoveryState"] = "{}"
    from nodecanvas_cli.workspace import Workspace

    ws = g.loaded.file and Workspace(project)
    ws._graphs[g.loaded.slot.address] = g
    ws._files[g.loaded.slot.path] = g.loaded.file
    ws.save(g)
    assert _apply(ncg, project, "Sub_BT", [{"op": "var-set", "name": "M", "type": "float"}])[0] == 0
    var = _raw(project, "Sub_BT").var_by_name("M")
    assert "System.Single" in var["$type"] and "_missingType" not in var and "_recoveryState" not in var


# ---------------------------------------------------------------------- types


def test_types_explains_fields_from_tooltips(ncg, project):
    (project / "Assets/Game/Doc.cs").write_text(
        """
using NodeCanvas.Framework; using ParadoxNotion.Design; using UnityEngine;
namespace Demo.Doc
{
    public class Documented : ActionTask
    {
        [Tooltip("How far to go.\\nIn metres.")] public float reach = 2f;
        [Name("Goal"), RequiredField, BlackboardOnly, Tooltip("Where to go.")] public BBParameter<Vector3> target;
        public bool silent;
    }
}
"""
    )
    code, out, _ = ncg("types", "Documented")
    assert code == 0
    assert "reach: float = 2f  — How far to go. In metres." in out
    assert 'target: BBParameter<Vector3>  "Goal"  [required]  [blackboard only]  — Where to go.' in out
    assert "\n  silent: bool\n" in out
    row = json.loads(ncg("types", "Documented", "--json")[1])[0]
    field = next(f for f in row["fields"] if f["name"] == "target")
    assert field["doc"] == "Where to go." and field["label"] == "Goal" and "required" in field["flags"]


def test_types_explains_builtin_nodes_without_sources(ncg):
    # the fixture's stubs declare no Parallel: the description comes from the built-in declarations
    out = ncg("types", "Parallel")[1]
    assert 'dynamic: bool  "Repeat"  — Children that finish run again' in out
    assert "policy: ParallelPolicy = ParallelPolicy.FirstFailure" in out and "Which child results" in out
