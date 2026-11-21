"""What we know without seeing it: the danger map, sounds, and early exploration."""
import math
import time

import mechfight as mf
from config import *  # noqa: F401,F403  (switches and tuning numbers)
from util import wrap  # noqa: F401


class SensesMixin:
    def danger(self, ix, iy, t, r=DANGER_R, s=DANGER_S):
        for et, ex, ey in self.enemies.values():
            if t - et < s and (ex - ix) ** 2 + (ey - iy) ** 2 < r * r:
                return True
        return False

    # ---------- sounds
    def hear(self, obs, x, y, t):
        for s in obs.get("sounds", []):
            kind, band = s.get("kind"), s.get("dist")
            base = SOUND_PRIO.get(kind)
            if base is None or band not in BAND_MULT:
                continue
            p = base * BAND_MULT[band]
            th = t - s.get("age", 0.0)
            b = s["bearing"]
            sf = self.sound_face
            if sf is None or p >= sf[2] or th - sf[0] > 1.5:
                self.sound_face = (th, b, p)
            if band != "far":  # rough position on the danger map (loot / explore / drops avoid it)
                dd = BAND_DIST[band]
                self.enemies[("snd", self.snd_i % 40)] = (th, x + dd * math.cos(b), y + dd * math.sin(b))
                self.snd_i += 1
            if kind in MOVE_SOUNDS and band == "near":
                self.threat_t = max(self.threat_t, th)  # a mech moving within ~40 m: stop looting, be ready

    # ---------- early exploration
    def mark_seen(self, x, y):
        b = EXPLORE_BIN
        bx, by = int(x // b), int(y // b)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                self.seen_bins.add((bx + dx, by + dy))

    def choose_explore(self, x, y, zone):
        nav = self.nav
        b = EXPLORE_BIN
        nc, nr = zone["next_center"], zone["next_radius"]
        cc, cr = zone["center"], zone["radius"]
        bx0, by0 = int(x // b), int(y // b)
        R = int(150 // b)
        best, bs = None, 1e18
        for dx in range(-R, R + 1):
            for dy in range(-R, R + 1):
                key = (bx0 + dx, by0 + dy)
                if key in self.seen_bins:
                    continue
                cx, cy = (key[0] + 0.5) * b, (key[1] + 0.5) * b
                d = math.hypot(cx - x, cy - y)
                if d < 40 or d > 150:
                    continue
                dn = math.hypot(cx - nc[0], cy - nc[1])
                if dn > nr - 20 or math.hypot(cx - cc[0], cy - cc[1]) > cr - 20:
                    continue
                if self.danger(cx, cy, self._t):
                    continue
                s = d + 0.5 * dn
                if s < bs:
                    best, bs = key, s
        if best is None:
            return None
        cx, cy = (best[0] + 0.5) * b, (best[1] + 0.5) * b
        c = nav.nearest_main(cx, cy, max_r=6)
        if nav.comp[c] != nav.main:
            self.seen_bins.add(best)  # nothing walkable there
            return None
        return nav.center(c)

    def _explore(self, x, y, zone, t):
        """Keep/choose an exploration goal; False when there is nothing left worth exploring."""
        g = self.explore_goal
        if g is not None and math.hypot(g[0] - x, g[1] - y) < 8.0:
            self.mark_seen(g[0], g[1])
            g = None
        if g is None or t - self.explore_t > 20.0:
            self.explore_t = t
            g = self.choose_explore(x, y, zone)
        self.explore_goal = g
        return g is not None
