"""``nodecanvas-cli verify --unity``: load graphs in the open Unity Editor and report what NodeCanvas sees.

The Editor is driven through the Unity CLI (``unity command eval_file``), which talks to the
project's Pipeline package (``com.unity.pipeline``). nodecanvas-cli waits for script compilation, then
re-imports each graph, deserializes and validates it, and collects node/task warnings, Missing
wrappers that survived loading, and console messages logged meanwhile.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .container import Slot
from .lint import static_issues
from .model import Graph, walk

SNIPPET = Path(__file__).with_name("unity_verify.cs.txt")
BUSY = "return EditorApplication.isCompiling || EditorApplication.isUpdating;"
# Warnings the NodeCanvas editor shows for a graph asset viewed without an owner; not a defect of the graph.
CONTEXT_WARNINGS = ("target agent is null",)
STATUS_TEXT = {
    "not-loaded": "Unity could not load the asset as a NodeCanvas graph",
    "deserialize-failed": "NodeCanvas failed to deserialize the graph",
    "owner-not-found": "no GraphOwner with this file id in the prefab",
    "scene-not-open": "skipped: the scene is not open in the Editor",
    "exception": "verification threw an exception",
}


# Exit codes of the Unity CLI worth waiting out: no Editor answered, the request ran out of time, it is busy
# (loading, compiling, reloading scripts).
TRANSIENT = {12, 14, 17}
AWAITING_INPUT = 18


class UnityUnavailable(Exception):
    """The Editor cannot be reached (exit code 2)."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class UnityFailed(Exception):
    """The Editor answered, but the verification snippet did not run."""


# ---------------------------------------------------------------------------- CLI transport


def unity_executable() -> Optional[str]:
    return os.environ.get("NODECANVAS_CLI_UNITY") or shutil.which("unity")


def _find(obj, pred):
    """First value in a nested JSON structure for which ``pred(key, value)`` holds."""
    stack = [(None, obj)]
    while stack:
        k, v = stack.pop(0)
        if pred(k, v):
            return v
        if isinstance(v, dict):
            stack.extend(v.items())
        elif isinstance(v, list):
            stack.extend((None, x) for x in v)
    return None


def _messages(doc) -> str:
    errors = _find(doc, lambda k, v: k == "errors" and isinstance(v, list) and v) or []
    texts = [e.get("message") or e.get("code") for e in errors if isinstance(e, dict)]
    for key in ("error", "errorDetails"):
        v = _find(doc, lambda k, x: k == key and isinstance(x, str) and x)
        if v:
            texts.append(v)
    return "; ".join(t for t in texts if t)


def _diagnostics(doc) -> List[str]:
    diags = _find(doc, lambda k, v: k == "diagnostics" and isinstance(v, list) and v) or []
    out = []
    for d in diags:
        if isinstance(d, dict) and str(d.get("severity", "")).lower() == "error":
            out.append(f"line {d.get('line', '?')}: {d.get('message', '')}")
    return out


