"""Two types with one short name, as real projects have them: nothing may pick or compare them by that name."""

import json
import re

import pytest

PAWNS = """
using UnityEngine;
namespace Demo.Meta { public class Pawn : MonoBehaviour { public void Greet() { } } }
namespace Demo.Arena
{
    public class Pawn : MonoBehaviour { public void Hit() { } }
    public class Hero : Pawn { }
    public class Health : MonoBehaviour { }
    public class Plain { }
    public class Other { }
    public interface ITarget { }
    public class Dummy : MonoBehaviour, ITarget { }
}
"""


@pytest.fixture
def pawns(project):
    (project / "Assets/Game/Pawns.cs").write_text(PAWNS)
    return project


def _apply(ncg, project, graph, ops, *flags):
    (project / "ops.json").write_text(json.dumps(ops))
    return ncg("apply", graph, project / "ops.json", *flags)


def _lint(ncg, graph, *flags):
    return json.loads(ncg("lint", graph, "--json", "--min", "info", *flags)[1])


# ---------------------------------------------------------------------- A: names typed by a person


def test_new_var_with_an_ambiguous_type_stops_and_lists_candidates(ncg, pawns):
    code, _, err = ncg("new", "Assets/Graphs/N.asset", "--kind", "bt", "--var", "Target:Pawn", "--dry-run")
    assert code == 1
    assert "'Pawn' is ambiguous" in err and "Demo.Meta.Pawn" in err and "Demo.Arena.Pawn" in err
    assert ncg("new", "Assets/Graphs/N.asset", "--kind", "bt", "--var", "Target:Demo.Arena.Pawn")[0] == 0
    assert "$Target: Demo.Arena.Pawn" in ncg("vars", "N")[1]


def test_partial_qualifier_is_enough(ncg, pawns):
    assert ncg("new", "Assets/Graphs/N.asset", "--kind", "bt", "--var", "T:Arena.Pawn")[0] == 0
    assert "$T: Demo.Arena.Pawn" in ncg("vars", "N")[1]


