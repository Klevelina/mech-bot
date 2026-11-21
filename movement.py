"""Where we stand: cover choice (corners, facing the open side), goals and path planning,
keeping points inside the safe circle."""
import math
import time

import mechfight as mf
from config import *  # noqa: F401,F403  (switches and tuning numbers)
from util import wrap  # noqa: F401
from nav import Search


class MoveMixin:
    # ---------- goal selection
    def choose_cover(self, mx, my, zone):
        nav = self.nav
        nc, nr = zone["next_center"], zone["next_radius"]
        R = nr - max(8.0, 0.25 * nr)
        if nav is None:
            return (nc[0], nc[1])
        if R < 15:
            return nav.center(nav.nearest_main(nc[0], nc[1]))
        R2 = R * R
        cands = []
        # enemies seen/heard lately: stay away from where they were (ghost play)
        t = getattr(self, "_t", 0.0)
        # 25 m squares within ~100 m of them (one lookup per spot instead of a distance per enemy)
        hot = set()
        if COVER_AVOID_ENEMIES:
            for et, ex, ey in self.enemies.values():
                if t - et < 15.0:
                    bx, by = int(ex // 25), int(ey // 25)
                    for dx in range(-4, 5):
                        for dy in range(-4, 5):
                            if dx * dx + dy * dy <= 17:
                                hot.add((bx + dx, by + dy))
        pool = nav.cover + nav.tree_cells if TREE_COVER else nav.cover
        ncx, ncy = nc[0], nc[1]
        bad = self.bad
        for c, x, y in pool:
            dx, dy = x - ncx, y - ncy
            d2 = dx * dx + dy * dy
            if d2 > R2 or c in bad:
                continue
            s = math.hypot(x - mx, y - my) + 0.6 * math.sqrt(d2)
            if hot and (int(x // 25), int(y // 25)) in hot:
                s += 80.0
            cands.append((s, c))
        if not cands:
            self.cover_face = None
            return nav.center(nav.nearest_main(nc[0], nc[1]))
        cands.sort()
        best, bs, bface = cands[0][1], 1e18, None
        if PARK_EXPOSURE:
            # among the closest candidates, prefer corners/alcoves: few directions an enemy can see us from
            for s, c in cands[:60]:
                e = self.expo.get(c)
                if e is None:
                    e = self.expo[c] = nav.exposure(c)
                n_open, face = e
                hid = 0.0
                if TREE_COVER:
                    hid = self.conc.get(c)
                    if hid is None:
                        hid = self.conc[c] = nav.conceal(c)
                # open directions only count as far as trees don't hide us there
                s += EXPO_W * n_open * (1.0 - hid) + (10.0 if n_open == 0 else 0.0)
                if s < bs:
                    best, bs, bface = c, s, face
        self.cover_face = bface
        return nav.center(best)

    def set_goal(self, xy, x, y):
        if self.goal is not None and math.hypot(xy[0] - self.goal[0], xy[1] - self.goal[1]) < 3.0:
            return
        self.goal = xy
        self.path = None
        self.replan(x, y)

    def replan(self, mx, my):
        if self.nav is None or self.goal is None:
            self.search = None
            return
        self.search = Search(self.nav, self.nav.cell_of(mx, my), self.nav.cell_of(*self.goal))

    def clamp_safe(self, px, py):
        """Pull a point back inside the nav's safe circle (so running away never runs into the zone)."""
        s = self.nav.safe if self.nav is not None else None
        if s is None:
            return px, py
        sx, sy, sr = s
        d = math.hypot(px - sx, py - sy)
        if d <= sr - 2.0 or d < 1e-6:
            return px, py
        f = (sr - 2.0) / d
        return sx + (px - sx) * f, sy + (py - sy) * f
