"""Estimated size of a node in the NodeCanvas editor, without Unity.

The editor never saves a node's size: ``GUILayout.Window`` shrinks the window around its content on every
repaint (``Node.ShowNodeGUI``), and labels do not wrap. So a node is as wide as its longest line and as tall as
its stacked blocks: title (FSM and dialogue nodes), icon, the labels of ``OnNodeGUI``, and rounded boxes with
the task summary or the sub-graph.

``summary`` follows ``Task.summaryInfo`` and the ``info`` property of the tasks NodeCanvas ships; a task it does
not know falls back to the compact text of ``Renderer.task``. Constants were fitted against real ``node.rect``
values (Unity 6000.2, NodeCanvas 3.x); ``nodecanvas-cli calibrate --unity`` adjusts the character width.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .model import Graph
from .render import OPERATION, Renderer
from .schema import simple_name

Size = Tuple[float, float]
MIN_SIZE: Size = (80.0, 20.0)  # Node.MIN_SIZE
TAG = re.compile(r"</?(?:b|i|color(?:=[^>]*)?|size(?:=[^>]*)?)>")
MARK = re.compile(r"(</?b>)")
NARROW = set(" ilj.,:;'!|()[]")
# Width of a character relative to a regular lowercase one: (lowercase, uppercase, narrow), regular and bold.
REGULAR = (1.0, 1.57, 0.72)
BOLD = (1.10, 1.56, 0.83)


@dataclass(frozen=True)
class TextMetrics:
    char_width: float = 5.7  # regular lowercase characters; the only value `calibrate` changes
    line_height: float = 14.5
    box_pad_x: float = 18.0  # rounded box with a label: margins and padding around the text
    box_pad_y: float = 10.5
    label_pad_x: float = 12.0  # bare label
    label_pad_y: float = 4.5
    title_pad_x: float = 4.0
    title_height: float = 27.0
    icon_height: float = 34.0
    spacer_height: float = 25.0  # GUILayout.Space(25) above the progress bar of Cooldown and Timeout
    button: Size = (103.0, 21.0)  # "CREATE NEW" under an unassigned sub-graph


def text_lines(text: str) -> List[str]:
    return text.split("\n")


def line_width(line: str, m: TextMetrics) -> float:
    width, factors = 0.0, REGULAR
    for part in MARK.split(line):
        if part == "<b>":
            factors = BOLD
        elif part == "</b>":
            factors = REGULAR
        else:
            for ch in TAG.sub("", part):
                width += m.char_width * (factors[1] if ch.isupper() else factors[2] if ch in NARROW else factors[0])
    return width


def text_width(text: str, m: TextMetrics) -> float:
    return max(line_width(line, m) for line in text_lines(text))


def bold(text: str) -> str:
    return f"<b>{text}</b>"


GENERIC_DEFAULTS = {
    "Boolean": "False",
    "Single": "0",
    "Double": "0",
    "Int32": "0",
    "Vector2": "(0.00, 0.00)",
    "Vector3": "(0.00, 0.00, 0.00)",
    "Vector4": "(0.00, 0.00, 0.00, 0.00)",
}


def _generic_default(task_type: str) -> str:
    """What a parameter left at ``default(T)`` prints in a generic task such as ``CheckVariable<Vector3>``."""
    match = re.search(r"\[\[([^,\]]+)", task_type)
    return GENERIC_DEFAULTS.get(simple_name(match.group(1)), "NULL") if match else "NULL"


def _unity_value(s: str, task_type: str = "") -> str:
    """The compact form of a literal, written the way ``BBParameter.ToString`` prints it."""
    if s.startswith("$"):
        return s.rstrip("!")
    if s in ("true", "false"):
        return s.capitalize()
    if s in ("null", "∅", "default"):
        return _generic_default(task_type)
    if re.fullmatch(r"-?\d+\.0", s):
        return s[:-2]
    match = re.fullmatch(r"\(([-\d., e]+)\)", s)
    if match:
        return "(" + ", ".join(f"{float(x):.2f}" for x in match.group(1).split(",")) + ")"
    return s


# Node types that have no [Icon] attribute, by graph kind.
NO_ICON = {
    "fsm": {"ActionState", "AnyState", "ConcurrentSubFSM", "ConcurrentSubTree", "EmptyState", "SuperActionState"},
    "dt": {"ActionNode", "GoToNode", "StatementNode"},
}
# ITaskAssignable nodes: they show a box even when no task is assigned.
TASK_NODES = {
    "ActionNode": "No Task",
    "ConditionNode": "No Task",
    "BinarySelector": "No Task",
    "ConditionalEvaluator": "No Task",
    "Interruptor": "No Task",
    "Monitor": "No Task",
    "WaitUntil": "No Task",
    "ActionState": "No Actions",
    "ConcurrentState": "No Actions",
    "OnFSMEnter": "No Actions",
    "OnFSMExit": "No Actions",
    "OnFSMUpdate": "No Actions",
}
TASK_FIELDS = ("_condition", "_action", "_conditionList", "_actionList")
SUBGRAPH_FIELDS = {
    "_subTree": "BehaviourTree",
    "_nestedBT": "BehaviourTree",
    "_nestedFSM": "FSM",
    "_subFSM": "FSM",
    "_nestedDLG": "DialogueTree",
    "_subDialogue": "DialogueTree",
}
SUBGRAPH_NODES = {
    "SubTree": "_subTree",
    "NestedBTState": "_nestedBT",
    "ConcurrentSubTree": "_subTree",
    "NestedFSM": "_nestedFSM",
    "NestedFSMState": "_nestedFSM",
    "ConcurrentSubFSM": "_subFSM",
    "NestedDT": "_nestedDLG",
    "NestedDTState": "_nestedDLG",
    "SubDialogueTree": "_subDialogue",
}
PARALLEL_POLICY = ("First Failure", "First Success", "First Success Or Failure")
MONITOR_MODE = ("Failure", "Success", "Running", "Resting", "Any")
TERMINATION = (None, "FirstSuccess", "FirstFailure")


def split_camel(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


class NodeSizer:
    def __init__(self, g: Graph, renderer: Renderer, metrics: Optional[TextMetrics] = None):
        self.g = g
        self.r = renderer
        self.m = metrics or TextMetrics()

    # ------------------------------------------------------------------ task text

    def _value(self, task: dict, field: str, t: str) -> str:
        return bold(_unity_value(self.r._f(task, field, t), t))

    def _param(self, p, ftype: Optional[str] = None) -> str:
        return bold(_unity_value(self.r.param(p, ftype) if isinstance(p, dict) else self.r.literal(p)))

    def _agent(self, task: dict) -> str:
        oa = task.get("overrideAgent")
        if isinstance(oa, dict) and (oa.get("_name") or oa.get("_targetVariableID")):
            return bold(self.r.var(oa.get("_name"), oa.get("_targetVariableID")).rstrip("!"))
        return bold("Self") if not isinstance(oa, dict) else self._param(oa)

    @staticmethod
    def _member(task: dict, key: str = "method") -> str:
        parts = ((task.get(key) or {}).get("_baseInfo") or "?").split("|")
        return parts[1] if len(parts) > 1 else parts[0]

    def _args(self, task: dict) -> str:
        info = (task.get("method") or {}).get("_paramsInfo") or ""
        types = info.split("|") if info else []
        out = []
        for i, p in enumerate(task.get("parameters") or []):
            pt = types[i] if i < len(types) else None
            out.append(self._param(p, f"BBParameter<{pt}>" if pt else None))
        return ", ".join(out)

    def _check_value(self, task: dict, t: str) -> str:
        op = self.r._cmp(task, "comparison", t)
        cv = task.get("checkValue")
        return f" {op} " + (self._param(cv) if isinstance(cv, dict) else bold("NULL"))

    def _info(self, task: dict, t: str) -> Optional[str]:
        name = simple_name(t)
        v = lambda k: self._value(task, k, t)  # noqa: E731
        if name == "ExecuteFunction_Multiplatform":
            ret = task.get("returnValue")
            head = f"{self._param(ret)} = " if isinstance(ret, dict) and ret else ""
            return f"{head}{self._agent(task)}.{self._member(task)}({self._args(task)})"
        if name == "CheckFunction_Multiplatform":
            return f"{self._agent(task)}.{self._member(task)}({self._args(task)}){self._check_value(task, t)}"
        if name == "CheckProperty_Multiplatform":
            return f"{self._agent(task)}.{self._member(task)}{self._check_value(task, t)}"
        if name == "GetProperty_Multiplatform":
            return f"{self._param(task.get('returnValue') or {})} = {self._agent(task)}.{self._member(task)}"
        if name == "SetBoolean":
            mode = self.r._f(task, "setTo", t)
            target = v("boolVariable")
            return f"Toggle {target}" if mode == "Toggle" else f"Set {target} to {mode.capitalize()}"
        if name in ("SetFloat", "SetInt", "SetVector3"):
            key = "Operation" if ("Operation" in task or "Operation" in self.r._fields(t)) else "operation"
            op = OPERATION.get(self.r._f(task, key, t), "=")
            per = " Per Second" if task.get("perSecond") else ""
            return f"{v('valueA')}  {op}  {v('valueB')}{per}"
        if name in ("SetFloatRandom", "SetIntRandom"):
            target = "floatVariable" if name == "SetFloatRandom" else "intVariable"
            return f"Set {v(target)} Random({v('minValue')}, {v('maxValue')})"
        if name == "SetVariable":
            return f"{v('valueA')} = {v('valueB')}"
        if name in ("CheckBoolean", "CheckVariable", "CheckString", "CheckVector3"):
            return f"{v('valueA')} == {v('valueB')}"
        if name in ("CheckFloat", "CheckInt"):
            return f"{v('valueA')} {self.r._cmp(task, 'checkType', t)} {v('valueB')}"
        if name == "CheckNull":
            return f"{v('variable')} == null"
        if name == "Wait":
            return f"Wait {v('waitTime')} sec."
        if name == "AddElementToList":
            return f"Add {v('targetElement')} In {v('targetList')}"
        if name == "PickListElement":
            return f"{v('saveAs')} = {v('targetList')} [{v('index')}]"
        if name == "PickRandomListElement":
            return f"{v('saveAs')} = Random From {v('targetList')}"
        if name == "ClearList":
            return f"Clear List {v('targetList')}"
        if name == "GetListCount":
            return f"{v('saveAs')} = {v('targetList')}.Count"
        return None

    def summary(self, task: Optional[dict], condition: bool = False) -> str:
        """``Task.summaryInfo`` of a serialized task."""
        if not isinstance(task, dict):
            return "No Task"
        t = task.get("$type", "?")
        name = simple_name(t)
        if name == "ActionList":
            items = [a for a in task.get("actions") or [] if isinstance(a, dict) and not a.get("_isDisabled")]
            if not items:
                return "No Actions"
            head = [bold("(In Sequence)")] if len(task.get("actions") or []) > 1 else []
            return "\n".join(head + ["▪" + self.summary(a) for a in items])
        if name == "ConditionList":
            items = [c for c in task.get("conditions") or [] if isinstance(c, dict) and not c.get("_isDisabled")]
            if not items:
                return "If No Conditions"
            head = [bold("(ALL True)")] if len(task.get("conditions") or []) > 1 else []
            return "If " + "\n".join(head + ["▪" + self.summary(c, True) for c in items])
        try:
            info = self._info(task, t)
        except (KeyError, TypeError, ValueError):
            info = None
        if info is None:
            info = self.r.task(task)
        prefix = "* " if isinstance(task.get("overrideAgent"), dict) else ""
        if condition or ".Conditions." in t:
            prefix += "If <b>!</b> " if task.get("_invert") else "If "
        return prefix + info

    # ------------------------------------------------------------------ blocks

    def _label(self, text: str) -> Size:
        m = self.m
        return text_width(text, m) + m.label_pad_x, len(text_lines(text)) * m.line_height + m.label_pad_y

    def _box(self, text: str) -> Size:
        m = self.m
        return text_width(text, m) + m.box_pad_x, len(text_lines(text)) * m.line_height + m.box_pad_y

    def _title(self, node: dict, name: str) -> Size:
        m = self.m
        text = node.get("_name")
        if not text:
            decl = self.r._decl(node.get("$type", ""))
            text = (decl.attr_str("Name") if decl is not None else None) or split_camel(name)
        if self.g.kind == "dt":
            text = (node.get("_actorName") or "SELF").upper()
        if name in SUBGRAPH_NODES:
            text = "{ " + text + " }"
        return text_width(bold(text), m) + m.title_pad_x, m.title_height

    def _gui_labels(self, node: dict, name: str) -> List[Size]:
        """The fixed labels of ``OnNodeGUI``; a label depending on play mode is left out."""
        out: List[Size] = []
        flag = lambda *keys: any(node.get(k) for k in keys)  # noqa: E731
        field = lambda k: _unity_value(self.r._f(node, k, node.get("$type", "")))  # noqa: E731
        if name in ("Cooldown", "Timeout", "Filter"):
            out.append((0.0, self.m.spacer_height))
        elif name == "Setter":
            out.append(self._label(f"Self = {field('newAgent')}"))
        elif name == "Repeater":
            mode = node.get("repeaterMode", 0)
            if mode == 1:
                text = "Until Success"
            elif mode == 2:
                text = "Repeat Forever"
            else:
                text = f"{bold(field('repeatTimes'))} Times"
            out.append(self._label(text))
        elif name == "Iterator":
            # Styles.leftLabel is 11 px and tabs align both values
            cur, lst = bold(field("current")), bold(field("targetList"))
            size = max(line_width(cur, self.m), line_width(lst, self.m))
            out.append((size * 0.85 + 88.0, 2 * self.m.line_height + 2.0))
            term = node.get("terminationCondition", 0)
            if 0 < term < len(TERMINATION):
                out.append(self._label(f"Break on {TERMINATION[term]}"))
        elif name == "Monitor":
            mode = node.get("monitorMode", 0)
            text = MONITOR_MODE[mode] if isinstance(mode, int) and 0 <= mode < len(MONITOR_MODE) else "Failure"
            out.append(self._label(bold(f"[{text}]")))
        elif name == "Limiter":
            out.append(self._label(f"0 / {field('maxCount')}"))
        elif name == "Guard":
            out.append(self._label(bold(field("token"))))
        elif name == "Parallel":
            policy = node.get("policy", 0)
            text = PARALLEL_POLICY[policy] if isinstance(policy, int) and 0 <= policy < 3 else PARALLEL_POLICY[0]
            out.append(self._label((bold("REPEAT") + "\n" if node.get("dynamic") else "") + bold(text)))
        elif name == "Switch":
            if flag("dynamic"):
                out.append(self._label(bold("DYNAMIC")))
            out.append(self._label("Current = 0"))
        elif name == "ActionState":
            if flag("repeatStateActions"):
                out.append(self._label(bold("[REPEAT]")))
        elif name == "AnyState":
            if flag("dontRetriggerStates"):
                out.append(self._label(bold("[NO RETRIGGER]")))
        elif name == "SuperActionState":
            for key, title in (("_onEnterList", "OnEnter"), ("_onUpdateList", "OnUpdate"), ("_onExitList", "OnExit")):
                actions = (node.get(key) or {}).get("actions") or []
                out.append(self._box(f"{len(actions)} {title} Actions"))
        elif name == "StatementNode":
            text = ((node.get("statement") or {}).get("_text") or "")[:30]
            out.append(self._box(f'" {text} "'))
        elif name == "MultipleChoiceNode":
            choices = node.get("availableChoices") or []
            if not choices:
                out.append(self._label("No Options Available"))
            for choice in choices:
                text = ((choice.get("statement") or {}).get("_text") or "")[:30]
                out.append(self._box(f"□ {text}"))
        elif name == "FinishNode":
            out.append(self._label(bold("Failure")))
        elif name == "Jumper":
            out.append(self._label(bold("NONE")))
        else:
            if flag("dynamic", "isDynamic"):
                out.append(self._label(bold("DYNAMIC")))
            if flag("random"):
                out.append(self._label(bold("RANDOM")))
        return out

    def _subgraph(self, node: dict, name: str) -> Optional[Size]:
        key = SUBGRAPH_NODES.get(name) or next((k for k in SUBGRAPH_FIELDS if k in node), None)
        if key is None:
            return None
        param = node.get(key)
        target = self.r.nested_target(node, param) if isinstance(param, dict) else "null"
        w, h = self._box(f"Sub{SUBGRAPH_FIELDS[key]}\n" + bold(_unity_value(target)))
        if target == "null":
            return max(w, self.m.button[0]), h + self.m.button[1]
        return w, h

    # ------------------------------------------------------------------ the node

    def size(self, node: dict) -> Size:
        m, kind = self.m, self.g.kind
        name = simple_name(node.get("$type", ""))
        blocks: List[Size] = []
        if kind != "bt":
            blocks.append(self._title(node, name))
        if name not in NO_ICON.get(kind, ()):
            blocks.append((52.0, m.icon_height))
        blocks.extend(self._gui_labels(node, name))
        tasks = [k for k in TASK_FIELDS if isinstance(node.get(k), dict)]
        for k in tasks:
            blocks.append(self._box(self.summary(node[k], k in ("_condition", "_conditionList"))))
        if not tasks and name in TASK_NODES:
            blocks.append(self._box(TASK_NODES[name]))
        sub = self._subgraph(node, name)
        if sub is not None:
            blocks.append(sub)
        width = max((b[0] for b in blocks), default=0.0)
        height = sum(b[1] for b in blocks)
        return max(math.ceil(width), MIN_SIZE[0]), max(math.ceil(height), MIN_SIZE[1])


def estimate_sizes(g: Graph, renderer: Renderer, metrics: Optional[TextMetrics] = None) -> Dict[int, Size]:
    """Estimated size of every node of ``g``, by ``id(node)``."""
    sizer = NodeSizer(g, renderer, metrics)
    out: Dict[int, Size] = {}
    for n in g.nodes:
        try:
            out[id(n)] = sizer.size(n)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            out[id(n)] = MIN_SIZE  # an unexpected shape must not stop an edit: the size is only a hint
    return out


def metrics_of(ws) -> TextMetrics:
    width = ws.config.layout.char_width
    return TextMetrics(char_width=width) if width else TextMetrics()


def workspace_sizes(ws, g: Graph) -> Dict[int, Size]:
    return estimate_sizes(g, ws.renderer(g), metrics_of(ws))
