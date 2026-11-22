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
        if PARK_EXPOSURE:
            # among the closest candidates, prefer corners/alcoves: few directions an enemy can see us from
            for s, c in cands[:60]:
                tree = PARK_TREES and getattr(self, "situation", "") == "ffa"
                e = self.expo.get((c, tree))
                if e is None:
                    e = self.expo[(c, tree)] = nav.exposure(c, canopy=tree)
                n_open, face = e
                expo_w = EXPO_W * (POWER_BIG_EXPO if getattr(self, "big", False) else 1.0)
                s += expo_w * n_open + (10.0 if n_open == 0 else 0.0)  # fully enclosed: can't see anyone coming
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

    def _zone_at(self, zone, tq):
        """The circle (cx, cy, r) at time tq: the held circle before the shrink, then moving linearly to the next."""
        hold = getattr(self, "zone_hold", None)
        if hold is None or zone["t_now"] <= zone["shrink_start"]:
            hold = (zone["center"][0], zone["center"][1], zone["radius"])
        if tq <= zone["shrink_start"] or zone["shrink_end"] <= zone["shrink_start"]:
            return hold
        f = min(1.0, (tq - zone["shrink_start"]) / (zone["shrink_end"] - zone["shrink_start"]))
        nc, nr = zone["next_center"], zone["next_radius"]
        return hold[0] + (nc[0] - hold[0]) * f, hold[1] + (nc[1] - hold[1]) * f, hold[2] + (nr - hold[2]) * f

    def zone_must(self, me, zone, t):
        """ZONE_FIRST: a point inside the next circle when we're outside the zone or it would catch us before we
        could walk in (with ZONE_PAD m and ZONE_SPARE_S s to spare); else None."""
        x, y = me["pos"][0], me["pos"][1]
        if t <= zone["shrink_start"]:
            self.zone_hold = (zone["center"][0], zone["center"][1], zone["radius"])
        zone = dict(zone, t_now=t)
        nc, nr = zone["next_center"], zone["next_radius"]
        dn = math.hypot(x - nc[0], y - nc[1])
        # where to go: just inside the next circle on our side (its centre once it's small)
        if nr <= 30.0:
            gx, gy = nc[0], nc[1]
        else:
            r = max(0.0, nr - min(30.0, max(3.0, 0.3 * nr)) - 5.0)
            if dn <= r:
                gx, gy = x, y
            else:
                gx, gy = nc[0] + (x - nc[0]) / dn * r, nc[1] + (y - nc[1]) / dn * r
        need = math.hypot(gx - x, gy - y)
        if need < 2.0:
            return None
        if not me.get("in_zone", True):
            return (gx, gy)
        # when does the shrinking circle reach us?
        deadline = 1e18
        if dn > nr - ZONE_PAD:
            deadline = zone["shrink_end"]
            for k in range(25):
                tk = zone["shrink_start"] + (zone["shrink_end"] - zone["shrink_start"]) * k / 24.0
                cx, cy, cr = self._zone_at(zone, tk)
                if math.hypot(x - cx, y - cy) > cr - ZONE_PAD:
                    deadline = tk
                    break
        t_need = need * 1.45 / max(1.0, me["stats"].get("walk_speed", 11.7)) + 4.0
        if t + t_need + ZONE_SPARE_S >= deadline:
            return (gx, gy)
        return None

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