class Editor:
    def __init__(self, exe: str, project: Path, timeout: float):
        self.exe = exe
        self.project = project
        self.timeout = timeout

    def command(self, name: str, *args: str, timeout: Optional[float] = None) -> dict:
        t = timeout or self.timeout
        argv = [
            self.exe,
            "command",
            name,
            *args,
            "--project-path",
            str(self.project),
            "--json",
            "--no-banner",
            "--non-interactive",
            "--timeout",
            str(int(t)),
            "--caller",
            "plugin",
            "--skill",
            "nodecanvas-cli",
        ]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=t + 30)
        except FileNotFoundError as e:
            raise UnityUnavailable(f"cannot run {self.exe}: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise UnityFailed(f"unity command {name} did not answer in {int(t)} s") from e
        try:
            doc = json.loads(p.stdout) if p.stdout.strip() else {}
        except ValueError:
            doc = {"raw": p.stdout}
        ok = (
            p.returncode == 0
            and doc.get("success", True) is not False
            and _find(doc, lambda k, v: k == "success" and v is False) is None
        )
        if not ok:
            diags = _diagnostics(doc)
            if diags:
                raise UnityFailed("the verification snippet does not compile in this project: " + "; ".join(diags[:5]))
            msg = _messages(doc) or (p.stderr.strip().splitlines() or [f"exit code {p.returncode}"])[-1]
            if p.returncode == AWAITING_INPUT:
                raise UnityFailed(f"a dialog in the Editor waits for an answer (`unity command editor dialog`): {msg}")
            raise UnityUnavailable(msg, p.returncode)
        return doc

    def _status(self, *args: str, timeout: float = 30) -> dict:
        """``unity status`` for this project; it reads the Editor's state without connecting to it."""
        argv = [self.exe, "status", "--project-path", str(self.project), *args, "--json", "--no-banner"]
        argv.append("--non-interactive")
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 30)
            return json.loads(p.stdout) if p.stdout.strip() else {}
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return {}

    def _editor_open(self, status: dict) -> bool:
        """The project has an Editor: listed by ``unity status`` or holding Unity's lock on the project."""
        count = _find(status, lambda k, v: k == "count" and isinstance(v, int))
        return bool(count) or (self.project / "Temp" / "UnityLockfile").exists()

    def connect(self, limit: float, log=None) -> None:
        """First contact. An Editor that is compiling or reloading scripts (right after `unity recompile`, say)
        drops its Pipeline connection for a while, which is not the same as no Editor at all: wait for that one."""
        deadline = time.monotonic() + limit
        noted = False
        while True:
            try:
                self.refresh()
                return
            except UnityUnavailable as e:
                status = self._status()
                errors = _find(status, lambda k, v: k == "compileErrors" and isinstance(v, list) and v)
                if errors:
                    texts = [x.get("message", str(x)) if isinstance(x, dict) else str(x) for x in errors[:5]]
                    raise UnityFailed("the Editor is in Safe Mode, scripts do not compile: " + "; ".join(texts)) from e
                listed = bool(_find(status, lambda k, v: k == "count" and isinstance(v, int)))
                if not self._editor_open(status) or time.monotonic() > deadline:
                    raise
                if noted and listed and e.code not in TRANSIENT:
                    raise  # it is there and ready, and this is not about being busy
                if log is not None and not noted:
                    log("the Editor is not answering yet (compiling or reloading scripts?), waiting…")
                noted = True
                left = max(1, int(deadline - time.monotonic()))
                if self._status("--until-ready", "--timeout", str(left), timeout=left).get("success") is not True:
                    time.sleep(1.0)

    def busy(self) -> bool:
        doc = self.command("eval", BUSY, timeout=60)
        return _find(doc, lambda k, v: k == "result" and isinstance(v, bool)) is True

    def refresh(self) -> None:
        self.command("eval", "AssetDatabase.Refresh(); " + BUSY, timeout=120)

    def wait_ready(self, limit: float, log=None) -> None:
        """Wait until the Editor is neither compiling nor importing; a domain reload drops the connection
        for a while, so failures after the first successful call count as "busy"."""
        deadline = time.monotonic() + limit
        noted = False
        while True:
            try:
                if not self.busy():
                    return
            except (UnityUnavailable, UnityFailed):
                pass
            if time.monotonic() > deadline:
                raise UnityFailed(f"the Editor is still compiling or importing after {int(limit)} s")
            if log is not None and not noted:
                log("waiting for the Editor to finish compiling/importing…")
                noted = True
            time.sleep(1.0)


# ---------------------------------------------------------------------------- snippet


