"""Moving so we're hard to hit: jinking under gun fire (learned pattern), laser sidestep, mortar dodge."""
import math
import time

import mechfight as mf
from config import *  # noqa: F401,F403  (switches and tuning numbers)
from util import wrap  # noqa: F401
from tactics import mortar_landing


class EvadeMixin:
    # ---------- evasive movement
    def walkable_dir(self, x, y, dx, dy, dist=4.0):
        nav = self.nav
        c0 = nav.cell_of(x, y)
        c = nav.cell_of(x + dx * dist, y + dy * dist)
        return (nav.comp[c] == nav.main or nav.comp[c] == nav.comp[c0]) and not nav.blocked[c] and nav.in_safe(c) \
            and nav.surf[c] - nav.surf[c0] <= 1.3

    def evade(self, obs, me, move, t):
        """Mortar dodge > laser sidestep > jink against guns. Returns (new move or None, boost?)."""
        if self.nav is None:
            self.jinker.step(t, False, 0.0)
            return None, False
        x, y, z = me["pos"]
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        if DODGE:
            mw = self.wcfg.get("mortar", {})
            for pr in obs["projectiles"]:
                if pr["kind"] != "mortar":
                    continue
                land = mortar_landing(pr["pos"], pr["vel"], mw.get("gravity", 22), self.nav.ground_z)
                if land and math.hypot(land[0] - x, land[1] - y) < mw.get("splash_radius", 7) + 3.0:
                    dx, dy = x - land[0], y - land[1]
                    dn = math.hypot(dx, dy)
                    if dn < 0.5:  # dead on us: any walkable side
                        dx, dy, dn = -math.sin(me["aim"][0]), math.cos(me["aim"][0]), 1.0
                    dx, dy = dx / dn, dy / dn
                    if not self.walkable_dir(x, y, dx, dy):
                        dx, dy = -dy, dx
                    self.jinker.step(t, False, 0.0)
                    return [dx, dy], me["energy"] > 35  # walking usually clears 7 m; boost only with energy to spare
        # who is shooting at us: a laser charging on us, else the nearest gun holder that hit us / sees us
        laser = None
        if DODGE:
            for m in mechs:
                if m.get("charging"):
                    b = math.atan2(y - m["pos"][1], x - m["pos"][0])
                    if abs(wrap(m["aim"][0] - b)) < 0.1 and self.nav.los(m["pos"][0], m["pos"][1], m["pos"][2] + 7,
                                                                       x, y, z + 4.5):
                        laser = m
                        break
        under_gun = t - self.last_gun_dmg_t < 3.0
        active = JINK and (under_gun or laser is not None)
        side = self.jinker.step(t, active, self.gun_dmg_tick)
        if not active:
            return None, False
        if laser is not None:
            src = laser["pos"]
            if side == 0:
                side = self.jinker.side
        else:
            shooters = [m for m in mechs if "gun" in self.emodel.kinds.get(m["id"], {"gun"}) and
                        t - self.attackers.get(m["id"], -1e9) < 3.0]
            if shooters:
                src = min(shooters, key=lambda m: math.hypot(m["pos"][0] - x, m["pos"][1] - y))["pos"]
            else:
                b = self.dmg_bearing[1]
                src = [x + 100 * math.cos(b), y + 100 * math.sin(b), z]
            if side == 0:
                if self.escape is not None:
                    side = self.jinker.side  # never pause while running for cover
                else:
                    return [0.0, 0.0], False  # stop-and-go pause
        # wedged while jinking (thin obstacle the 2 m grid misses): reverse
        self.jink_hist.append((t, x, y))
        while self.jink_hist and t - self.jink_hist[0][0] > 1.0:
            self.jink_hist.popleft()
        t0, x0, y0 = self.jink_hist[0]
        if t - t0 > 0.8 and math.hypot(x - x0, y - y0) < 0.5:
            self.jinker.flip()
            side = -side
            self.jink_hist.clear()
        b = math.atan2(src[1] - y, src[0] - x)
        px, py = -math.sin(b) * side, math.cos(b) * side
        if not self.walkable_dir(x, y, px, py):
            self.jinker.flip()
            px, py = -px, -py
            if not self.walkable_dir(x, y, px, py):
                return None, False
        bx, by = move
        if bx == 0.0 and by == 0.0 and self.goal is not None:
            gx, gy = self.goal
            dg = math.hypot(gx - x, gy - y)
            if dg > 6.0:  # drifted while jinking: lean back toward where we want to stand
                bx, by = 0.5 * (gx - x) / dg, 0.5 * (gy - y) / dg
        if self.escape is not None and (bx or by):
            mx, my = bx + 0.4 * px, by + 0.4 * py  # escaping: reaching cover first, small wobble only
        else:
            mx, my = 0.6 * bx + px, 0.6 * by + py
        n = math.hypot(mx, my) or 1.0
        return [mx / n, my / n], laser is not None and me["energy"] > 20
