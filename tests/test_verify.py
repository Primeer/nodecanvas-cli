import json
import stat
import sys

import pytest

FAKE = r"""#!{python}
# Speaks the protocol of the Unity CLI 1.0 with com.unity.pipeline 1.0: `script eval [--file]` prints the bare
# result (--result-only), a failure prints the envelope and exits non-zero. FAKE_UNITY_PIPELINE=legacy plays a
# package older than 1.0, which has `eval_file` (timeout in ms) and wraps results in objects.
import json, os, re, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_UNITY_LOG"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")
mode = os.environ.get("FAKE_UNITY_MODE", "ok")
legacy = os.environ.get("FAKE_UNITY_PIPELINE") == "legacy"

def fail(exit_code, code, message):
    print(json.dumps({{"success": False, "command": "command", "data": None,
                      "errors": [{{"code": code, "message": message}}], "warnings": []}}))
    sys.exit(exit_code)

if argv[0] == "status":
    if os.environ.get("FAKE_UNITY_SAFE_MODE"):
        print(json.dumps({{"success": False, "data": {{"items": [{{"state": "unreachable", "safeMode": {{
              "detected": True, "compileErrors": [{{"file": "Assets/Bot.cs", "line": 3, "column": 1,
              "code": "CS1002", "message": "; expected"}}]}}}}]}},
              "errors": [{{"code": "STATUS_SAFE_MODE", "message": "Safe Mode"}}]}}))
        sys.exit(6)
    if os.environ.get("FAKE_UNITY_DIALOG"):
        print(json.dumps({{"success": False, "data": {{"items": [{{"state": "starting", "blockedBy": {{
              "reason": "dialog", "dialogs": [{{"level": 0, "title": "Save changes?"}}]}}}}]}},
              "errors": [{{"code": "STATUS_BLOCKED_BY_DIALOG", "message": "blocked"}}]}}))
        sys.exit(6)
    if mode == "down":
        print(json.dumps({{"success": False, "data": {{"items": [], "total": 0}},
                          "errors": [{{"code": "STATUS_NO_INSTANCES", "message": "No Unity Editor"}}]}}))
        sys.exit(12)
    print(json.dumps({{"success": True, "data": {{"items": [{{"state": "ready", "projectPath": "."}}], "total": 1}}}}))
    sys.exit(0)
command = argv[1:3]
file = argv[argv.index("--file") + 1] if "--file" in argv else None
snippet = file or "eval_file" in command
outage = int(os.environ.get("FAKE_UNITY_OUTAGE", "0"))
if outage or os.environ.get("FAKE_UNITY_SNIPPET_OUTAGE"):
    # the first calls find the Editor reloading scripts: no Pipeline connection, exit code 12
    counter = os.environ["FAKE_UNITY_LOG"] + ".count"
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    limit = outage or int(os.environ["FAKE_UNITY_SNIPPET_OUTAGE"])
    hit = n < limit if outage else (snippet and n < limit + 2)
    if hit:
        fail(12, "EDITOR_UNREACHABLE", "No Pipeline instance found")
if mode == "down":
    fail(12, "EDITOR_UNREACHABLE", "No Pipeline instance found")
if mode == "dialog" and snippet:
    fail(18, "AWAITING_INPUT", "The Editor waits on a dialog: Save changes?")
if mode == "failed" and snippet:
    fail(6, "COMMAND_FAILED", "Failed to execute command 'script': NullReferenceException")
if legacy and file:
    fail(13, "REMOTE_COMMAND_NOT_FOUND", "unknown parameter 'file'")
if not legacy and "eval_file" in command:
    fail(13, "REMOTE_COMMAND_NOT_FOUND", "No command named 'eval_file' is available.")
if not snippet:
    print(json.dumps({{"result": False, "success": True}}) if legacy else "false")
    sys.exit(0)
if mode == "nocompile":
    if legacy:
        print(json.dumps({{"success": False, "data": {{"success": False, "error": "Compilation Failed",
                           "diagnostics": [{{"severity": "error", "line": 3,
                             "message": "The type or namespace name 'NodeCanvas' could not be found"}}]}}}}))
        sys.exit(1)
    fail(6, "COMMAND_FAILED", "Failed to execute command 'script': Command execution failed: error: "
         "COMPILATION_ERROR: Compilation Failed: 1 error(s)\nerror: line 3, col 1: error CS0246: "
         "The type or namespace name 'NodeCanvas' could not be found")
code = open(file or argv[-2], encoding="utf-8").read()  # eval_file <path> <ms>
targets = re.findall(r'new string\[\] \{{ "([^"]*)", "([^"]*)" \}}', code)
graphs = []
for path, anchor in targets:
    g = {{"path": path, "anchor": anchor, "status": "ok", "nodes": 10, "variables": 5, "prime": 0, "saved": False,
          "issues": [], "console": []}}
    if path.endswith("Broken_BT.asset"):
        g["nodes"] = 7; g["variables"] = 1
        g["issues"] = [{{"severity": "warning", "at": "#1", "text": "* Missing Reflected Info 'Demo.Game.Bot.Fly ()'"}},
                       {{"severity": "warning", "at": "#4", "text": "* 'Bot' target agent is null"}}]
        g["console"] = [{{"type": "Error", "text": "(Serialization Error) Method Request Error"}},
                        {{"type": "Log", "text": "noise"}}]
    if path.endswith("Sentry.prefab"):
        g["nodes"] = 2; g["variables"] = 0
    graphs.append(g)
report = json.dumps({{"nodecanvas_cli_verify": 1, "unity": "6000.0.1f1", "windows": [], "graphs": graphs}})
print(json.dumps({{"success": True, "result": report}}) if legacy else json.dumps(report))
"""


