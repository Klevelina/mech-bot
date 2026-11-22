"""Combat movement (squad all in): never stand still in a fight.

Every decision, with a target in our line of fire, we score staying put and a step of COMBAT_STEP m in 16 directions:
  - keep the target at our matchup's preferred distance (playbook band middle)
  - keep a clear line of fire to it
  - stay out of the sight of every other enemy close by
  - stay inside the safe circle, away from walls
  - drift toward loose cubes close by
  - follow a strafe direction (sideways to the target) that flips at random every 0.3-1.0 s, so our path can't be
    predicted by an enemy leading its shots
and take the best one. Before that: sidestep gun bullets that would hit us within COMBAT_DODGE_S, and a laser that is
charging with its aim on us.
"""
import math
import random

from config import *  # noqa: F401,F403
from util import wrap
from playbook import band, our_role


class CombatMixin:
    cm_rng = None
    cm_side = 1.0
    cm_flip_t = 0.0

    def _cm_walk(self, x, y, dx, dy, r):
        """Can we walk straight from (x, y) r m along (dx, dy)? (main ground, no step taller than step-up)."""
        nav = self.nav
        prev = nav.cell_of(x, y)
        step_up = self.cfg["mech"].get("step_up", 1.2)
        k = 2.0
        while k <= r + 0.01:
            c = nav.cell_of(x + dx * k, y + dy * k)
            if nav.comp[c] != nav.main or nav.surf[c] - nav.surf[prev] > step_up:
                return False
            prev = c
            k += 2.0
        return True

    def _cm_dodge(self, obs, me):
        """Sidestep: gun / drone bullets about to hit us, a laser charging on us. Unit (dx, dy) or None."""
        x, y, z = me["pos"]
        cz = z + me["stats"].get("height", 9.0) * 0.55
        reach = me["stats"].get("radius", 2.5) + 1.2
        dx = dy = 0.0
        for p in obs["projectiles"]:
            if p["kind"] == "mortar":
                continue  # shells: evade.py leaves their landing spot
            vx, vy, vz = p["vel"]
            v2 = vx * vx + vy * vy + vz * vz
            if v2 < 1.0:
                continue
            tc = ((x - p["pos"][0]) * vx + (y - p["pos"][1]) * vy + (cz - p["pos"][2]) * vz) / v2
            if tc <= 0 or tc > COMBAT_DODGE_S:
                continue
            mx, my = p["pos"][0] + vx * tc - x, p["pos"][1] + vy * tc - y
            if math.hypot(mx, my) > reach:
                continue
            vh = math.hypot(vx, vy)
            if vh < 1.0:
                continue
            px, py = -vy / vh, vx / vh  # sideways to the bullet, away from where it passes
            if px * mx + py * my > 0:
                px, py = -px, -py
            dx += px
            dy += py
        for m in obs["mechs"]:
            if m["kind"] != "mech" or not m.get("charging"):
                continue
            to_me = math.atan2(y - m["pos"][1], x - m["pos"][0])
            if abs(wrap(m["aim"][0] - to_me)) > 0.15:
                continue
            a = to_me + math.pi / 2 * self.cm_side
            dx += 1.5 * math.cos(a)
            dy += 1.5 * math.sin(a)
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 0.01 else None

    def combat_move(self, obs, me, m, t, t_start):
        """([mx, my], mode) or None (target not in our line of fire: leave movement to the fight goal)."""
        if self.nav is None or m["kind"] != "mech" or not self.can_hit(me, m):
            return None
        if self.cm_rng is None:
            self.cm_rng = random.Random(self.cfg.get("match", {}).get("seed", 0) or 7)
        nav = self.nav
        x, y, z = me["pos"]
        if t >= self.cm_flip_t:
            if self.cm_rng.random() < 0.6:
                self.cm_side = -self.cm_side
            self.cm_flip_t = t + 0.3 + 0.7 * self.cm_rng.random()
        d0 = self._cm_dodge(obs, me)
        if d0 is not None:
            return [d0[0], d0[1]], "DODGE"
        ex, ey = m["pos"][0], m["pos"][1]
        bd = math.hypot(ex - x, ey - y) or 1.0
        ux, uy = (ex - x) / bd, (ey - y) / bd
        sx, sy = -uy * self.cm_side, ux * self.cm_side  # strafe direction
        pref = self.allin_range(me, m)
        scale = 1.0 + self.size_per_cube * m.get("cubes", 0)
        tz = m["pos"][2] + 4.5 * scale
        mh = me["stats"].get("muzzle_height", 7.0)
        others = [o for o in obs["mechs"] if o["kind"] == "mech" and o["id"] != m["id"] and
                  math.hypot(o["pos"][0] - x, o["pos"][1] - y) < COMBAT_OTHERS_R][:3]
        cube = None
        for it in self.mem.items.values():
            if it["kind"] == "cube" and abs(it["z"] - z) < 4.0:
                dc = math.hypot(it["x"] - x, it["y"] - y)
                if dc < 40.0 and (cube is None or dc < cube[2]):
                    cube = (it["x"], it["y"], dc)
        best, bmove = -1e18, [0.0, 0.0]
        side_blocked = False
        for k in range(-1, 16):
            if k < 0:
                dx = dy = r = 0.0
                cx, cy = x, y
            else:
                a = k * math.pi / 8
                dx, dy, r = math.cos(a), math.sin(a), COMBAT_STEP
                cx, cy = x + dx * r, y + dy * r
                if not self._cm_walk(x, y, dx, dy, r):
                    if dx * sx + dy * sy > 0.9:
                        side_blocked = True
                    continue
            c = nav.cell_of(cx, cy)
            cz = nav.surf[c]
            d = math.hypot(ex - cx, ey - cy)
            s = -1.1 * abs(d - pref) - 0.4 * r
            if nav.los(cx, cy, cz + mh, ex, ey, tz):
                s += 45.0
            for o in others:
                if nav.los(o["pos"][0], o["pos"][1], self.eye_of(o), cx, cy, cz + 0.55 * (nav.body_top + 0.5)):
                    s -= 30.0
            if not nav.in_safe(c):
                s -= 40.0
            if cube is not None:
                s += 1.5 * (cube[2] - math.hypot(cube[0] - cx, cube[1] - cy))
            s += 9.0 * (dx * sx + dy * sy)
            if REAR_ARC:
                # outside the target's front arc our hits do rear damage (x1.5): favour steps that get us there,
                # or deeper round its side while its torso is still turning
                off = abs(wrap(math.atan2(cy - ey, cx - ex) - m["aim"][0]))
                if off > math.pi / 2:
                    s += REAR_ARC_W
                elif off > math.pi / 3:
                    s += REAR_ARC_W * 0.4
            if k < 0:
                s -= 8.0
            if s > best:
                best, bmove = s, [dx, dy]
        if side_blocked:
            self.cm_side = -self.cm_side
            self.cm_flip_t = t + 0.5
        return bmove, "STRAFE"
