"""Layout by node size (`sizing: content`, the default); test_apply.py keeps the old distances under `sizing: fixed`."""

import json

import pytest

from nodecanvas_cli.nodesize import workspace_sizes
from nodecanvas_cli.workspace import Workspace

ACTIONS = "NodeCanvas.Tasks.Actions."
LONG = "CalculateCirclePositionAroundTheTargetWithOffset"


def apply(ncg, project, graph, ops, *flags):
    path = project / "ops.json"
    path.write_text(json.dumps(ops))
    return ncg("apply", graph, path, *flags)


def wait(seconds=1):
    return {"$type": "ActionNode", "_action": {"$type": "Wait", "waitTime": seconds}}


def rects(graph="Bot_BT"):
    """Graph and each node's (x, y, w, h), sizes rounded up to the grid like the layout does."""
    ws = Workspace()
    g = ws.graph(graph)
    sizes = workspace_sizes(ws, g)
    up = lambda v: -(-v // 20) * 20  # noqa: E731
    out = {}
    for n in g.nodes:
        p = n["_position"]
        w, h = sizes[id(n)]
        out[g.nid(n)] = (p.get("x", 0.0), p.get("y", 0.0), up(w), up(h))
    return g, out


def assert_no_overlaps(r):
    items = list(r.items())
    for i, (a, (ax, ay, aw, ah)) in enumerate(items):
        for b, (bx, by, bw, bh) in items[i + 1 :]:
            assert not (ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah), (a, r[a], b, r[b])


def assert_on_grid(r):
    assert all(v[0] % 20 == 0 and v[1] % 20 == 0 for v in r.values()), r


def set_long_task(node):
    """An edit that makes ``node`` very wide: Unity prints the whole member name."""
    return {
        "op": "replace",
        "node": node,
        "with": {
            "$type": "ActionNode",
            "_action": {
                "$type": ACTIONS + "ExecuteFunction_Multiplatform",
                "method": {"_baseInfo": f"Game.Bot|{LONG}|System.Void"},
            },
        },
    }


def test_layout_op_does_not_overlap_wide_nodes(ncg, project):
    code, _, err = apply(ncg, project, "Bot_BT", [set_long_task("7"), set_long_task("5"), {"op": "layout"}], "--force")
    assert code == 0, err
    g, r = rects()
    assert max(v[2] for v in r.values()) > 300  # wider than the old fixed step
    assert_no_overlaps(r)
    assert_on_grid(r)


def test_layout_keeps_the_gap_between_edges_and_the_child_order(ncg, project):
    code, _, err = apply(ncg, project, "Bot_BT", [set_long_task("7"), {"op": "layout"}], "--force")
    assert code == 0, err
    g, r = rects()
    for n in g.nodes:
        kids = [r[g.nid(k)] for k in g.children(n)]
        for a, b in zip(kids, kids[1:]):
            if a[1] == b[1]:
                assert b[0] - (a[0] + a[2]) >= 40  # sibling_gap 60, less up to a cell of centring and snapping
            assert a[0] + a[2] / 2 < b[0] + b[2] / 2


def test_layout_centres_parents_over_children(ncg, project):
    assert apply(ncg, project, "Bot_BT", [{"op": "layout"}])[0] == 0
    g, r = rects()
    for n in g.nodes:
        kids = g.children(n)
        if len(kids) == 1:
            x, _, w, _ = r[g.nid(n)]
            kx, _, kw, _ = r[g.nid(kids[0])]
            assert abs(x + w / 2 - kx - kw / 2) <= 20


def test_layout_stacks_rows_by_the_tallest_node(ncg, project):
    tall = {"$type": "NodeCanvas.Framework.ActionList", "actions": [{"$type": ACTIONS + "Wait"}] * 6}
    ops = [{"op": "set", "node": "5", "path": "_action", "value": tall}, {"op": "layout"}]
    code, _, err = apply(ncg, project, "Bot_BT", ops, "--force")
    assert code == 0, err
    g, r = rects()
    five = r["5"]
    assert five[3] > 100
    assert_no_overlaps(r)
    assert len({r[g.nid(k)][1] for k in g.children(g.node("6"))}) == 1  # one row


def test_new_node_goes_past_the_right_edge_of_a_wide_sibling(ncg, project):
    code, _, err = apply(ncg, project, "Bot_BT", [set_long_task("9")], "--force")
    assert code == 0, err
    code, _, err = apply(ncg, project, "Bot_BT", [{"op": "add", "parent": "0", "node": wait()}])
    assert code == 0, err
    g, r = rects()
    kids = [g.nid(k) for k in g.children(g.node("0"))]
    wide, new = r[kids[-2]], r[kids[-1]]
    assert new[0] == wide[0] + wide[2] + 60
    assert new[1] == wide[1]  # joins the row of its siblings


def test_new_only_child_is_centred_under_its_parent(ncg, project):
    code, _, err = apply(
        ncg,
        project,
        "Bot_BT",
        [
            {"op": "add", "parent": "0", "id": "s", "node": {"$type": "Selector"}},
            {"op": "add", "parent": "s", "node": wait()},
        ],
    )
    assert code == 0, err
    g, r = rects()
    parent = g.children(g.node("0"))[-1]
    px, py, pw, ph = r[g.nid(parent)]
    cx, cy, cw, _ = r[g.nid(g.children(parent)[0])]
    assert abs(px + pw / 2 - cx - cw / 2) <= 20
    assert cy == py + ph + 100


def test_size_uses_the_text_after_the_whole_batch(ncg, project):
    ops = [
        {"op": "add", "parent": "0", "id": "a", "node": wait()},
        set_long_task("a"),
        {"op": "add", "parent": "0", "node": wait()},
    ]
    code, _, err = apply(ncg, project, "Bot_BT", ops, "--force")
    assert code == 0, err
    g, r = rects()
    kids = [g.nid(k) for k in g.children(g.node("0"))]
    wide, last = r[kids[-2]], r[kids[-1]]
    assert wide[2] > 300 and last[0] >= wide[0] + wide[2] + 60


def test_wrap_moves_the_node_below_the_wrapper(ncg, project):
    _, before = rects()
    code, _, err = apply(ncg, project, "Bot_BT", [{"op": "wrap", "node": "5", "with": "Cooldown"}])
    assert code == 0, err
    g, r = rects()
    wrapper = g.children(g.node("4"))[0]
    x, y, _, h = r[g.nid(wrapper)]
    assert (x, y) == before["5"][:2]
    assert r[g.nid(g.children(wrapper)[0])][1] == y + h + 100
    assert_on_grid(r)


def test_fsm_state_goes_past_the_right_edge_of_every_state(ncg, project):
    _, before = rects("Bot_FSM")
    code, _, err = apply(ncg, project, "Bot_FSM", [{"op": "add", "node": {"$type": "ActionState", "_name": "New"}}])
    assert code == 0, err
    g, after = rects("Bot_FSM")
    new = next(v for k, v in after.items() if g.node(k).get("_name") == "New")
    assert new[0] >= max(v[0] + v[2] for v in before.values()) + 40
    assert_no_overlaps(after)
    assert_on_grid(after)


def test_apply_reports_the_source_of_sizes(ncg, project):
    _, out, _ = apply(ncg, project, "Bot_BT", [{"op": "add", "parent": "0", "node": wait()}], "--dry-run")
    assert "sizes: estimated 11" in out


def test_fixed_sizing_reports_no_sizes(ncg, project):
    (project / "nodecanvas-cli.json").write_text('{"layout": {"sizing": "fixed"}}')
    _, out, _ = apply(ncg, project, "Bot_BT", [{"op": "add", "parent": "0", "node": wait()}], "--dry-run")
    assert "layout:" in out and "sizes:" not in out


@pytest.mark.parametrize(
    "layout,message",
    [
        ('{"sizing": "auto"}', "layout.sizing"),
        ('{"sizing": 1}', "layout.sizing"),
        ('{"grid": true}', "layout.grid"),
        ('{"char_width": 0}', "layout.char_width"),
        ('{"nope": 1}', "unknown layout setting"),
    ],
)
def test_bad_layout_settings(ncg, project, layout, message):
    (project / "nodecanvas-cli.json").write_text('{"layout": %s}' % layout)
    code, _, err = ncg("list")
    assert code == 2 and message in err


def test_gaps_depend_on_the_sizing_mode():
    from nodecanvas_cli.config import Layout

    assert (Layout().sibling_gap, Layout().level_gap) == (60.0, 100.0)
    fixed = Layout(sizing="fixed")
    assert (fixed.sibling_gap, fixed.level_gap) == (300.0, 200.0)
    assert Layout(sizing="fixed", sibling_gap=400.0).sibling_gap == 400.0


def test_layout_command_writes_and_dry_run_does_not(ncg, project):
    asset = project / "Assets/Graphs/Bot_BT.asset"
    before = asset.read_bytes()
    code, out, _ = ncg("layout", "Bot_BT", "--dry-run")
    assert code == 0 and "layout:" in out and "sizes: estimated 10" in out and "dry run" in out
    assert asset.read_bytes() == before
    code, out, _ = ncg("layout", "Bot_BT")
    assert code == 0 and "saved" in out
    assert asset.read_bytes() != before
    _, r = rects()
    assert_no_overlaps(r)
    assert_on_grid(r)
    code, out, _ = ncg("layout", "Bot_BT")
    assert code == 0 and "no node moved" in out


def test_layout_command_takes_a_subtree(ncg, project):
    _, before = rects()
    assert ncg("layout", "Bot_BT", "--node", "6")[0] == 0
    _, after = rects()
    assert after["1"] == before["1"]


def test_layout_command_refuses_flat_graphs(ncg):
    code, _, err = ncg("layout", "Bot_FSM")
    assert code == 2 and "Behaviour Trees" in err


# ------------------------------------------------------------------ relayout by contours


def sequence(*kids):
    """Ops for a Sequencer under the root with these subtrees; a kid is a handle or (handle, its kids)."""
    ops = []

    def add(parent, handle, below):
        node = {"$type": "Sequencer"} if below else wait()
        ops.append({"op": "add", "parent": parent, "id": handle, "node": node})
        for h, b in below:
            add(handle, h, b)

    for handle, below in kids:
        add("0", handle, below)
    return ops


def tree_ops():
    """A deep wide branch, then a leaf: the leaf's row above the branch's deep rows is free."""
    deep = ("d", [("d1", [("d11", []), ("d12", []), ("d13", [])]), ("d2", [("d21", []), ("d22", []), ("d23", [])])])
    return sequence(deep, ("leaf", []))


def width(r):
    return max(v[0] + v[2] for v in r.values()) - min(v[0] for v in r.values())


def slot_width(graph="Bot_BT"):
    from nodecanvas_cli.layout import Layouter

    ws = Workspace()
    g = ws.graph(graph)
    lay = Layouter(g, ws.config.layout, workspace_sizes(ws, g))
    lefts = lay._slot_lefts(g.node("0"))
    return max(lefts[id(n)] + lay.size(n)[0] for n in g.nodes) - min(lefts[id(n)] for n in g.nodes)


def test_layout_packs_a_subtree_above_its_deeper_neighbour(ncg, project):
    code, _, err = apply(ncg, project, "Bot_BT", tree_ops() + [{"op": "layout"}], "--force")
    assert code == 0, err
    g, r = rects()
    assert_no_overlaps(r)
    assert_on_grid(r)
    assert width(r) < slot_width()
    kids = g.children(g.node("0"))
    leaf, branch = kids[-1], kids[-2]
    grandkids = [r[g.nid(m)] for k in g.children(branch) for m in g.children(k)]
    assert r[g.nid(leaf)][0] < max(x + w for x, _, w, _ in grandkids)  # the leaf sits over the branch's last row


def test_layout_keeps_edges_of_a_short_parent_off_a_tall_neighbour(ncg, project):
    tall = {"$type": "NodeCanvas.Framework.ActionList", "actions": [{"$type": ACTIONS + "Wait"}] * 6}
    ops = sequence(
        ("p", [("p1", []), ("p2", []), ("p3", []), ("p4", [])]),
        ("t", []),
    ) + [{"op": "set", "node": "t", "path": "_action", "value": tall}, {"op": "layout"}]
    code, _, err = apply(ncg, project, "Bot_BT", ops, "--force")
    assert code == 0, err
    g, r = rects()
    kids = g.children(g.node("0"))
    px, py, pw, ph = r[g.nid(kids[-2])]
    tx, ty, tw, th = r[g.nid(kids[-1])]
    assert py == ty and ph < th
    for k in g.children(kids[-2]):
        kx, ky, kw, _ = r[g.nid(k)]
        # a straight edge reaches the tall node's bottom edge at this x
        at = px + pw / 2 + (kx + kw / 2 - px - pw / 2) * (ty + th - py - ph) / (ky - py - ph)
        assert at < tx
    assert_no_overlaps(r)


def test_layout_puts_a_merge_beside_its_other_parent(ncg, project):
    ops = [
        {"op": "add", "parent": "0", "id": "b", "node": {"$type": "BinarySelector"}},
        {"op": "add", "parent": "b", "id": "x", "node": {"$type": "ConditionalEvaluator"}},
        {"op": "add", "parent": "b", "id": "m", "node": {"$type": "Merge"}},
        {"op": "connect", "from": "x", "to": "m"},
        {"op": "add", "parent": "m", "node": wait()},
        {"op": "layout"},
    ]
    code, _, err = apply(ncg, project, "Bot_BT", ops, "--force")
    assert code == 0, err
    g, r = rects()
    for n in g.nodes:
        centres = [r[g.nid(k)][0] + r[g.nid(k)][2] / 2 for k in g.children(n)]
        assert centres == sorted(centres) and len(set(centres)) == len(centres), g.nid(n)
    assert_no_overlaps(r)
    assert_on_grid(r)


def test_layout_keeps_a_wide_parent_off_its_neighbours(ncg, project):
    ops = sequence(("a", [("a1", [])]), ("b", [("b1", [])]))
    ops += [set_long_task("a1"), {"op": "layout"}]
    code, _, err = apply(ncg, project, "Bot_BT", ops, "--force")
    assert code == 0, err
    g, r = rects()
    assert_no_overlaps(r)
    for n in g.nodes:
        row = [r[g.nid(k)] for k in g.children(n)]
        for a, b in zip(row, row[1:]):
            if a[1] == b[1]:
                assert b[0] - (a[0] + a[2]) >= 60  # contours keep the gap exact


def test_fixed_sizing_keeps_consecutive_slots(ncg, project):
    (project / "nodecanvas-cli.json").write_text('{"layout": {"sizing": "fixed"}}')
    code, _, err = apply(ncg, project, "Bot_BT", tree_ops() + [{"op": "layout"}], "--force")
    assert code == 0, err
    ws = Workspace()
    g = ws.graph("Bot_BT")
    kids = g.children(g.node("0"))
    leaf, branch = kids[-1], kids[-2]
    last = [m for k in g.children(branch) for m in g.children(k)][-1]
    assert leaf["_position"]["x"] - last["_position"]["x"] == 300  # the next slot, not over the branch


class FakeGraph:
    """Just the links `Layouter` reads; the ops refuse to make a cycle."""

    def __init__(self, links):
        self.nodes = [{} for _ in range(max(max(pair) for pair in links) + 1)]
        self.kids = {i: [] for i in range(len(self.nodes))}
        self.ups = {i: [] for i in range(len(self.nodes))}
        for a, b in links:
            self.kids[a].append(b)
            self.ups[b].append(a)

    def _index(self, n):
        return next(i for i, m in enumerate(self.nodes) if m is n)

    def children(self, n):
        return [self.nodes[i] for i in self.kids[self._index(n)]]

    def parents(self, n):
        return [self.nodes[i] for i in self.ups[self._index(n)]]


def test_layout_of_a_cycle_positions_every_node():
    from nodecanvas_cli.config import Layout
    from nodecanvas_cli.layout import Layouter

    g = FakeGraph([(0, 1), (1, 2), (2, 1), (0, 3)])
    lay = Layouter(g, Layout())
    lay.relayout(g.nodes[0])
    assert all(lay.pos(n) is not None for n in g.nodes)


def test_layout_keeps_the_centre_order_with_a_merge_deeper_than_its_sibling():
    from nodecanvas_cli.config import Layout
    from nodecanvas_cli.layout import Layouter

    # 0 -> [1, 2, 5]; 5 -> 6 -> 2 puts the Merge #2 three rows down, where only #4 is in its way
    g = FakeGraph([(0, 1), (0, 2), (0, 5), (1, 3), (1, 7), (3, 4), (5, 6), (6, 2)])
    wide = id(g.nodes[7])
    lay = Layouter(g, Layout(), {wide: (1000.0, 40.0)})
    lay.relayout(g.nodes[0])
    centre = lambda i: lay.pos(g.nodes[i])[0] + lay.size(g.nodes[i])[0] / 2  # noqa: E731
    assert centre(1) < centre(2) < centre(5)


def test_layout_falls_back_to_slots_when_the_contour_walk_misses_a_node(monkeypatch):
    from nodecanvas_cli.config import Layout
    from nodecanvas_cli.layout import Layouter

    g = FakeGraph([(0, 1), (0, 2)])
    lay = Layouter(g, Layout())
    monkeypatch.setattr(Layouter, "_contour_lefts", lambda *_: None)
    lay.relayout(g.nodes[0])
    want = lay._slot_lefts(g.nodes[0])
    shift = lay.pos(g.nodes[0])[0] - want[id(g.nodes[0])]
    assert [lay.pos(n)[0] for n in g.nodes] == [lay.snap(want[id(n)] + shift) for n in g.nodes]


# ------------------------------------------------------------------ lint


def lint_codes(ncg, graph="Bot_BT"):
    _, out, _ = ncg("lint", graph, "--json", "--min", "info")
    return {(i["at"], i["code"]) for i in json.loads(out)}


def test_lint_sees_an_overlap_with_a_wide_node(ncg, project):
    code, _, err = apply(ncg, project, "Bot_BT", [set_long_task("7")], "--force")
    assert code == 0, err
    ws = Workspace()
    g = ws.graph("Bot_BT")
    wide = next(n for n in g.nodes if LONG in str(n.get("_action")))
    x = wide["_position"].get("x", 0.0)
    y = wide["_position"].get("y", 0.0)
    ops = [{"op": "set", "node": g.nid(g.node("8")), "path": "_position", "value": [x + 200, y]}]
    assert apply(ncg, project, "Bot_BT", ops, "--force")[0] == 0
    assert any(code == "overlap" for _, code in lint_codes(ncg))


def test_lint_child_order_compares_centres(ncg, project):
    # #7 is left of #8 by its corner, but so wide that its centre is to the right: the editor sorts by the centre
    code, _, err = apply(ncg, project, "Bot_BT", [set_long_task("7")], "--force")
    assert code == 0, err
    ws = Workspace()
    g = ws.graph("Bot_BT")
    parent = next(n for n in g.nodes if len(g.children(n)) == 2 and "ProbabilitySelector" in n["$type"])
    wide, other = g.children(parent)
    x = wide["_position"].get("x", 0.0)
    assert LONG in str(wide.get("_action"))
    ops = [{"op": "set", "node": g.nid(other), "path": "_position", "value": [x + 20, 900]}]
    assert apply(ncg, project, "Bot_BT", ops, "--force")[0] == 0
    assert (f"#{g.nid(parent)}", "child-order") in lint_codes(ncg)