def kind(call):
    """What a logged call of the fake did: status, eval (inline code), file (`script eval --file`) or eval_file."""
    if call[0] == "status":
        return "status"
    if "--file" in call:
        return "file"
    return "eval_file" if "eval_file" in call else "eval"


@pytest.fixture
def fake_unity(tmp_path, monkeypatch):
    exe = tmp_path / "unity"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "unity.log"
    monkeypatch.setenv("NODECANVAS_CLI_UNITY", str(exe))
    monkeypatch.setenv("FAKE_UNITY_LOG", str(log))

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


def test_verify_requires_unity_flag(ncg):
    code, _, err = ncg("verify", "Bot_BT")
    assert code == 2 and "--unity" in err


def test_verify_editor_unavailable(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "down")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 2 and "No Pipeline instance found" in err and "unity pipeline install" in err


def test_verify_missing_cli(ncg, monkeypatch):
    monkeypatch.setenv("NODECANVAS_CLI_UNITY", "")
    monkeypatch.setenv("PATH", "/nonexistent")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 2 and "not on PATH" in err


def test_verify_reports(ncg, fake_unity, project):
    code, out, err = ncg("verify", "--unity", "Bot_BT", "Broken_BT", "Sentry")
    assert code == 1, err
    assert "ok   Assets/Graphs/Bot_BT.asset  nodes=10 vars=5" in out
    assert "FAIL Assets/Graphs/Broken_BT.asset" in out
    assert "error   #1         Missing Reflected Info 'Demo.Game.Bot.Fly ()'" in out
    assert "target agent is null" not in out  # context-dependent editor warning
    assert "Method Request Error" in out and "noise" not in out
    assert "ok   Assets/Prefabs/Sentry.prefab:Sentry  nodes=2" in out
    calls = fake_unity()
    assert [kind(c) for c in calls] == ["eval", "eval", "file"]  # refresh, wait for compilation, check
    assert calls[0][:3] == ["command", "script", "eval"]
    assert calls[0][calls[0].index("--project-path") + 1] == str(project)
    assert "--skill" in calls[0] and "--result-only" in calls[0]
    check = calls[-1]
    fence = check.index("--")
    assert check[fence + 1 : fence + 3] == ["--file", check[fence + 2]]
    assert check[fence + 3 :] == ["--timeout", "300"]  # the snippet's own timeout, in seconds, after the fence
    assert check.index("--result-only") < fence


def test_verify_snippet_targets_and_resave(fake_unity):
    from nodecanvas_cli.verify import build_snippet

    code = build_snippet([("Assets/A.asset", ""), ("Assets/P.prefab", "1003")], resave=True)
    assert 'new string[] { "Assets/A.asset", "" }, new string[] { "Assets/P.prefab", "1003" }' in code
    assert "var resave = true;" in code


def test_verify_count_mismatch_and_json(ncg, fake_unity):
    code, out, _ = ncg("verify", "--unity", "Sub_BT", "--json")
    rows = json.loads(out)["graphs"]
    assert code == 1 and rows[0]["ok"] is False
    assert any("Unity loaded 10 nodes, the file has 4" in i["message"] for i in rows[0]["issues"])


def test_verify_snippet_does_not_compile(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "nocompile")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "does not compile" in err and "NodeCanvas" in err


def test_verify_changed(ncg, fake_unity, project):
    import subprocess

    def run(*a):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=project, check=True, capture_output=True
        )

    run("init", "-q")
    run("add", "-A")
    run("commit", "-qm", "base")
    code, out, _ = ncg("verify", "--unity", "--changed")
    assert code == 0 and "no changed graphs" in out
    (project / "ops.json").write_text('[{"op": "set", "node": "1", "path": "_position", "value": [0, 160]}]')
    assert ncg("apply", "Sentry", project / "ops.json")[0] == 0
    code, out, _ = ncg("verify", "--unity", "--changed")
    assert "Sentry.prefab:Sentry" in out and "Bot_BT" not in out


