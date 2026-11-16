"""`layout --unity` and `calibrate --unity`, against a stand-in for the Unity CLI."""

import json
import re
import stat
import sys

import pytest

FAKE = r"""#!{python}
import json, os, re, sys
argv = sys.argv[1:]
mode = os.environ.get("FAKE_UNITY_MODE", "ok")
if argv[0] == "status":
    rows = [] if mode == "down" else [{{"state": "ready"}}]
    print(json.dumps({{"success": mode != "down", "data": {{"items": rows}}}}))
    sys.exit(12 if mode == "down" else 0)
if mode == "down":
    print(json.dumps({{"success": False, "errors": [{{"code": "EDITOR_UNREACHABLE",
                                                     "message": "No Pipeline instance found"}}]}}))
    sys.exit(12)
assert argv[1:3] == ["script", "eval"], argv
if "--file" not in argv:
    print("false")  # Pipeline 1.0: the bare result
    sys.exit(0)
code = open(argv[argv.index("--file") + 1], encoding="utf-8").read()
assert "nodecanvas_cli_sizes" in code
paths = re.findall(r'"(Assets/[^"]+)"', code)
open(os.environ["FAKE_UNITY_LOG"], "a").write(json.dumps(paths) + "\n")
counts = json.loads(os.environ["FAKE_UNITY_NODES"])
size = json.loads(os.environ.get("FAKE_UNITY_SIZE", "[400, 60]"))
if mode == "noapi":
    report = {{"nodecanvas_cli_sizes": 1, "error": "EditorWindow.RepaintImmediately is not available"}}
else:
    graphs = []
    for p in paths:
        n = counts.get(p, 0)
        nodes = [None if mode == "undrawn" else size for _ in range(n)]
        graphs.append({{"path": p, "status": "ok", "nodes": nodes}})
    report = {{"nodecanvas_cli_sizes": 1, "unity": "6000.2.8f1", "graphs": graphs}}
print(json.dumps(json.dumps(report)))
"""

BOT = "Assets/Graphs/Bot_BT.asset"


@pytest.fixture
def fake_unity(tmp_path, monkeypatch):
    exe = tmp_path / "fake_unity"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "unity.log"
    monkeypatch.setenv("NODECANVAS_CLI_UNITY", str(exe))
    monkeypatch.setenv("FAKE_UNITY_LOG", str(log))
    monkeypatch.setenv("FAKE_UNITY_NODES", json.dumps({BOT: 10}))
    return log


def positions():
    from nodecanvas_cli.workspace import Workspace

    g = Workspace().graph("Bot_BT")
    return g, {g.nid(n): n["_position"].get("x", 0.0) for n in g.nodes}


def test_layout_unity_uses_the_measured_sizes(ncg, project, fake_unity):
    code, out, err = ncg("layout", "Bot_BT", "--unity", "--dry-run")
    assert code == 0, err
    assert "sizes: unity 10" in out and "estimated" not in out
    assert json.loads(fake_unity.read_text().splitlines()[0]) == [BOT]
    code, out, err = ncg("layout", "Bot_BT", "--unity")
    assert code == 0, err
    g, x = positions()
    # every node is 400 wide here, so leaves of one parent are 400 + 60 apart
    kids = [g.nid(k) for k in g.children(g.node("0"))]
    assert all(x[b] - x[a] >= 460 for a, b in zip(kids, kids[1:]))


def test_layout_unity_estimates_what_it_did_not_draw(ncg, project, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "undrawn")
    code, out, err = ncg("layout", "Bot_BT", "--unity", "--dry-run")
    assert code == 0, err
    assert "sizes: estimated 10" in out


def test_layout_unity_fails_without_an_editor_and_does_not_fall_back(ncg, project, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "down")
    asset = project / BOT
    before = asset.read_bytes()
    code, out, err = ncg("layout", "Bot_BT", "--unity", "--timeout", "1")
    assert code == 2 and "no Unity Editor" in err and "drop --unity" in err
    assert "layout:" not in out and asset.read_bytes() == before


def test_layout_unity_needs_the_cli(ncg, project, monkeypatch):
    monkeypatch.setenv("NODECANVAS_CLI_UNITY", "")
    monkeypatch.setenv("PATH", "")
    code, _, err = ncg("layout", "Bot_BT", "--unity")
    assert code == 2 and "not on PATH" in err


def test_layout_unity_refuses_a_graph_edited_in_the_editor(ncg, project, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_NODES", json.dumps({BOT: 3}))
    code, _, err = ncg("layout", "Bot_BT", "--unity")
    assert code == 1 and "Unity sees 3 nodes" in err and "the file has 10" in err


def test_layout_unity_reports_an_old_editor(ncg, project, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "noapi")
    code, _, err = ncg("layout", "Bot_BT", "--unity")
    assert code == 1 and "RepaintImmediately" in err


def test_layout_unity_refuses_bound_graphs(ncg, project, fake_unity):
    code, _, err = ncg("layout", "Sentry", "--unity")
    assert code == 2 and ".asset graphs" in err


def test_layout_unity_needs_content_sizing(ncg, project, fake_unity):
    (project / "nodecanvas-cli.json").write_text('{"layout": {"sizing": "fixed"}}')
    code, _, err = ncg("layout", "Bot_BT", "--unity")
    assert code == 2 and "sizing" in err


def test_calibrate_needs_unity(ncg):
    code, _, err = ncg("calibrate")
    assert code == 2 and "--unity" in err


def test_calibrate_fits_the_character_width_and_writes_only_on_request(ncg, project, fake_unity, monkeypatch):
    # Measured sizes taken from the estimate at a wider character prove the fit moves in the right direction
    from nodecanvas_cli.nodesize import NodeSizer, TextMetrics
    from nodecanvas_cli.workspace import Workspace

    ws = Workspace()
    g = ws.graph("Bot_BT")
    wide = NodeSizer(g, ws.renderer(g), TextMetrics(char_width=7.0))
    monkeypatch.setenv("FAKE_UNITY_NODES", json.dumps({BOT: 10}))
    monkeypatch.setenv("FAKE_UNITY_SIZE", json.dumps(wide.size(g.nodes[5])))
    code, out, err = ncg("calibrate", "Bot_BT", "--unity")
    assert code == 0, err
    assert "measured 10 nodes in 1 graphs (Unity 6000.2.8f1)" in out
    best = float(re.findall(r"^char_width ([\d.]+):", out, re.M)[1])
    assert best > TextMetrics().char_width
    assert "--write saves" in out and not (project / "nodecanvas-cli.json").exists()

    (project / "nodecanvas-cli.json").write_text('{"layout": {"grid": 20}}')
    code, out, err = ncg("calibrate", "Bot_BT", "--unity", "--write")
    assert code == 0, err
    raw = json.loads((project / "nodecanvas-cli.json").read_text())
    assert raw["layout"] == {"grid": 20, "char_width": best}
    # the next estimate uses it
    assert f"char_width {best:.3f}:" in ncg("calibrate", "Bot_BT", "--unity")[1]


def test_calibrate_reports_nothing_drawn(ncg, project, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "undrawn")
    code, _, err = ncg("calibrate", "Bot_BT", "--unity")
    assert code == 1 and "drew no nodes" in err


def test_snippet_lists_the_targets():
    from nodecanvas_cli.unity_sizes import build_snippet

    code = build_snippet(["Assets/A.asset", "Assets/B.asset"])
    assert 'new string[] { "Assets/A.asset", "Assets/B.asset" }' in code
    assert "/*TARGETS*/" not in code and "ForceUpdate" not in code and "SaveAssets" not in code
