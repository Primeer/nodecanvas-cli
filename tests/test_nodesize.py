"""Estimated node sizes. Expected texts follow the C# ``info`` properties; sizes come from real ``node.rect`` values."""

import pytest

from nodecanvas_cli.nodesize import NodeSizer, TextMetrics, line_width, text_width
from nodecanvas_cli.workspace import Workspace

BT = "NodeCanvas.BehaviourTrees."
ACTIONS = "NodeCanvas.Tasks.Actions."
CONDITIONS = "NodeCanvas.Tasks.Conditions."
VAR = {"_name": "Target", "_targetVariableID": "x"}
FLAG = {"_name": "Alert", "_targetVariableID": "y"}
METHOD = {"_baseInfo": "Game.Bot|Attack|System.Void", "_paramsInfo": "Game.Unit"}
GETTER = {"_baseInfo": "Game.Bot|get_IsDead|System.Boolean"}


@pytest.fixture
def sizer(ncg, project):
    ws = Workspace()
    g = ws.graph("Bot_BT")
    return NodeSizer(g, ws.renderer(g))


@pytest.mark.parametrize(
    "task,expected",
    [
        (
            {"$type": ACTIONS + "ExecuteFunction_Multiplatform", "method": METHOD, "parameters": [VAR]},
            "<b>Self</b>.Attack(<b>$Target</b>)",
        ),
        ({"$type": ACTIONS + "SetBoolean", "boolVariable": FLAG}, "Set <b>$Alert</b> to True"),
        ({"$type": ACTIONS + "SetBoolean", "boolVariable": FLAG, "setTo": 2}, "Toggle <b>$Alert</b>"),
        ({"$type": ACTIONS + "Wait", "waitTime": {"_value": 2.0}}, "Wait <b>2</b> sec."),
        (
            {"$type": ACTIONS + "SetFloat", "valueA": VAR, "valueB": {"_value": 1.5}, "perSecond": True},
            "<b>$Target</b>  =  <b>1.5</b> Per Second",
        ),
        (
            {
                "$type": ACTIONS + "ExecuteFunction_Multiplatform",
                "method": METHOD,
                "overrideAgent": {"_name": "Owner", "_targetVariableID": "z"},
            },
            "* <b>$Owner</b>.Attack()",
        ),
        (
            {"$type": CONDITIONS + "CheckProperty_Multiplatform", "method": GETTER, "checkValue": {"_value": False}},
            "If <b>Self</b>.get_IsDead == <b>False</b>",
        ),
        (
            {"$type": CONDITIONS + "CheckBoolean", "valueA": FLAG, "valueB": {"_value": True}, "_invert": True},
            "If <b>!</b> <b>$Alert</b> == <b>True</b>",
        ),
    ],
)
def test_summary_follows_task_info(sizer, task, expected):
    assert sizer.summary(task) == expected


def test_summary_of_lists_has_one_line_per_enabled_task(sizer):
    wait = {"$type": ACTIONS + "Wait", "waitTime": {"_value": 1.0}}
    off = dict(wait, _isDisabled=True)
    text = sizer.summary({"$type": "NodeCanvas.Framework.ActionList", "actions": [wait, off, wait]})
    assert text == "<b>(In Sequence)</b>\n▪Wait <b>1</b> sec.\n▪Wait <b>1</b> sec."
    assert sizer.summary({"$type": "NodeCanvas.Framework.ActionList", "actions": []}) == "No Actions"
    check = {"$type": CONDITIONS + "CheckBoolean", "valueA": FLAG, "valueB": {"_value": True}}
    text = sizer.summary({"$type": "NodeCanvas.Framework.ConditionList", "conditions": [check]})
    assert text == "If ▪If <b>$Alert</b> == <b>True</b>"


def test_unknown_task_uses_the_compact_text(sizer):
    assert "Teleport" in sizer.summary({"$type": "Game.Teleport"})


@pytest.mark.parametrize(
    "node,expected",
    [
        ({"$type": BT + "Sequencer"}, (80, 34)),  # icon only
        ({"$type": BT + "Cooldown"}, (80, 59)),  # space for the progress bar
        ({"$type": BT + "ActionNode"}, (80, 59)),  # "No Task" box
        ({"$type": BT + "Sequencer", "dynamic": True, "random": True}, (80, 72)),
        ({"$type": BT + "Repeater", "repeaterMode": 2, "repeatTimes": {"_value": 1}}, (99, 53)),
    ],
)
def test_size_of_builtin_nodes_matches_the_editor(sizer, node, expected):
    w, h = sizer.size(node)
    assert abs(w - expected[0]) <= 3 and abs(h - expected[1]) <= 1


def test_long_task_text_widens_the_node_and_lines_make_it_taller(sizer):
    short = {"$type": BT + "ActionNode", "_action": {"$type": ACTIONS + "Wait", "waitTime": {"_value": 1.0}}}
    long_call = {
        "$type": ACTIONS + "ExecuteFunction_Multiplatform",
        "method": {"_baseInfo": "Game.Bot|" + "CalculateCirclePosition" * 3 + "|System.Void"},
    }
    wide = {"$type": BT + "ActionNode", "_action": long_call}
    listed = {
        "$type": BT + "ActionNode",
        "_action": {"$type": "NodeCanvas.Framework.ActionList", "actions": [long_call] * 2},
    }
    assert sizer.size(wide)[0] > sizer.size(short)[0] + 300
    assert sizer.size(wide)[1] == sizer.size(short)[1]
    assert sizer.size(listed)[1] > sizer.size(wide)[1] + 2 * 14


def test_character_classes():
    m = TextMetrics()
    assert line_width("MMMM", m) > line_width("mmmm", m) > line_width("iiii", m)
    assert line_width("<b>mmmm</b>", m) > line_width("mmmm", m)
    assert text_width("ab\nabcd", m) == line_width("abcd", m)
    assert line_width("<color=#ff0000>ab</color>", m) == line_width("ab", m)


def test_char_width_scales_the_text():
    assert line_width("abcd", TextMetrics(char_width=8.0)) == 2 * line_width("abcd", TextMetrics(char_width=4.0))
