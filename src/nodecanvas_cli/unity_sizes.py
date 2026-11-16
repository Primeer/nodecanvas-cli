"""Node sizes from the open Unity Editor: ``layout --unity`` and ``calibrate --unity``.

``apply`` always estimates sizes (``nodesize``), so an edit gives the same result on any machine and needs no Editor.
These two commands ask for exact sizes on purpose, and fail when the Editor is not there: a silent fallback to the
estimate would make the result depend on whether Unity happened to be open.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import CONFIG_NAME
from .container import ContainerError, Slot
from .model import Graph, GraphError
from .nodesize import NodeSizer, Size, TextMetrics, metrics_of
from .ops import apply_ops
from .schema import simple_name
from .verify import Editor, UnityFailed, UnityUnavailable, _cs, _find, _run_snippet, unity_executable

SNIPPET = Path(__file__).with_name("unity_sizes.cs.txt")
Row = List[Optional[Size]]


class SizesFailed(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


def build_snippet(paths: List[str]) -> str:
    return SNIPPET.read_text(encoding="utf-8").replace("/*TARGETS*/", ", ".join(_cs(p) for p in paths))


def parse_report(doc) -> dict:
    def is_report(k, v):
        if isinstance(v, dict) and v.get("nodecanvas_cli_sizes") == 1:
            return True
        return isinstance(v, str) and v.lstrip().startswith('{"nodecanvas_cli_sizes"')

    found = _find(doc, is_report)
    if found is None:
        raise UnityFailed("the Editor returned no size report")
    report = json.loads(found) if isinstance(found, str) else found
    if report.get("error"):
        raise UnityFailed(report["error"])
    return report


def measure(ws, slots: List[Slot], timeout: float) -> Tuple[str, Dict[str, Row]]:
    """Unity's version and the size of every node of ``slots``, by asset path; ``None`` for a node it did not draw."""
    exe = unity_executable()
    if exe is None:
        raise SizesFailed("the Unity CLI (`unity`) is not on PATH; install it or set NODECANVAS_CLI_UNITY")
    editor = Editor(exe, ws.project.root, timeout)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    try:
        editor.connect(timeout, log)
    except UnityFailed as e:
        raise SizesFailed(str(e)) from e
    except UnityUnavailable as e:
        raise SizesFailed(
            f"no Unity Editor with the Pipeline package is reachable for {ws.project.root}: {e}\n"
            "  open the project in Unity, or drop --unity to use estimated sizes"
        ) from e
    deadline = time.monotonic() + timeout
    try:
        editor.wait_ready(timeout, log)
        fd, tmp = tempfile.mkstemp(prefix="nodecanvas_cli_sizes_", suffix=".cs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(build_snippet([s.path for s in slots]))
            report = parse_report(_run_snippet(editor, tmp, timeout, deadline, log))
        finally:
            os.unlink(tmp)
    except UnityUnavailable as e:
        raise SizesFailed(f"the Unity Editor stopped answering: {e}") from e
    except UnityFailed as e:
        raise SizesFailed(str(e), 1) from e
    rows: Dict[str, Row] = {}
    for r in report.get("graphs", []):
        if r.get("status") == "ok":
            rows[r["path"]] = [tuple(v) if v else None for v in r.get("nodes", [])]
    return report.get("unity", "?"), rows


# ---------------------------------------------------------------------------- layout


def _measured_nodes(ws, g: Graph, timeout: float) -> Dict[int, Size]:
    slot = g.loaded.slot
    if slot.bound:
        raise SizesFailed(f"{slot.address}: --unity works on .asset graphs for now; drop it to use estimated sizes")
    _, rows = measure(ws, [slot], timeout)
    row = rows.get(slot.path)
    if row is None:
        raise SizesFailed(f"Unity could not load {slot.path} as a NodeCanvas graph", 1)
    if len(row) != len(g.nodes):
        raise SizesFailed(
            f"Unity sees {len(row)} nodes in {slot.path}, the file has {len(g.nodes)}: "
            "save or discard the edits open in the Editor and try again",
            1,
        )
    return {id(n): size for n, size in zip(g.nodes, row) if size is not None}


def cmd_layout(ws, a) -> int:
    g = ws.graph(a.graph, a.kind)
    if g.kind != "bt":
        print("error: layout tidies Behaviour Trees; FSM and dialogue nodes are placed one by one", file=sys.stderr)
        return 2
    measured = None
    if a.unity:
        if ws.config.layout.sizing != "content":
            print(f'error: --unity needs "sizing": "content" in {CONFIG_NAME}', file=sys.stderr)
            return 2
        try:
            measured = _measured_nodes(ws, g, a.timeout)
        except SizesFailed as e:
            print(f"error: {e}", file=sys.stderr)
            return e.code
    ed = apply_ops(ws, g, [{"op": "layout", "node": a.node}], measured)
    summary = ed.layout_summary()
    print(summary or "layout: no node moved")
    for note in ed.notes:
        print(note)
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    ws.save(g)
    print(f"saved {g.loaded.slot.address}")
    return 0


# ---------------------------------------------------------------------------- calibrate


def _pct(values: List[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def _describe(errors: List[float]) -> str:
    magnitudes = [abs(e) for e in errors]
    return (
        f"mean {sum(magnitudes) / len(magnitudes):.1f} px, p90 {_pct(magnitudes, 0.9):.0f} px, "
        f"max {max(magnitudes):.0f} px, too narrow by over 40 px: {sum(e < -40 for e in errors)}"
    )


def cmd_calibrate(ws, a) -> int:
    if not a.unity:
        print("error: calibrate compares estimates with the Unity Editor; pass --unity", file=sys.stderr)
        return 2
    slots = [ws.find(q, a.kind) for q in a.graphs] if a.graphs else [s for s in ws.slots if not s.bound]
    slots = [s for s in slots if not s.bound]
    if not slots:
        print("error: no .asset graphs to measure", file=sys.stderr)
        return 2
    try:
        unity, rows = measure(ws, slots, a.timeout)
    except SizesFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code

    base = metrics_of(ws)
    probe = TextMetrics(char_width=base.char_width + 1.0)
    samples: List[Tuple[str, float, float, float]] = []  # node type, real width, estimate, px per unit of char_width
    for s in slots:
        row = rows.get(s.path)
        try:
            g = ws.load(s)
        except (GraphError, ContainerError):
            continue
        if row is None or len(row) != len(g.nodes):
            continue
        renderer = ws.renderer(g)
        now, then = NodeSizer(g, renderer, base), NodeSizer(g, renderer, probe)
        for n, real in zip(g.nodes, row):
            if real is None:
                continue
            est = now.size(n)[0]
            samples.append((simple_name(n.get("$type", "")), real[0], est, then.size(n)[0] - est))
    if not samples:
        print(
            "error: Unity drew no nodes: show the NodeCanvas window (not a hidden tab) and try again", file=sys.stderr
        )
        return 1

    # Least squares over the nodes whose width comes from text; nodes at the minimum width do not depend on it.
    text = [x for x in samples if x[1] > 80 and x[3] > 0]
    denom = sum(x[3] ** 2 for x in text)
    delta = sum(x[3] * (x[1] - x[2]) for x in text) / denom if denom else 0.0
    best = round(base.char_width + delta, 3)
    print(f"measured {len(samples)} nodes in {len(rows)} graphs (Unity {unity})")
    print(f"char_width {base.char_width:.3f}: {_describe([x[2] - x[1] for x in samples])}")
    print(f"char_width {best:.3f}: {_describe([x[2] + x[3] * delta - x[1] for x in samples])}")
    by_type: Dict[str, List[float]] = {}
    for name, real, est, _ in samples:
        by_type.setdefault(name, []).append(abs(est - real))
    worst = sorted(by_type.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:5]
    print("largest errors: " + ", ".join(f"{k} {sum(v) / len(v):.0f} px" for k, v in worst))
    if not a.write:
        print(f"--write saves char_width {best:.3f} to {CONFIG_NAME}")
        return 0
    path = ws.project.root / CONFIG_NAME
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw.setdefault("layout", {})["char_width"] = best
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved char_width {best:.3f} to {path}")
    return 0


def register(sub) -> None:
    s = sub.add_parser("layout", help="tidy a Behaviour Tree's layout, using node sizes")
    s.add_argument("graph")
    s.add_argument("--node", default="0", help="root of the subtree to lay out (default: the start node)")
    s.add_argument("--unity", action="store_true", help="take exact node sizes from the open Unity Editor")
    s.add_argument("--dry-run", action="store_true", help="show the result without writing")
    s.add_argument("--timeout", type=float, default=300, help="seconds to wait for the Editor (default 300)")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    s.set_defaults(func=cmd_layout)

    c = sub.add_parser("calibrate", help="compare estimated node sizes with the open Unity Editor")
    c.add_argument("graphs", nargs="*", help="graphs to measure (default: all .asset graphs)")
    c.add_argument("--unity", action="store_true", help="measure in the open Unity Editor (required)")
    c.add_argument("--write", action="store_true", help=f"save the fitted char_width to {CONFIG_NAME}")
    c.add_argument("--timeout", type=float, default=600, help="seconds to wait for the Editor (default 600)")
    c.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    c.set_defaults(func=cmd_calibrate)
