"""The sh launchers bundled with the Claude Code plugin (bin/ and skills/nodecanvas-cli/scripts/)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nodecanvas_cli import __version__

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "nodecanvas-cli"
SCRIPTS = ROOT / "skills" / "nodecanvas-cli" / "scripts" / "nodecanvas-cli"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="sh launcher")


def run(launcher, env=None, python=None):
    env = {k: v for k, v in (env or os.environ).items() if k != "NODECANVAS_PYTHON"}
    if python is not None:
        env["NODECANVAS_PYTHON"] = str(python)
    return subprocess.run([str(launcher), "--version"], capture_output=True, text=True, env=env)


def tools_dir(tmp_path):
    """A PATH directory with only the external commands the launcher needs (no Python)."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("readlink", "dirname"):
        (tools / name).symlink_to(shutil.which(name))
    return tools


def test_launchers_identical():
    assert BIN.read_bytes() == SCRIPTS.read_bytes()
    assert b"\r" not in BIN.read_bytes()


@pytest.mark.parametrize("launcher", [BIN, SCRIPTS])
def test_direct(launcher):
    r = run(launcher)
    assert r.returncode == 0, r.stderr
    assert __version__ in r.stdout


@pytest.mark.parametrize("launcher", [BIN, SCRIPTS])
def test_symlink_chain(tmp_path, launcher):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "nc").symlink_to(launcher)
    (tmp_path / "a" / "nc").symlink_to(Path("..") / "b" / "nc")
    for link in (tmp_path / "b" / "nc", tmp_path / "a" / "nc"):
        r = run(link)
        assert r.returncode == 0, r.stderr
        assert __version__ in r.stdout


def test_skips_old_python(tmp_path):
    shims = tmp_path / "shims"
    shims.mkdir()
    old = shims / "python3"
    old.write_text("#!/bin/sh\n[ \"$1\" = -c ] && exit 1\necho old python used >&2\nexit 3\n")
    old.chmod(0o755)
    (shims / "python3.11").symlink_to(sys.executable)
    r = run(BIN, {"PATH": f"{shims}{os.pathsep}{tools_dir(tmp_path)}"})
    assert r.returncode == 0, r.stderr
    assert __version__ in r.stdout


def test_no_python(tmp_path):
    r = run(BIN, {"PATH": str(tools_dir(tmp_path))})
    assert r.returncode == 2
    assert "Python 3.9+ is required" in r.stderr


def test_python_path_with_spaces(tmp_path):
    support = tmp_path / "Application Support" / "My Python"
    support.mkdir(parents=True)
    (support / "python3").symlink_to(sys.executable)
    r = run(BIN, {"PATH": str(tools_dir(tmp_path))}, python=support / "python3")
    assert r.returncode == 0, r.stderr
    assert __version__ in r.stdout


def test_bad_python_override(tmp_path):
    r = run(BIN, python=tmp_path / "no such" / "python3")
    assert r.returncode == 2
    assert "NODECANVAS_PYTHON=" in r.stderr and "no such" in r.stderr


def test_plugin_dir_with_spaces(tmp_path):
    plugin = tmp_path / "Application Support" / "nodecanvas cli"
    (plugin / "bin").mkdir(parents=True)
    shutil.copy2(BIN, plugin / "bin" / "nodecanvas-cli")
    (plugin / "src").symlink_to(ROOT / "src")
    (tmp_path / "nc").symlink_to(plugin / "bin" / "nodecanvas-cli")
    for launcher in (plugin / "bin" / "nodecanvas-cli", tmp_path / "nc"):
        r = run(launcher)
        assert r.returncode == 0, r.stderr
        assert __version__ in r.stdout