def _cs(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_snippet(targets: Iterable[Tuple[str, str]], resave: bool) -> str:
    text = SNIPPET.read_text(encoding="utf-8")
    items = ", ".join(f"new string[] {{ {_cs(p)}, {_cs(a)} }}" for p, a in targets)
    return text.replace("/*TARGETS*/", items).replace("/*RESAVE*/", "true" if resave else "false")


def parse_report(doc) -> dict:
    def is_report(k, v):
        if isinstance(v, dict) and v.get("nodecanvas_cli_verify") == 1:
            return True
        return isinstance(v, str) and v.lstrip().startswith('{"nodecanvas_cli_verify"')

    found = _find(doc, is_report)
    if found is None:
        raise UnityFailed("the Editor returned no verification report")
    return json.loads(found) if isinstance(found, str) else found


# ---------------------------------------------------------------------------- command


def target_of(slot: Slot) -> Tuple[str, str]:
    return slot.path, (slot.anchor or "") if slot.bound else ""


def _missing_wrappers(g: Graph) -> int:
    """Missing* wrappers saved in the file (variables, nodes, tasks)."""
    n = sum(1 for v in g.variables.values() if "MissingVariableType" in v.get("$type", ""))
    for owner in list(g.nodes) + list(g.connections):
        n += sum(1 for _, d in walk(owner) if "Framework.Internal.Missing" in (d.get("$type") or ""))
    return n


def _compare(g: Graph, r: dict) -> List[Tuple[str, str, str]]:
    out = []
    wrappers = _missing_wrappers(g)
    if wrappers and not any("missing" in (i.get("text") or "") for i in r.get("issues", [])):
        out.append(
            (
                "info",
                "-",
                f"{wrappers} Missing wrapper(s) in the file were restored on load "
                "(`nodecanvas-cli lint --min info` lists them; --resave drops them)",
            )
        )
    if r.get("nodes") is not None and r["nodes"] != len(g.nodes):
        out.append(("error", "-", f"Unity loaded {r['nodes']} nodes, the file has {len(g.nodes)}"))
    if r.get("variables") is not None and r["variables"] != len(g.variables):
        out.append(("error", "bb", f"Unity loaded {r['variables']} variables, the file has {len(g.variables)}"))
    if g.nodes and r.get("prime") not in (None, 0):
        out.append(("error", "#0", f"Unity's start node is #{r['prime']}, nodecanvas-cli's is #0"))
    return out


def _console(entries: List[dict], verbose: bool) -> List[Tuple[str, str]]:
    sev = {"Error": "error", "Exception": "error", "Assert": "error", "Warning": "warning"}
    out, seen = [], set()
    for e in entries:
        s = sev.get(e.get("type"), "info")
        if s == "info" and not verbose:
            continue
        text = re.sub(r"</?b>", "", (e.get("text") or "").strip().split("\n")[0])
        # NodeCanvas logs reach us twice: from its own logger ("(Tag) text") and forwarded to the
        # console ("(Tag Error): text")
        text = re.sub(r"^\((.+?)(?: (?:Error|Warning|Log|Exception|Assert))?\):? ", r"(\1) ", text)
        if (s, text) in seen:
            continue
        seen.add((s, text))
        out.append((s, text[:300]))
    return out


def _run_snippet(editor: Editor, path: str, timeout: float, deadline: float, log) -> dict:
    """``eval_file``, again after a wait if a script reload took the Editor away in the meantime."""
    while True:
        try:
            return editor.command("eval_file", path, str(int(timeout * 1000)))
        except UnityUnavailable as e:
            left = deadline - time.monotonic()
            if e.code not in TRANSIENT or left <= 0:
                raise
            editor.wait_ready(left, log)


def cmd_verify(ws, a) -> int:
    if not a.unity:
        print(
            "error: verify runs in the Unity Editor; pass --unity (`nodecanvas-cli lint` does the static checks)",
            file=sys.stderr,
        )
        return 2
    if a.changed and a.graphs:
        print("error: name graphs or pass --changed, not both", file=sys.stderr)
        return 2
    if a.changed:
        slots = ws.changed_slots()
        if not slots:
            print("no changed graphs (git diff HEAD + untracked files)")
            return 0
    elif a.graphs:
        slots = [ws.find(q, a.kind) for q in a.graphs]
    else:
        print("error: name graphs to verify or pass --changed", file=sys.stderr)
        return 2
    seen, unique = set(), []
    for s in slots:
        if s.address not in seen:
            seen.add(s.address)
            unique.append(s)
    slots = unique

    exe = unity_executable()
    if exe is None:
        print("error: the Unity CLI (`unity`) is not on PATH; install it or set NODECANVAS_CLI_UNITY", file=sys.stderr)
        return 2
    editor = Editor(exe, ws.project.root, a.timeout)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    try:
        editor.connect(a.timeout, log)
    except UnityFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except UnityUnavailable as e:
        print(
            f"error: no Unity Editor with the Pipeline package is reachable for {ws.project.root}: {e}\n"
            "  open the project in Unity (add the package once with `unity pipeline install`) and try again",
            file=sys.stderr,
        )
        return 2
    deadline = time.monotonic() + a.timeout
    try:
        editor.wait_ready(a.timeout, log)
        fd, tmp = tempfile.mkstemp(prefix="nodecanvas_cli_verify_", suffix=".cs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(build_snippet([target_of(s) for s in slots], a.resave))
            doc = _run_snippet(editor, tmp, a.timeout, deadline, log)
        finally:
            os.unlink(tmp)
        report = parse_report(doc)
    except UnityUnavailable as e:
        print(f"error: the Unity Editor stopped answering: {e}", file=sys.stderr)
        return 2
    except UnityFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    by_target = {(r.get("path"), r.get("anchor") or ""): r for r in report.get("graphs", [])}
    rows = []
    failed = False
    for s in slots:
        r = by_target.get(target_of(s), {"status": "not-reported", "issues": [], "console": []})
        try:
            g = ws.load(s)
        except Exception:  # noqa: BLE001 - the file may be unreadable for nodecanvas-cli but fine for Unity
            g = None
        issues = [
            (i["severity"], i["at"], i["text"])
            for i in r.get("issues", [])
            if not any(w in i.get("text", "") for w in CONTEXT_WARNINGS)
        ]
        issues = [
            ("error" if t.startswith("* ") else sev, at, t[2:] if t.startswith("* ") else t) for sev, at, t in issues
        ]
        if g is not None and r.get("status") == "ok":
            issues += _compare(g, r)
        if g is not None:
            # what Unity cannot see: types that convert (through GetComponent, say) and still deliver nothing
            issues += [(i.severity, i.at, f"[{i.code}] {i.message}") for i in static_issues(ws, g)]
        console = _console(r.get("console", []), a.verbose)
        status = r.get("status", "?")
        bad = (
            status not in ("ok", "scene-not-open")
            or any(i[0] == "error" for i in issues)
            or any(c[0] == "error" for c in console)
        )
        failed = failed or bad
        rows.append(
            {
                "graph": s.address,
                "status": status,
                "nodes": r.get("nodes"),
                "variables": r.get("variables"),
                "prime": r.get("prime"),
                "saved": r.get("saved", False),
                "issues": [{"severity": i[0], "at": i[1], "message": i[2]} for i in issues],
                "console": [{"severity": c[0], "message": c[1]} for c in console],
                "ok": not bad,
            }
        )
    if a.json:
        print(json.dumps({"unity": report.get("unity"), "graphs": rows}, ensure_ascii=False, indent=1))
        return 1 if failed else 0
    if report.get("windows"):
        print(
            "note: NodeCanvas editor window open (" + ", ".join(report["windows"]) + "); "
            "close it if it shows one of these graphs, it may hold stale data"
        )
    for row in rows:
        mark = "skip" if row["status"] == "scene-not-open" else ("ok  " if row["ok"] else "FAIL")
        if row["status"] == "ok":
            detail = f"nodes={row['nodes']} vars={row['variables']}" + (" resaved" if row["saved"] else "")
        else:
            detail = STATUS_TEXT.get(row["status"], row["status"])
        print(f"{mark} {row['graph']}  {detail}")
        for i in row["issues"]:
            print(f"  {i['severity']:<7} {i['at']:<10} {i['message']}")
        for c in row["console"]:
            print(f"  {c['severity']:<7} {'console':<10} {c['message']}")
    bad = sum(1 for r in rows if not r["ok"])
    print(f"{len(rows)} graphs verified in Unity {report.get('unity', '?')}: {bad} with problems")
    return 1 if failed else 0


def register(sub) -> None:
    s = sub.add_parser("verify", help="load graphs in the open Unity Editor and report NodeCanvas errors")
    s.add_argument("graphs", nargs="*", help="graphs to verify")
    s.add_argument("--unity", action="store_true", help="run the check in the open Unity Editor (required)")
    s.add_argument("--changed", action="store_true", help="verify graphs in files changed since HEAD (git)")
    s.add_argument(
        "--resave",
        action="store_true",
        help="let NodeCanvas re-serialize graph assets (drops recoverable Missing wrappers; changes files)",
    )
    s.add_argument(
        "--timeout", type=float, default=300, help="seconds to wait for compilation and the check (default 300)"
    )
    s.add_argument("--verbose", action="store_true", help="also show info-level console messages")
    s.add_argument("--kind", choices=["bt", "fsm", "dt"], help="pick the graph of this kind when the name is ambiguous")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_verify)
