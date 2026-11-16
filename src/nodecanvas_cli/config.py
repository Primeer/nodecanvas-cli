"""Optional per-project settings from ``nodecanvas-cli.json`` in the Unity project root."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CONFIG_NAME = "nodecanvas-cli.json"
SIZING_MODES = ("content", "fixed")


class ConfigError(Exception):
    pass


@dataclass
class Layout:
    grid: float = 20.0  # NodeCanvas editor grid (GraphEditor.GRID_SIZE)
    # "content": gaps are clear space between node edges, sizes come from the node's text.
    # "fixed": nodes count as points and gaps are distances between their top-left corners.
    sizing: str = "content"
    level_gap: Optional[float] = None  # parent -> child: 100 clear px ("content"), 200 px ("fixed")
    sibling_gap: Optional[float] = None  # neighbours on one row: 60 clear px ("content"), 300 px ("fixed")
    char_width: Optional[float] = None  # px per regular character, see `nodecanvas-cli calibrate --unity`

    def __post_init__(self) -> None:
        fixed = self.sizing == "fixed"
        if self.level_gap is None:
            self.level_gap = 200.0 if fixed else 100.0
        if self.sibling_gap is None:
            self.sibling_gap = 300.0 if fixed else 60.0


@dataclass
class Scaffold:
    comments: bool = True  # TODO comments in generated method bodies
    indent: str = ""  # "" = detect from neighbouring .cs files (default 4 spaces)


@dataclass
class Config:
    layout: Layout
    scaffold: Scaffold = None


def load(root: Path) -> Config:
    path = root / CONFIG_NAME
    layout = {}
    scaffold = Scaffold()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ConfigError(f"{path}: invalid JSON: {e}") from e
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: expected a JSON object")
        for section in ("layout", "scaffold"):
            if not isinstance(raw.get(section) or {}, dict):
                raise ConfigError(f"{path}: {section} must be an object")
        for k, v in (raw.get("layout") or {}).items():
            if k == "sizing":
                if v not in SIZING_MODES:
                    raise ConfigError(f"{path}: layout.sizing must be one of {', '.join(SIZING_MODES)}")
                layout[k] = v
                continue
            if not hasattr(Layout(), k):
                raise ConfigError(f"{path}: unknown layout setting {k!r}")
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise ConfigError(f"{path}: layout.{k} must be a positive number")
            layout[k] = float(v)
        for k, v in (raw.get("scaffold") or {}).items():
            if k == "comments" and isinstance(v, bool):
                scaffold.comments = v
            elif k == "indent" and isinstance(v, (int, str)):
                scaffold.indent = " " * v if isinstance(v, int) else v
            else:
                raise ConfigError(f"{path}: bad scaffold setting {k!r}")
    return Config(Layout(**layout), scaffold)
