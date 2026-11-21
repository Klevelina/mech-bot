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
        for c, x, y in nav.cover:
            dx, dy = x - nc[0], y - nc[1]
            d2 = dx * dx + dy * dy
            if d2 > R2 or c in self.bad:
                continue
            cands.append((math.hypot(x - mx, y - my) + 0.6 * math.sqrt(d2), c))
        if not cands:
            self.cover_face = None
            return nav.center(nav.nearest_main(nc[0], nc[1]))
        cands.sort()
        best, bs, bface = cands[0][1], 1e18, None
        loot_pts = []
        if BASE and getattr(self, "mem", None) is not None:
            from loot import VALUE
            for i, it in self.mem.items.items():
                if i in self.mem.banned:
                    continue
                v = VALUE.get(it["kind"], 4.0 if it["kind"] == "pickup" else 0.0)
                if v > 0:
                    loot_pts.append((it["x"], it["y"], v))
        if PARK_EXPOSURE:
            # among the closest candidates, prefer corners/alcoves: few directions an enemy can see us from
            for s, c in cands[:60]:
                e = self.expo.get(c)
                if e is None:
                    e = self.expo[c] = nav.exposure(c)
                n_open, face = e
                s += EXPO_W * n_open + (10.0 if n_open == 0 else 0.0)  # fully enclosed: can't see anyone coming
                if BASE and loot_pts:
                    # home base: a strong corner with loot around it (short trips, then back)
                    bx, by = nav.center(c)
                    near = sum(v for (lx, ly, v) in loot_pts if (lx - bx) ** 2 + (ly - by) ** 2 < BASE_LOOT_R ** 2)
                    s -= BASE_LOOT_W * min(near, 15.0)
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
