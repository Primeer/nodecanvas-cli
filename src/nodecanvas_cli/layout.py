"""Node placement for edited graphs.

Only nodes an edit created or moved are positioned; existing nodes move only when they have to
make room (siblings to the right of an insertion, branches that would overlap). Rules:

* every coordinate set here lies on the editor grid (``Layout.grid``);
* ``_position`` is a node's top-left corner, and sizes (``sizes``, by ``id(node)``) are rounded up to the grid;
* a Behaviour Tree child sits ``level_gap`` below its parent's bottom edge;
* nodes on one row keep ``sibling_gap`` between their edges, and siblings keep a left-to-right order that
  matches their child order (the NodeCanvas editor re-sorts children by the centre x when a node is dragged).

With ``sizing: fixed`` every node has size zero, so the same formulas measure distances between corners.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Set, Tuple

from .config import Layout
from .model import Graph
from .nodesize import MIN_SIZE, Size

MAX_PASSES = 200


class Layouter:
    def __init__(self, g: Graph, cfg: Layout, sizes: Optional[Dict[int, Size]] = None):
        self.g = g
        self.cfg = cfg
        self.sizes: Dict[int, Size] = sizes or {}

    # ------------------------------------------------------------------ helpers

    def snap(self, v: float) -> float:
        return float(round(v / self.cfg.grid) * self.cfg.grid)

    def snap_up(self, v: float) -> float:
        return float(math.ceil(v / self.cfg.grid - 1e-9) * self.cfg.grid)

    @property
    def content(self) -> bool:
        return self.cfg.sizing == "content"

    def size(self, n: dict) -> Size:
        if not self.content:
            return 0.0, 0.0
        w, h = self.sizes.get(id(n), MIN_SIZE)
        return self.snap_up(w), self.snap_up(h)

    @staticmethod
    def pos(n: dict) -> Optional[tuple]:
        p = n.get("_position")
        if not isinstance(p, dict):
            return None
        return float(p.get("x", 0.0)), float(p.get("y", 0.0))

    def set(self, n: dict, x: float, y: float) -> None:
        from .ops import set_position

        set_position(n, x, y)

    def right(self, n: dict) -> float:
        return self.pos(n)[0] + self.size(n)[0]

    def below(self, n: dict) -> float:
        """Where a child of ``n`` starts."""
        return self.pos(n)[1] + self.size(n)[1] + self.cfg.level_gap

    def child_shift(self, n: dict) -> float:
        """How far below ``n``'s top a child starts."""
        return self.size(n)[1] + self.cfg.level_gap

    def subtree(self, n: dict) -> List[dict]:
        out, seen, stack = [], set(), [n]
        while stack:
            m = stack.pop()
            if id(m) in seen:
                continue
            seen.add(id(m))
            out.append(m)
            stack.extend(self.g.children(m))
        return out

    def translate(self, roots: List[dict], dx: float, dy: float, keep: Set[int] = frozenset()) -> None:
        """Shift the subtrees of ``roots`` by (dx, dy); nodes whose id is in ``keep`` stay put."""
        seen: Set[int] = set(keep)
        for r in roots:
            for m in self.subtree(r):
                if id(m) in seen:
                    continue
                seen.add(id(m))
                p = self.pos(m) or (0.0, 0.0)
                self.set(m, p[0] + dx, p[1] + dy)

    def parent(self, n: dict) -> Optional[dict]:
        ps = self.g.parents(n)
        return ps[0] if ps else None

    def ancestors(self, n: dict) -> List[dict]:
        out, cur, guard = [], self.parent(n), 0
        while cur is not None and guard < 10000:
            out.append(cur)
            cur = self.parent(cur)
            guard += 1
        return out

    def overlaps(self, a: dict, b: dict) -> bool:
        """The rectangles of ``a`` and ``b``, with the gaps a row and half a level, intersect."""
        ax, ay = self.pos(a)
        bx, by = self.pos(b)
        return self._hit((ax, ay), self.size(a), (bx, by), self.size(b))

    def _hit(self, ap: Tuple[float, float], asize: Size, bp: Tuple[float, float], bsize: Size) -> bool:
        gx, gy = self.cfg.sibling_gap, self.cfg.level_gap / 2
        return (
            ap[0] < bp[0] + bsize[0] + gx
            and bp[0] < ap[0] + asize[0] + gx
            and ap[1] < bp[1] + bsize[1] + gy
            and bp[1] < ap[1] + asize[1] + gy
        )

    def _row_slots(self, kids: List[dict], centre: float) -> List[float]:
        """Left edges for ``kids`` side by side, centred on ``centre``."""
        gap = self.cfg.sibling_gap
        total = sum(self.size(k)[0] for k in kids) + gap * (len(kids) - 1)
        x, out = centre - total / 2, []
        for k in kids:
            out.append(x)
            x += self.size(k)[0] + gap
        return out

    # ------------------------------------------------------------------ BT

    def slot_parent(self, n: dict, positioned: Set[int]) -> Optional[dict]:
        """The parent ``n`` gets its slot under: with several parents (a Merge), the one where it has
        the most already positioned siblings, so its x satisfies the strictest child order."""
        ps = [p for p in self.g.parents(n) if self.pos(p) is not None] or self.g.parents(n)
        if len(ps) < 2:
            return ps[0] if ps else None

        def siblings(p: dict) -> int:
            return sum(1 for k in self.g.children(p) if k is not n and id(k) in positioned)

        return max(ps, key=siblings)  # max() keeps the first on ties

    def place(self, n: dict, positioned: Set[int]) -> None:
        """Put ``n`` (and its subtree, keeping its internal layout) into its slot under its parent.

        A node with several parents goes ``level_gap`` below the lowest of them; next to positioned
        siblings it joins their row unless that row is too close to the parent."""
        gap, p = self.cfg.sibling_gap, self.slot_parent(n, positioned)
        if p is None:
            if self.pos(n) is None:
                self.set(n, 0.0, 0.0)
                self.place_new_children(n)
            return
        px, _ = self.pos(p) or (0.0, 0.0)
        kids = self.g.children(p)
        i = next(k for k, c in enumerate(kids) if c is n)
        left = next((kids[j] for j in range(i - 1, -1, -1) if id(kids[j]) in positioned and self.pos(kids[j])), None)
        right = next(
            (kids[j] for j in range(i + 1, len(kids)) if id(kids[j]) in positioned and self.pos(kids[j])), None
        )
        w = self.size(n)[0]
        if left is not None and right is not None and self.pos(right)[0] - self.right(left) >= w + 2 * gap:
            x = self.right(left) + (self.pos(right)[0] - self.right(left) - w) / 2
        elif left is not None:
            x = self.right(left) + gap
        elif right is not None:
            x = self.pos(right)[0] - gap - w
        else:
            x = self._row_slots(kids, px + self.size(p)[0] / 2)[i]
        y = max(
            [self.pos(q)[1] + self.child_shift(q) for q in self.g.parents(n) if self.pos(q) is not None]
            or [self.cfg.level_gap]
        )
        if self.content:
            y = max([y] + [self.pos(s)[1] for s in (left, right) if s is not None])
        x, y = self.snap(x), self.snap(y)
        cur = self.pos(n)
        if cur is None:
            self.set(n, x, y)
            self.place_new_children(n)
        else:
            self.translate([n], x - cur[0], y - cur[1])
        positioned.add(id(n))
        self.keep_sibling_order(p)

    def place_new_children(self, n: dict) -> None:
        kids = [k for k in self.g.children(n) if self.pos(k) is None]
        if not kids:
            return
        slots = self._row_slots(kids, self.pos(n)[0] + self.size(n)[0] / 2)
        y = self.snap(self.below(n))
        for k, x in zip(kids, slots):
            self.set(k, self.snap(x), y)
            self.place_new_children(k)

    def keep_sibling_order(self, parent: dict) -> None:
        kids = self.g.children(parent)
        for i in range(1, len(kids)):
            if self.pos(kids[i - 1]) is None or self.pos(kids[i]) is None:
                continue
            need = self.right(kids[i - 1]) + self.cfg.sibling_gap - self.pos(kids[i])[0]
            if need > 0:
                self.translate(kids[i:], self.snap_up(need), 0.0)

    def resolve(self, n: dict) -> None:
        """Push branches apart until no node of ``n``'s subtree overlaps a node outside it."""
        for _ in range(MAX_PASSES):
            inside = {id(m) for m in self.subtree(n)}
            hit = None
            for m in self.subtree(n):
                if self.pos(m) is None:
                    continue
                for o in self.g.nodes:
                    if id(o) not in inside and self.pos(o) is not None and self.overlaps(m, o):
                        hit = (m, o)
                        break
                if hit:
                    break
            if hit is None:
                return
            self.separate(n, *hit)

    def separate(self, n: dict, m: dict, other: dict) -> None:
        """Shift one side of the overlap right. With shared children (Merge) a branch can reach the
        other side too, so the moved set always excludes the side that stays: every call makes progress."""
        gap = self.cfg.sibling_gap
        mine = {id(x) for x in self.subtree(n)}
        n_path = [n] + self.ancestors(n)
        n_ids = {id(x): k for k, x in enumerate(n_path)}
        branch_o, cur = other, self.parent(other)
        while cur is not None and id(cur) not in n_ids:
            branch_o, cur = cur, self.parent(cur)
        past_other = self.snap_up(self.right(other) + gap - self.pos(m)[0])
        if cur is None:
            # unrelated tree (another root or an orphan): move our own top branch right
            self.move_mine([n_path[-1]], n, mine, other, past_other)
            return
        common = cur
        branch_n = n_path[n_ids[id(common)] - 1]
        kids = self.g.children(common)
        i_n = next(k for k, c in enumerate(kids) if c is branch_n)
        i_o = next(k for k, c in enumerate(kids) if c is branch_o)
        if i_o > i_n:
            self.translate(kids[i_o:], self.snap_up(self.right(m) + gap - self.pos(other)[0]), 0.0, keep=mine)
        else:
            self.move_mine(kids[i_n:], n, mine, branch_o, past_other)

    def move_mine(self, roots: List[dict], n: dict, mine: Set[int], stay: dict, dx: float) -> None:
        """Shift ``roots`` and ``n``'s whole subtree by ``dx``, leaving ``stay``'s subtree in place."""
        keep = {id(m) for m in self.subtree(stay)} - mine
        self.translate(roots + [n], dx, 0.0, keep=keep)

    def _depths(self, root: dict) -> Dict[int, int]:
        """Longest path from ``root``: a Merge goes below all of its parents."""
        limit = len(self.g.nodes)
        depth: Dict[int, int] = {}
        stack = [(root, 0)]
        while stack:
            n, d = stack.pop()
            if depth.get(id(n), -1) >= d or d > limit:
                continue
            depth[id(n)] = d
            stack.extend((k, d + 1) for k in self.g.children(n))
        return depth

    def _slot_lefts(self, root: dict) -> Dict[int, float]:
        """Left edges where leaves take consecutive slots, so subtrees never reach under each other, parents
        centre over their children, and a parent wider than its children pushes them apart."""
        gap = self.cfg.sibling_gap
        centres: Dict[int, float] = {}
        order: List[dict] = []
        seen: Set[int] = set()
        cursor = [0.0]

        def visit(n: dict) -> float:
            seen.add(id(n))
            start, first = cursor[0], len(order)
            order.append(n)
            w = self.size(n)[0]
            kids = [k for k in self.g.children(n) if id(k) not in seen]
            if not kids:
                c = start + w / 2
                cursor[0] = start + w + gap
            else:
                xs = [visit(k) for k in kids]
                c = (xs[0] + xs[-1]) / 2
                short = start - (c - w / 2)
                if short > 0:
                    for m in order[first + 1 :]:
                        centres[id(m)] += short
                    c += short
                    cursor[0] += short
                cursor[0] = max(cursor[0], c + w / 2 + gap)
            centres[id(n)] = c
            return c

        visit(root)
        return {id(m): centres[id(m)] - self.size(m)[0] / 2 for m in order}

    def _owners(self, root: dict, depth: Dict[int, int]) -> Dict[int, dict]:
        """The parent each node is laid out under: the shallowest one, the first reached on ties. This keeps
        the Merge of ``BinarySelector[X, Merge]`` with ``X -> Merge`` beside X, as a child of the selector."""
        owners: Dict[int, dict] = {}
        seen: Set[int] = set()

        def walk(n: dict) -> None:
            seen.add(id(n))
            for k in self.g.children(n):
                o = owners.get(id(k))
                if o is None or depth[id(n)] < depth[id(o)]:
                    owners[id(k)] = n
                if id(k) not in seen:
                    walk(k)

        walk(root)
        return owners

    def _contour_lefts(self, root: dict, depth: Dict[int, int], rows: Dict[int, float]) -> Optional[Dict[int, float]]:
        """Left edges where each subtree moves in as close to its left sibling as its contour allows.

        A depth is a row, so a contour is ``{depth: (left, right)}``. All values stay on the grid, which keeps
        the gaps exact. ``None`` when the walk missed a node, which takes a cycle in the graph."""
        gap, grid = self.cfg.sibling_gap, self.cfg.grid
        owners = self._owners(root, depth)
        lefts: Dict[int, float] = {}
        seen: Set[int] = set()

        def widen(contour: Dict[int, Tuple[float, float]], row: int, lo: float, hi: float) -> None:
            cur = contour.get(row)
            contour[row] = (lo, hi) if cur is None else (min(cur[0], lo), max(cur[1], hi))

        def visit(n: dict) -> Tuple[List[dict], Dict[int, Tuple[float, float]]]:
            seen.add(id(n))
            members: List[dict] = [n]
            contour: Dict[int, Tuple[float, float]] = {}
            first: Optional[float] = None
            last: Optional[float] = None
            for k in self.g.children(n):
                if id(k) in seen or owners[id(k)] is not n:
                    continue
                k_members, k_contour = visit(k)
                common = [contour[r][1] + gap - v[0] for r, v in k_contour.items() if r in contour]
                if common:
                    dx = max(common)
                elif contour:
                    dx = max(v[1] for v in contour.values()) + gap - min(v[0] for v in k_contour.values())
                else:
                    dx = 0.0
                centre = lefts[id(k)] + self.size(k)[0] / 2
                if last is not None and centre + dx <= last:  # the editor sorts children by the centre
                    dx = self.snap_up(last + grid - centre)
                for m in k_members:
                    lefts[id(m)] += dx
                for r, (lo, hi) in k_contour.items():
                    widen(contour, r, lo + dx, hi + dx)
                members += k_members
                last = centre + dx
                first = last if first is None else first
            w, h = self.size(n)
            left = 0.0 if first is None else self.snap((first + last) / 2 - w / 2)
            lefts[id(n)] = left
            row = depth[id(n)]
            widen(contour, row, left, left + w)
            if first is not None and h < rows[row]:
                # n is shorter than its row: its edges cross the rest of the row, so a taller neighbour stays out
                widen(contour, row, self.snap(first), self.snap_up(last))
            return members, contour

        visit(root)
        return lefts if len(seen) == len(depth) else None

    def relayout(self, root: dict) -> None:
        """Explicit tidy layout of ``root``'s subtree. By node size, subtrees pack by their contours; with
        ``sizing: fixed``, leaves take consecutive slots.

        The root keeps its x, snapped to the grid, and, when it has a parent, goes ``level_gap`` below it.
        """
        rp = self.pos(root) or (0.0, 0.0)
        parent = self.parent(root)
        top = self.below(parent) if parent is not None and self.pos(parent) is not None else rp[1]
        nodes = self.subtree(root)
        depth = self._depths(root)
        rows: Dict[int, float] = {}
        for m in nodes:
            rows[depth[id(m)]] = max(rows.get(depth[id(m)], 0.0), self.size(m)[1])
        ys: Dict[int, float] = {}
        y = top
        for d in sorted(rows):
            ys[d] = y
            y += rows[d] + self.cfg.level_gap
        lefts = self._contour_lefts(root, depth, rows) if self.content else None
        if lefts is None:
            lefts = self._slot_lefts(root)
        shift = rp[0] - lefts[id(root)]
        for m in nodes:
            self.set(m, self.snap(lefts[id(m)] + shift), self.snap(ys[depth[id(m)]]))
        self.resolve(root)

    # ------------------------------------------------------------------ FSM / DT

    def place_flat(self, n: dict) -> None:
        gap = self.cfg.sibling_gap
        size = self.size(n)
        src = next((p for p in self.g.parents(n) if self.pos(p)), None)
        others = [o for o in self.g.nodes if o is not n and self.pos(o)]
        if src is not None:
            x, y = self.right(src) + gap, self.pos(src)[1]
        elif others:
            x, y = max(self.right(o) for o in others) + gap, min(self.pos(o)[1] for o in others)
        else:
            x, y = 0.0, 0.0
        x, y = self.snap(x), self.snap(y)
        for _ in range(MAX_PASSES):
            if not any(self._hit((x, y), size, self.pos(o), self.size(o)) for o in others):
                break
            y += size[1] + self.cfg.level_gap
        self.set(n, x, y)