def test_verify_notes_missing_wrappers_restored_on_load(ncg, fake_unity):
    # Broken_BT saves #6 as MissingAction; Unity reported no missing type, so it restored the wrapper
    _, out, _ = ncg("verify", "--unity", "Broken_BT")
    assert "1 Missing wrapper(s) in the file were restored on load" in out
    _, out, _ = ncg("verify", "--unity", "Bot_BT")
    assert "Missing wrapper" not in out


def test_verify_changed_with_names_is_an_error(ncg, fake_unity):
    code, _, err = ncg("verify", "--unity", "--changed", "Bot_BT")
    assert code == 2 and "not both" in err


def test_verify_waits_for_an_editor_reloading_scripts(ncg, fake_unity, project, monkeypatch):
    # right after `unity recompile` the Editor has no Pipeline connection for a while, but it is there
    (project / "Temp").mkdir()
    (project / "Temp/UnityLockfile").write_text("")
    monkeypatch.setenv("FAKE_UNITY_OUTAGE", "2")
    code, out, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 0, err
    assert "waiting" in err and "ok   Assets/Graphs/Bot_BT.asset" in out
    names = [kind(c) for c in fake_unity()]
    assert names.count("status") >= 2 and names[-1] == "file"


def test_verify_does_not_wait_when_no_editor_is_open(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "down")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 2 and "waiting" not in err and "No Pipeline instance found" in err
    assert "--until-ready" not in [x for c in fake_unity() for x in c]


def test_verify_gives_up_on_the_timeout(ncg, fake_unity, project, monkeypatch):
    (project / "Temp").mkdir()
    (project / "Temp/UnityLockfile").write_text("")
    monkeypatch.setenv("FAKE_UNITY_OUTAGE", "1000")
    code, _, err = ncg("verify", "--unity", "Bot_BT", "--timeout", "1")
    assert code == 2 and "No Pipeline instance found" in err


