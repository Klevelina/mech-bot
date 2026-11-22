"""Walk grid + time-sliced A* for mechfight. Standard library only.

Grid: square cells (default 2 m) over the terrain. Each cell has a surface height (terrain, or the top of
a prism standing on it), and an 8-bit mask of the directions you can walk out of it (climb <= step_up
onto prisms, <= slope limit on terrain, any drop down, no corner cutting).
"""
from __future__ import annotations

import array
import heapq
import json
import math
import os
import sys
import time
from collections import deque

SQ2 = math.sqrt(2.0)
# direction k: (dx, dy); bit 1<<k in the mask
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
OPP = [1, 0, 3, 2, 7, 6, 5, 4]
# a diagonal needs both of its orthogonal moves
DIAG_NEEDS = {4: (0, 2), 5: (0, 3), 6: (1, 2), 7: (1, 3)}


def _log(*a):
    print(*a, file=sys.stderr, flush=True)


class Nav:
    def __init__(self, map_dir: str, mech: dict, cell: float = 2.0):
        t0 = time.perf_counter()
        self.body_top = 8.5   # height above the ground a hiding spot must hide (the bot updates it as we grow)
        self.muzzle_z = 7.0   # our gun height, for spots that must keep a line of fire
        with open(os.path.join(map_dir, "v2", "world.json"), encoding="utf-8") as f:
            world = json.load(f)
        ter = world["terrain"]
        tnx, tny = int(ter["nx"]), int(ter["ny"])
        tx0, ty0, ts = float(ter["minX"]), float(ter["minY"]), float(ter["step"])
        hsc = float(ter.get("scale", 0.01))
        hm = array.array("h")
        with open(os.path.join(map_dir, "v2", ter.get("file", "terrain.i16")), "rb") as f:
            hm.frombytes(f.read())
        if sys.byteorder == "big":
            hm.byteswap()
        self.hm, self.tnx, self.tny, self.tx0, self.ty0, self.ts, self.hsc = hm, tnx, tny, tx0, ty0, ts, hsc

        step_up = float(mech.get("step_up", 1.2))
        slope = math.radians(float(mech.get("max_slope_deg", 50)))
        head = float(mech.get("height", 9))

        self.cell = cell
        self.ox, self.oy = tx0, ty0
        W = int(((tnx - 1) * ts) // cell)
        H = int(((tny - 1) * ts) // cell)
        N = W * H
        self.W, self.H, self.N = W, H, N

        # --- ground at cell centers (nearest terrain vertex)
        ground = array.array("f", [0.0]) * N
        vcol = [min(tnx - 1, int(round((i + 0.5) * cell / ts))) for i in range(W)]
        for j in range(H):
            vj = min(tny - 1, int(round((j + 0.5) * cell / ts)))
            row = vj * tnx
            base = j * W
            for i in range(W):
                ground[base + i] = hm[row + vcol[i]] * hsc

        # --- steep terrain: any 1-step vertex pair steeper than the slope limit
        lim = math.tan(slope) * ts / hsc  # in raw units
        steep = bytearray(N)
        for vj in range(tny - 1):
            r0 = hm[vj * tnx:(vj + 1) * tnx]
            r1 = hm[(vj + 1) * tnx:(vj + 2) * tnx]
            for vi, (a, b, c) in enumerate(zip(r0, r0[1:], r1)):
                if a - b > lim or b - a > lim or a - c > lim or c - a > lim:
                    ci = int(vi * ts / cell)
                    cj = int(vj * ts / cell)
                    if ci < W and cj < H:
                        steep[cj * W + ci] = 1

        # --- prisms: rasterize at half-cell resolution, keep the max top over each cell
        surf = array.array("f", ground)
        roof = bytearray(N)
        sub = cell / 2.0
        SW, SH = 2 * W, 2 * H
        ox, oy = self.ox, self.oy
        # fine (half-cell) solid spans for line-of-fire checks: tallest prism per sub-cell, its base and top
        ftop = array.array("f", [-1e9]) * (SW * SH)
        fbase = array.array("f", [0.0]) * (SW * SH)
        for p in world["prisms"]:
            pbase, ptop = float(p["base"]), float(p["top"])
            edges = []
            for r in [p["ring"]] + list(p.get("holes") or []):
                n = len(r) // 2
                for k in range(n):
                    x1, y1 = r[2 * k], r[2 * k + 1]
                    k2 = (k + 1) % n
                    x2, y2 = r[2 * k2], r[2 * k2 + 1]
                    if y1 != y2:
                        edges.append((x1, y1, (x2 - x1) / (y2 - y1), y2))
            ys = p["ring"][1::2]
            sj0 = max(0, int((min(ys) - oy) / sub))
            sj1 = min(SH - 1, int((max(ys) - oy) / sub) + 1)
            for sj in range(sj0, sj1 + 1):
                y = oy + (sj + 0.5) * sub
                xs = sorted(x1 + (y - y1) * k for (x1, y1, k, y2) in edges if (y1 <= y) != (y2 <= y))
                cj = sj >> 1
                rowc = cj * W
                rowf = sj * SW
                for a, b in zip(xs[0::2], xs[1::2]):
                    si0 = max(0, math.ceil((a - ox) / sub - 0.5))
                    si1 = min(SW - 1, math.floor((b - ox) / sub - 0.5))
                    for si in range(si0, si1 + 1):
                        fc = rowf + si
                        if ptop > ftop[fc]:
                            ftop[fc] = ptop
                            fbase[fc] = pbase
                        c = rowc + (si >> 1)
                        if pbase > ground[c] + head:
                            continue  # clears our head: walk under it
                        if ptop > surf[c]:
                            surf[c] = ptop
                        roof[c] = 1

        # --- blocked: map border, steep bare terrain
        blocked = bytearray(N)
        for i in range(W):
            blocked[i] = 1
            blocked[(H - 1) * W + i] = 1
        for j in range(H):
            blocked[j * W] = 1
            blocked[j * W + W - 1] = 1
        for c in range(N):
            if steep[c] and not roof[c]:
                blocked[c] = 1

        # --- direction masks
        terr_up = max(step_up, 0.9 * cell * math.tan(slope))
        prism_up = step_up + 0.1
        offs = [dx + dy * W for dx, dy in DIRS]
        self.offs = offs
        mask = bytearray(N)
        for j in range(1, H - 1):
            for c in range(j * W + 1, j * W + W - 1):
                s = surf[c]
                rc = roof[c]
                m = 0
                for k in range(4):
                    n = c + offs[k]
                    if blocked[n]:
                        continue
                    up = prism_up if (rc or roof[n]) else terr_up
                    if surf[n] - s <= up:
                        m |= 1 << k
                for k in range(4, 8):
                    a, b = DIAG_NEEDS[k]
                    if not (m >> a & 1 and m >> b & 1):
                        continue
                    n = c + offs[k]
                    if blocked[n]:
                        continue
                    up = prism_up if (rc or roof[n]) else terr_up * SQ2
                    if surf[n] - s <= up:
                        m |= 1 << k
                mask[c] = m

        # --- extra cost near walls (1 ring: +1.5, 2 rings: +0.5)
        extra = array.array("f", [0.0]) * N
        near = [c for c in range(N) if not blocked[c] and mask[c] != 0xFF]
        for c in near:
            extra[c] = 1.5
        for c in near:
            for o in offs:
                n = c + o
                if 0 <= n < N and extra[n] < 0.5:
                    extra[n] = 0.5

        # --- connected components (4-neighbour, both directions walkable)
        comp = array.array("i", [-1]) * N
        sizes = []
        for s0 in range(N):
            if comp[s0] != -1 or blocked[s0] or not mask[s0]:
                continue
            cid = len(sizes)
            comp[s0] = cid
            q = deque([s0])
            cnt = 0
            while q:
                c = q.popleft()
                cnt += 1
                m = mask[c]
                for k in range(4):
                    if m >> k & 1:
                        n = c + offs[k]
                        if comp[n] == -1 and mask[n] >> OPP[k] & 1:
                            comp[n] = cid
                            q.append(n)
            sizes.append(cnt)
        self.main = max(range(len(sizes)), key=sizes.__getitem__) if sizes else -1

        # --- cover cells: in the main component, next to a wall taller than a mech's eyes
        cover = []
        for c in near:
            if comp[c] != self.main:
                continue
            s = surf[c]
            for o in offs:
                if surf[c + o] - s > 8.0:
                    x, y = self.center(c)
                    cover.append((c, x, y))
                    break

        self.surf, self.roof, self.blocked, self.mask, self.extra, self.comp = surf, roof, blocked, mask, extra, comp
        self.ftop, self.fbase, self.sub, self.SW, self.SH = ftop, fbase, sub, SW, SH
        self.cover = cover
        self.build_s = time.perf_counter() - t0
        _log(f"nav: {W}x{H} cells, {len(sizes)} comps, main={sizes[self.main] if sizes else 0} cells, "
             f"{len(cover)} cover, built in {self.build_s:.2f}s")

    # --- geometry helpers
    def cell_of(self, x: float, y: float) -> int:
        i = min(self.W - 1, max(0, int((x - self.ox) / self.cell)))
        j = min(self.H - 1, max(0, int((y - self.oy) / self.cell)))
        return j * self.W + i

    def center(self, c: int):
        j, i = divmod(c, self.W)
        return self.ox + (i + 0.5) * self.cell, self.oy + (j + 0.5) * self.cell

    def ground_z(self, x: float, y: float) -> float:
        """Bilinear terrain height (ignores prisms)."""
        fx = (x - self.tx0) / self.ts
        fy = (y - self.ty0) / self.ts
        i = min(self.tnx - 2, max(0, int(fx)))
        j = min(self.tny - 2, max(0, int(fy)))
        u, v = fx - i, fy - j
        hm, n = self.hm, self.tnx
        a, b = hm[j * n + i], hm[j * n + i + 1]
        c, d = hm[(j + 1) * n + i], hm[(j + 1) * n + i + 1]
        return ((a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v) * self.hsc

    def nearest_main(self, x: float, y: float, max_r: int = 40) -> int:
        """Closest cell of the main ground component to (x, y)."""
        c0 = self.cell_of(x, y)
        if self.comp[c0] == self.main:
            return c0
        j0, i0 = divmod(c0, self.W)
        for r in range(1, max_r):
            best, bd = -1, 1e18
            for dj in range(-r, r + 1):
                for di in (-r, r) if abs(dj) != r else range(-r, r + 1):
                    i, j = i0 + di, j0 + dj
                    if 0 <= i < self.W and 0 <= j < self.H:
                        c = j * self.W + i
                        if self.comp[c] == self.main:
                            d = di * di + dj * dj
                            if d < bd:
                                best, bd = c, d
            if best >= 0:
                return best
        return c0

    def exposure(self, c, rays=12, reach=60.0):
        """How open cell c is: of `rays` horizontal directions from eye height, how many see `reach` m
        without hitting a building or terrain. Returns (open count, yaw to face = mean open direction or None)."""
        cx, cy = self.center(c)
        z0 = self.surf[c] + 8.0
        ftop, fbase, SW, SH, ox, oy, sub = self.ftop, self.fbase, self.SW, self.SH, self.ox, self.oy, self.sub
        hm, tnx, tny, tx0, ty0, ts, hsc = self.hm, self.tnx, self.tny, self.tx0, self.ty0, self.ts, self.hsc
        n_open, sx, sy = 0, 0.0, 0.0
        for k in range(rays):
            a = 2 * math.pi * k / rays
            dx, dy = math.cos(a), math.sin(a)
            blocked = False
            d = 2.0
            while d <= reach:
                px, py = cx + dx * d, cy + dy * d
                i, j = int((px - ox) / sub), int((py - oy) / sub)
                if not (0 <= i < SW and 0 <= j < SH):
                    blocked = True  # map edge
                    break
                fc = j * SW + i
                if fbase[fc] <= z0 <= ftop[fc]:
                    blocked = True
                    break
                vi, vj = int((px - tx0) / ts + 0.5), int((py - ty0) / ts + 0.5)
                if 0 <= vi < tnx and 0 <= vj < tny and hm[vj * tnx + vi] * hsc > z0:
                    blocked = True
                    break
                d += 2.0
            if not blocked:
                n_open += 1
                sx += dx
                sy += dy
        face = math.atan2(sy, sx) if n_open else None
        return n_open, face

    safe = None  # (cx, cy, r): hiding/flanking spots must be inside this circle (set by the bot from the zone)

    def in_safe(self, c: int) -> bool:
        if self.safe is None:
            return True
        x, y = self.center(c)
        sx, sy, sr = self.safe
        return (x - sx) ** 2 + (y - sy) ** 2 < sr * sr

    def los(self, ax, ay, az, bx, by, bz, skip=3.0, step=None) -> bool:
        """Line of sight/fire between two 3D points over the grid's surfaces (buildings + terrain).
        Ignores the first/last `skip` m (the shooter's and target's own cells). Trees are not solids."""
        dx, dy, dz = bx - ax, by - ay, bz - az
        d = math.hypot(dx, dy)
        sub = self.sub
        n = int(d / (step or sub))
        if n < 2:
            return True
        ftop, fbase, SW, SH, ox, oy = self.ftop, self.fbase, self.SW, self.SH, self.ox, self.oy
        hm, tnx, tny, tx0, ty0, ts, hsc = self.hm, self.tnx, self.tny, self.tx0, self.ty0, self.ts, self.hsc
        lo, hi = skip / d, 1.0 - skip / d
        for k in range(1, n):
            f = k / n
            if f < lo or f > hi:
                continue
            px, py, pz = ax + dx * f, ay + dy * f, az + dz * f
            i = int((px - ox) / sub)
            j = int((py - oy) / sub)
            if 0 <= i < SW and 0 <= j < SH:
                fc = j * SW + i
                if fbase[fc] <= pz <= ftop[fc]:
                    return False
            vi = int((px - tx0) / ts + 0.5)
            vj = int((py - ty0) / ts + 0.5)
            if 0 <= vi < tnx and 0 <= vj < tny and hm[vj * tnx + vi] * hsc > pz:
                return False
        return True

    def hide_cell(self, ex, ey, ez, mx, my, max_r=50.0, deadline=None):
        """Nearest walkable main-component cell (searching outward from us) that `(ex, ey, ez)` can't see.
        Checks our top (surface + 8.5 m) against the enemy's eye. Returns a cell or -1."""
        i0 = int((mx - self.ox) / self.cell)
        j0 = int((my - self.oy) / self.cell)
        W, H, comp, main, surf = self.W, self.H, self.comp, self.main, self.surf
        rmax = int(max_r / self.cell)
        for r in range(1, rmax + 1):
            cand = []
            for dj in range(-r, r + 1):
                for di in ((-r, r) if abs(dj) != r else range(-r, r + 1)):
                    i, j = i0 + di, j0 + dj
                    if 0 <= i < W and 0 <= j < H:
                        c = j * W + i
                        if comp[c] == main and self.in_safe(c):
                            cand.append(c)
            for n_c, c in enumerate(cand[::2] if r > 6 else cand):
                cx, cy = self.center(c)
                if not self.los(ex, ey, ez, cx, cy, surf[c] + self.body_top, skip=1.0):
                    return c
                if deadline is not None and n_c & 15 == 15 and time.perf_counter() > deadline:
                    return -1
            if deadline is not None and time.perf_counter() > deadline:
                return -1
        return -1

    def flank_cell(self, hide_from, shoot_at, mx, my, max_r=40.0, deadline=None):
        """Nearest walkable cell (searching outward from us) hidden from every eye in `hide_from`
        [(x, y, eye_z), ...] and, if `shoot_at` (x, y, z) is given, with a clear line of fire to it.
        Returns a cell or -1."""
        i0 = int((mx - self.ox) / self.cell)
        j0 = int((my - self.oy) / self.cell)
        W, H, comp, main, surf = self.W, self.H, self.comp, self.main, self.surf
        rmax = int(max_r / self.cell)
        n_c = 0
        for r in range(0, rmax + 1):
            cand = []
            for dj in range(-r, r + 1):
                for di in ((-r, r) if abs(dj) != r else range(-r, r + 1)):
                    i, j = i0 + di, j0 + dj
                    if 0 <= i < W and 0 <= j < H:
                        c = j * W + i
                        if comp[c] == main and self.in_safe(c):
                            cand.append(c)
            for c in cand[::2] if r > 6 else cand:
                n_c += 1
                if deadline is not None and n_c & 7 == 0 and time.perf_counter() > deadline:
                    return -1
                cx, cy = self.center(c)
                top = surf[c] + self.body_top
                if any(self.los(ex, ey, ez, cx, cy, top, skip=1.0) for ex, ey, ez in hide_from):
                    continue
                if shoot_at is not None and not self.los(cx, cy, surf[c] + self.muzzle_z, *shoot_at):
                    continue
                return c
        return -1

    def walk_cover(self, eyes, mx, my, body_h=9.0, max_steps=30, max_checks=140, deadline=None):
        """Hidden spot by WALKING distance: flood outward over walkable cells from us; a cell counts when no eye in
        `eyes` [(x, y, z)] sees our head or our middle there. Score = 2 x steps - 0.25 x (distance to the nearest eye,
        up to 80 m): close to walk to, not right next to them. Returns a cell or -1."""
        start = self.cell_of(mx, my)
        if self.comp[start] != self.main:
            start = self.nearest_main(mx, my)
        mask, offs, surf = self.mask, self.offs, self.surf
        steps = {start: 0}
        queue = [start]
        best, bs = -1, 1e18
        checked = 0
        qi = 0
        while qi < len(queue) and checked < max_checks:
            c = queue[qi]
            qi += 1
            st = steps[c]
            if st > max_steps or (best >= 0 and st * 2 > bs + 30):
                break
            j, i = divmod(c, self.W)
            if st > 0 and (i + j) % 2 == 0 and self.in_safe(c):
                checked += 1
                cx, cy = self.center(c)
                z0 = surf[c]
                if not any(self.los(ex, ey, ez, cx, cy, z0 + body_h, skip=1.0) or
                           self.los(ex, ey, ez, cx, cy, z0 + body_h * 0.5, skip=1.0) for ex, ey, ez in eyes):
                    near = min(math.hypot(ex - cx, ey - cy) for ex, ey, _ in eyes)
                    s = st * 2 - 0.25 * min(near, 80.0)
                    if s < bs:
                        best, bs = c, s
                if deadline is not None and checked & 7 == 0 and time.perf_counter() > deadline:
                    break
            m = mask[c]
            for k in range(8):
                if m & (1 << k):
                    n = c + offs[k]
                    if n not in steps:
                        steps[n] = st + 1
                        queue.append(n)
        return best

    def penalize(self, x: float, y: float, radius_cells: int = 2, amount: float = 25.0) -> None:
        j0, i0 = divmod(self.cell_of(x, y), self.W)
        for dj in range(-radius_cells, radius_cells + 1):
            for di in range(-radius_cells, radius_cells + 1):
                i, j = i0 + di, j0 + dj
                if 0 <= i < self.W and 0 <= j < self.H:
                    self.extra[j * self.W + i] += amount


class Search:
    """Weighted A*, run in slices with step(deadline). When finished, `path` is a list of cells
    (start..goal) and `reached` says whether it got to the goal (else it ends at the closest cell found)."""

    def __init__(self, nav: Nav, start: int, goal: int, weight: float = 1.5, max_exp: int = 120000):
        self.nav, self.start, self.goal, self.w, self.max_exp = nav, start, goal, weight, max_exp
        self.gj, self.gi = divmod(goal, nav.W)
        h0 = self._h(start)
        self.g = {start: 0.0}
        self.came = {start: -1}
        self.open = [(h0 * weight, 0.0, start)]
        self.best, self.best_h = start, h0
        self.exp = 0
        self.done = False
        self.reached = False
        self.path = None
        self.moves = [(1 << k, nav.offs[k], 1.0 if k < 4 else SQ2) for k in range(8)]

    def _h(self, c: int) -> float:
        j, i = divmod(c, self.nav.W)
        dx = abs(i - self.gi)
        dy = abs(j - self.gj)
        return dx + dy + (SQ2 - 2.0) * min(dx, dy)

    def step(self, deadline: float) -> bool:
        if self.done:
            return True
        nav = self.nav
        mask, extra, W = nav.mask, nav.extra, nav.W
        g, came, op, w = self.g, self.came, self.open, self.w
        gi, gj, goal = self.gi, self.gj, self.goal
        moves = self.moves
        pop, push = heapq.heappop, heapq.heappush
        k = 2.0 - SQ2
        n_iter = 0
        while op:
            n_iter += 1
            if n_iter & 127 == 0 and time.perf_counter() > deadline:
                return False
            f, gc, c = pop(op)
            if gc > g.get(c, 1e18):
                continue
            if c == goal:
                self._finish(c, True)
                return True
            self.exp += 1
            if self.exp > self.max_exp:
                break
            hc = (f - gc) / w
            if hc < self.best_h:
                self.best, self.best_h = c, hc
            m = mask[c]
            if not m:
                continue
            for bit, off, cost in moves:
                if m & bit:
                    n = c + off
                    ng = gc + cost + extra[n]
                    if ng < g.get(n, 1e18):
                        g[n] = ng
                        came[n] = c
                        j, i = divmod(n, W)
                        dx = i - gi if i > gi else gi - i
                        dy = j - gj if j > gj else gj - j
                        h = dx + dy - k * (dx if dx < dy else dy)
                        push(op, (ng + w * h, ng, n))
        self._finish(self.best, False)
        return True

    def _finish(self, end: int, reached: bool) -> None:
        path = []
        c = end
        while c != -1:
            path.append(c)
            c = self.came[c]
        path.reverse()
        self.path, self.reached, self.done = path, reached, True
        self.g = self.came = self.open = None  # free memory