@pytest.mark.parametrize(
    "op",
    [
        {"op": "var-add", "name": "X", "type": "Pawn"},
        {"op": "var-add", "name": "X", "type": "List<Pawn>"},
        {"op": "var-add", "name": "X", "type": "Pawn[]"},
        {"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"$type": "SetVariable<Pawn>"}}},
        {"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"call": "Pawn.Hit"}}},
    ],
)
def test_ambiguous_everywhere_a_person_names_a_type(ncg, pawns, op):
    code, _, err = _apply(ncg, pawns, "Bot_BT", [op], "--dry-run")
    assert code == 1 and "ambiguous" in err and "Demo.Meta.Pawn" in err


def test_a_wrong_qualifier_is_not_ignored(ncg, pawns):
    code, _, err = _apply(ncg, pawns, "Bot_BT", [{"op": "var-add", "name": "X", "type": "Wrong.Ns.Pawn"}], "--dry-run")
    assert code == 1 and "no type 'Wrong.Ns.Pawn'" in err and "Demo.Arena.Pawn" in err


def test_an_unknown_short_name_is_an_error_with_suggestions(ncg, pawns):
    code, _, err = _apply(ncg, pawns, "Bot_BT", [{"op": "var-add", "name": "X", "type": "Heroo"}], "--dry-run")
    assert code == 1 and "'Heroo' not found" in err and "did you mean Hero" in err


def test_full_names_outside_the_sources_stay_allowed(ncg, pawns):
    ops = [
        {"op": "var-add", "name": "T", "type": "TMPro.TMP_Text"},
        {"op": "var-add", "name": "U", "type": "UnityEngine.AI.NavMeshAgent"},
    ]
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0


def test_a_project_type_shadowing_a_unity_one_is_ambiguous(ncg, project):
    (project / "Assets/Game/Mine.cs").write_text("namespace Mine { public class Transform { } }")
    code, _, err = _apply(ncg, project, "Bot_BT", [{"op": "var-add", "name": "X", "type": "Transform"}], "--dry-run")
    assert code == 1 and "UnityEngine.Transform" in err and "Mine.Transform" in err
    ops = [{"op": "var-add", "name": "X", "type": "UnityEngine.Transform"}]
    assert _apply(ncg, project, "Bot_BT", ops)[0] == 0


def test_scaffold_agent_and_members_do_not_guess(ncg, pawns):
    code, _, err = ncg("scaffold", "action", "Foo", "--dir", "Assets/Game", "--agent", "Pawn", "--dry-run")
    assert code == 1 and "ambiguous" in err
    code, out, _ = ncg("members", "Pawn")
    assert code == 1 and "ambiguous" in out
    assert "Hit" in ncg("members", "Arena.Pawn")[1]


def test_rename_member_does_not_touch_both_types(ncg, pawns):
    ops = [
        {"op": "var-add", "name": "M", "type": "Demo.Meta.Pawn"},
        {"op": "var-add", "name": "A", "type": "Demo.Arena.Pawn"},
        {"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"call": "Meta.Pawn.Greet"}}},
        {"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"call": "Arena.Pawn.Hit"}}},
    ]
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0
    (pawns / "Assets/Game/Pawns.cs").write_text(PAWNS.replace("Hit()", "Greet()"))
    ops = [{"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"call": "Arena.Pawn.Greet"}}}]
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0
    code, _, err = ncg("refactor", "rename-member", "Pawn.Greet", "Salute", "--dry-run")
    assert code == 1 and "ambiguous" in err and "Demo.Arena.Pawn" in err


def test_node_output_can_be_applied_back(ncg, pawns):
    ops = [
        {"op": "var-add", "name": "A", "type": "Arena.Pawn"},
        {
            "op": "add",
            "parent": "0",
            "id": "n",
            "node": {"$type": "ActionNode", "_action": {"$type": "SetVariable<Arena.Pawn>", "valueA": "$A"}},
        },
        {"op": "add", "parent": "0", "id": "c", "node": {"$type": "ActionNode", "_action": {"call": "Arena.Pawn.Hit"}}},
    ]
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0
    tree = ncg("show", "Bot_BT")[1]
    nid = next(line for line in tree.splitlines() if "Hit" in line).split("#")[1].split()[0]
    spec = json.loads(ncg("node", "Bot_BT", nid)[1].splitlines()[0].split(" ", 1)[1])
    assert spec["_action"]["call"] == "Demo.Arena.Pawn.Hit"
    nid = next(line for line in tree.splitlines() if re.search(r"\$A\b", line)).split("#")[1].split()[0]
    spec = json.loads(ncg("node", "Bot_BT", nid)[1].splitlines()[0].split(" ", 1)[1])
    assert spec["_action"]["$type"] == "SetVariable<Demo.Arena.Pawn>"
    assert _apply(ncg, pawns, "Bot_BT", [{"op": "add", "parent": "0", "node": spec}], "--dry-run")[0] == 0


# ---------------------------------------------------------------------- C: relation


@pytest.mark.parametrize(
    "src,dst,rel,level",
    [
        ("Demo.Arena.Pawn", "Demo.Arena.Pawn", "SAME", None),
        ("Demo.Arena.Hero", "Demo.Arena.Pawn", "ASSIGNABLE", None),
        ("Demo.Arena.Dummy", "Demo.Arena.ITarget", "ASSIGNABLE", None),
        ("Demo.Arena.Pawn", "UnityEngine.MonoBehaviour", "ASSIGNABLE", None),
        ("Demo.Arena.Pawn", "UnityEngine.Component", "ASSIGNABLE", None),
        ("Demo.Arena.Pawn", "System.Object", "ASSIGNABLE", None),
        ("System.Int32", "System.Single", "WIDENS", None),
        ("Demo.Arena.Pawn", "System.String", "WIDENS", None),
        ("System.Boolean", "System.Single", "CONVERTS", "warning"),
        ("System.Single", "System.Int32", "CONVERTS", "warning"),
        ("UnityEngine.Vector3", "UnityEngine.Vector2", "CONVERTS", "warning"),
        ("UnityEngine.GameObject", "Demo.Arena.Pawn", "LOOKUP", "info"),
        ("Demo.Arena.Pawn", "UnityEngine.GameObject", "LOOKUP", "info"),
        ("UnityEngine.Transform", "Demo.Arena.Pawn", "LOOKUP", "info"),
        ("Demo.Arena.Pawn", "UnityEngine.Vector3", "LOOKUP", "info"),
        ("Demo.Arena.Pawn", "Demo.Arena.ITarget", "LOOKUP", "info"),
        ("Demo.Arena.Pawn", "Demo.Arena.Hero", "LOOKUP", "info"),
        ("Demo.Meta.Pawn", "Demo.Arena.Pawn", "UNRELATED", "error"),
        ("Demo.Arena.Pawn", "Demo.Arena.Health", "UNRELATED", "warning"),
        ("Demo.Arena.Plain", "Demo.Arena.Other", "NONE", "error"),
        ("Demo.Arena.Plain", "Demo.Arena.Pawn", "NONE", "error"),
        ("UnityEngine.Vector3", "UnityEngine.Color", "NONE", "error"),
        ("System.Single", "Demo.Arena.Pawn", "NONE", "error"),
        (
            "System.Collections.Generic.List`1[[Demo.Arena.Pawn, A]]",
            "System.Collections.Generic.List`1[[Demo.Meta.Pawn, A]]",
            "NONE",
            "error",
        ),
        ("System.Collections.Generic.List`1[[Demo.Arena.Pawn, A]]", "IList", "UNKNOWN", None),
        ("TMPro.TMP_Text", "Demo.Arena.Pawn", "UNKNOWN", None),
    ],
)
def test_relation(ncg, pawns, src, dst, rel, level):
    from nodecanvas_cli.typerel import Rel, relation, severity
    from nodecanvas_cli.workspace import Workspace

    schema = Workspace(pawns).schema
    got = relation(schema, src, dst)
    assert got is Rel[rel]
    assert severity(got, src, dst) == level


def test_base_outside_the_sources_is_not_treated_as_known(ncg, project):
    (project / "Assets/Game/Net.cs").write_text(
        "namespace N1 { public class Pawn : Fusion.NetworkBehaviour { } "
        "public class Item : Fusion.NetworkBehaviour { } }"
        "namespace N2 { public class Pawn : Fusion.NetworkBehaviour { } }"
    )
    from nodecanvas_cli.typerel import Rel, relation
    from nodecanvas_cli.workspace import Workspace

    schema = Workspace(project).schema
    assert relation(schema, "N1.Pawn", "N2.Pawn") is Rel.UNRELATED
    assert relation(schema, "N1.Pawn", "N1.Item") is Rel.UNRELATED
    assert relation(schema, "N1.Pawn", "UnityEngine.Vector3") is Rel.UNKNOWN


# ---------------------------------------------------------------------- B, D: messages, contracts


def _with_sub_var(ncg, project, sub_type, parent_type, arrow="<-"):
    """Sub_BT gets a public $Target; Bot_BT #8 (a SubTree of it) maps it to a parent variable $Target."""
    ops = [{"op": "var-add", "name": "Target2", "type": sub_type, "public": True}]
    assert _apply(ncg, project, "Sub_BT", ops, "--force")[0] == 0
    ops = [
        {"op": "var-add", "name": "Parent2", "type": parent_type},
        {"op": "map", "node": "8", "map": {"Target2": f"{arrow}$Parent2"}},
    ]
    return ops


def test_mapping_between_two_pawns_is_an_error_with_full_names(ncg, pawns):
    ops = _with_sub_var(ncg, pawns, "Demo.Arena.Pawn", "Demo.Meta.Pawn")
    code, out, err = _apply(ncg, pawns, "Bot_BT", ops)
    assert code == 1 and "not saved" in err
    assert "mapping-type-mismatch" in out
    assert "Sub_BT.$Target2 (Demo.Arena.Pawn) is mapped to $Parent2 (Demo.Meta.Pawn)" in out
    assert "GetComponent" in out
    assert _apply(ncg, pawns, "Bot_BT", ops, "--force")[0] == 0
    issue = next(i for i in _lint(ncg, "Bot_BT") if i["code"] == "mapping-type-mismatch")
    assert issue["severity"] == "error"


def test_mapping_of_unrelated_components_with_different_names_warns(ncg, pawns):
    ops = _with_sub_var(ncg, pawns, "Demo.Arena.Health", "Demo.Arena.Pawn")
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0
    issue = next(i for i in _lint(ncg, "Bot_BT") if i["code"] == "mapping-type-mismatch")
    assert issue["severity"] == "warning"


def test_mapping_checks_each_direction(ncg, pawns):
    # a subclass goes in unchanged; read back, the base has to be looked up
    ops = _with_sub_var(ncg, pawns, "Demo.Arena.Pawn", "Demo.Arena.Hero")
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0
    assert not [i for i in _lint(ncg, "Bot_BT") if i["code"] == "mapping-type-mismatch"]
    ops = [{"op": "map", "node": "8", "map": {"Target2": "<->$Parent2"}}]
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0
    issue = next(i for i in _lint(ncg, "Bot_BT") if i["code"] == "mapping-type-mismatch")
    assert issue["severity"] == "info"


def test_generic_task_against_the_bound_variable(ncg, pawns):
    ops = [
        {"op": "var-add", "name": "M", "type": "Demo.Meta.Pawn"},
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ActionNode", "_action": {"$type": "SetVariable<Demo.Arena.Pawn>", "valueA": "$M"}},
        },
    ]
    code, out, err = _apply(ncg, pawns, "Bot_BT", ops)
    assert code == 1 and "not saved" in err and "param-type-mismatch" in out
    assert "stores Demo.Arena.Pawn into $M (Demo.Meta.Pawn)" in out
    ops[1]["node"]["_action"]["$type"] = "SetVariable<Demo.Meta.Pawn>"
    assert _apply(ncg, pawns, "Bot_BT", ops)[0] == 0
    assert not [i for i in _lint(ncg, "Bot_BT") if i["code"] == "param-type-mismatch"]


def test_generic_parameter_through_a_base_class(ncg, project):
    (project / "Assets/Game/Gen.cs").write_text(
        """
using NodeCanvas.Framework;
namespace Demo.Gen
{
    public class Item { }
    public class Thing { }
    public abstract class Holder<T> : ActionTask { public BBParameter<T> value; }
    public class ItemHolder : Holder<Item> { }
}
"""
    )
    ops = [
        {"op": "var-add", "name": "T", "type": "Demo.Gen.Thing"},
        {
            "op": "add",
            "parent": "0",
            "node": {"$type": "ActionNode", "_action": {"$type": "ItemHolder", "value": "$T"}},
        },
    ]
    code, out, _ = _apply(ncg, project, "Bot_BT", ops)
    assert code == 1
    assert "ItemHolder.value expects Item, bound to $T (Thing): no conversion exists" in out


def test_reflection_argument_between_two_pawns(ncg, pawns):
    (pawns / "Assets/Game/Pawns2.cs").write_text(
        "using UnityEngine; namespace Demo.Arena { public class Boss : MonoBehaviour { public void Hit(Pawn p) { } } }"
    )
    ops = [
        {"op": "var-add", "name": "M", "type": "Demo.Meta.Pawn"},
        {"op": "add", "parent": "0", "node": {"$type": "ActionNode", "_action": {"call": "Boss.Hit", "args": ["$M"]}}},
    ]
    code, out, _ = _apply(ncg, pawns, "Bot_BT", ops)
    assert code == 1 and "arg-type-mismatch" in out
    assert "$M (Demo.Meta.Pawn) but the parameter takes Demo.Arena.Pawn" in out


def test_vars_and_show_name_the_pawn(ncg, pawns):
    assert _apply(ncg, pawns, "Bot_BT", [{"op": "var-add", "name": "P", "type": "Arena.Pawn"}])[0] == 0
    assert "$P: Demo.Arena.Pawn" in ncg("vars", "Bot_BT")[1]
    assert "P:Demo.Arena.Pawn" in ncg("show", "Bot_BT")[1]