def test_verify_reports_safe_mode(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_OUTAGE", "1000")
    monkeypatch.setenv("FAKE_UNITY_SAFE_MODE", "1")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 2 and "Safe Mode" in err and "Assets/Bot.cs(3,1): CS1002 ; expected" in err


def test_verify_retries_the_check_after_a_reload(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_SNIPPET_OUTAGE", "1")
    code, out, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 0, err
    assert [kind(c) for c in fake_unity()].count("file") == 2


def test_verify_fails_on_a_type_contract_unity_cannot_see(ncg, fake_unity, project):
    (project / "Assets/Game/Pawns.cs").write_text(
        "using UnityEngine; namespace A { public class Pawn : MonoBehaviour { } } "
        "namespace B { public class Pawn : MonoBehaviour { } }"
    )
    sub = [{"op": "var-add", "name": "T", "type": "A.Pawn", "public": True}]
    (project / "ops.json").write_text(json.dumps(sub))
    assert ncg("apply", "Sub_BT", project / "ops.json")[0] == 0
    ops = [{"op": "var-add", "name": "P", "type": "B.Pawn"}, {"op": "map", "node": "8", "map": {"T": "<-$P"}}]
    (project / "ops.json").write_text(json.dumps(ops))
    assert ncg("apply", "Bot_BT", project / "ops.json", "--force")[0] == 0
    code, out, _ = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "FAIL Assets/Graphs/Bot_BT.asset" in out
    assert "[mapping-type-mismatch] Sub_BT.$T (A.Pawn) is mapped to $P (B.Pawn)" in out
    rows = json.loads(ncg("verify", "--unity", "Bot_BT", "--json")[1])["graphs"]
    assert rows[0]["ok"] is False


def test_verify_shows_contract_warnings_under_ok(ncg, fake_unity, project):
    (project / "Assets/Game/Comp.cs").write_text(
        "using UnityEngine; namespace A { public class Health : MonoBehaviour { } }"
    )
    sub = [{"op": "var-add", "name": "T", "type": "A.Health", "public": True}]
    (project / "ops.json").write_text(json.dumps(sub))
    assert ncg("apply", "Sub_BT", project / "ops.json")[0] == 0
    # $Target is a Unit component; no new variable, the fake Editor reports the fixture's variable count
    (project / "ops.json").write_text(json.dumps([{"op": "map", "node": "8", "map": {"T": "<-$Target"}}]))
    assert ncg("apply", "Bot_BT", project / "ops.json")[0] == 0
    code, out, _ = ncg("verify", "--unity", "Bot_BT")
    assert code == 0 and "ok   Assets/Graphs/Bot_BT.asset" in out
    assert "warning" in out and "[mapping-type-mismatch]" in out


def test_verify_changed_finds_a_stale_mapping_type_in_the_parent(ncg, fake_unity, project):
    import subprocess

    def apply(graph, ops):
        (project / "ops.json").write_text(json.dumps(ops))
        assert ncg("apply", graph, project / "ops.json")[0] == 0

    apply("Sub_BT", [{"op": "var-add", "name": "Shared", "type": "float", "public": True}])
    apply("Bot_BT", [{"op": "map", "node": "8", "map": {"Shared": "<-$Speed"}}])
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=project, check=True, capture_output=True
        )
    apply("Sub_BT", [{"op": "var-set", "name": "Shared", "type": "int"}])
    code, out, err = ncg("verify", "--unity", "--changed")
    assert code == 1 and "also checking" in err
    assert "FAIL Assets/Graphs/Bot_BT.asset" in out and "[stale-mapping-type]" in out


def test_verify_falls_back_to_eval_file_on_an_older_pipeline(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_PIPELINE", "legacy")
    code, out, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 0, err
    assert "ok   Assets/Graphs/Bot_BT.asset  nodes=10" in out
    calls = fake_unity()
    assert [kind(c) for c in calls] == ["eval", "eval", "file", "eval_file"]
    assert calls[-1][1:3] == ["script", "eval_file"] and calls[-1][-1] == "300000"  # milliseconds


def test_verify_takes_the_form_from_the_package_lock(ncg, fake_unity, project, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_PIPELINE", "legacy")
    (project / "Packages").mkdir(exist_ok=True)
    lock = {"dependencies": {"com.unity.pipeline": {"version": "0.10.0-exp.1", "source": "registry"}}}
    (project / "Packages/packages-lock.json").write_text(json.dumps(lock))
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 0, err
    assert [kind(c) for c in fake_unity()] == ["eval", "eval", "eval_file"]


def test_verify_on_pipeline_1_does_not_try_eval_file(ncg, fake_unity, project, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_PIPELINE", "legacy")  # an Editor that refuses --file
    (project / "Packages").mkdir(exist_ok=True)
    (project / "Packages/packages-lock.json").write_text(
        json.dumps({"dependencies": {"com.unity.pipeline": {"version": "1.0.0"}}})
    )
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "unity pipeline upgrade" in err
    assert "eval_file" not in [kind(c) for c in fake_unity()]


def test_verify_snippet_does_not_compile_on_an_older_pipeline(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_PIPELINE", "legacy")
    monkeypatch.setenv("FAKE_UNITY_MODE", "nocompile")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "does not compile" in err and "line 3: The type or namespace name 'NodeCanvas'" in err


def test_verify_compile_error_lines(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "nocompile")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "line 3, col 1: error CS0246" in err and "Unity Editor stopped" not in err


def test_verify_reports_a_dialog_without_waiting(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_OUTAGE", "1000")
    monkeypatch.setenv("FAKE_UNITY_DIALOG", "1")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 2 and "Save changes?" in err and "unity command editor dialog" in err
    assert "--until-ready" not in [x for c in fake_unity() for x in c]


def test_verify_reports_a_dialog_met_by_the_check(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "dialog")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "waits for an answer" in err and "editor dialog answer" in err


def test_verify_a_failed_check_is_not_a_missing_editor(ncg, fake_unity, monkeypatch):
    monkeypatch.setenv("FAKE_UNITY_MODE", "failed")
    code, _, err = ncg("verify", "--unity", "Bot_BT")
    assert code == 1 and "NullReferenceException" in err and "stopped answering" not in err


def test_pipeline_version(tmp_path):
    from nodecanvas_cli.verify import pipeline_version

    assert pipeline_version(tmp_path) is None
    (tmp_path / "Packages").mkdir()
    (tmp_path / "Packages/manifest.json").write_text('{"dependencies": {"com.unity.pipeline": "1.0.0-pre.1"}}')
    assert pipeline_version(tmp_path) == (1, 0, 0)
    (tmp_path / "Packages/packages-lock.json").write_text(
        '{"dependencies": {"com.unity.pipeline": {"version": "0.9.1-exp.1"}}}'
    )
    assert pipeline_version(tmp_path) == (0, 9, 1)


def test_result_unwraps_both_forms():
    from nodecanvas_cli.verify import _result

    assert _result(False) is False and _result("x") == "x" and _result(None) is None
    assert _result({"success": True, "data": {"result": {"success": True, "result": True}}}) is True
