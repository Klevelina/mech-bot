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

    def _cm_paths(self, me, energy_ok):
        """COMBAT_PLAN: where each candidate move takes us over the next PLAN_STEPS x PLAN_DT s, with acceleration.
        [(dx, dy, boost, [(x, y, vx, vy) per step, first = now])]; candidate 0 = stand (brake)."""
        x, y = me["pos"][0], me["pos"][1]
        v0 = me.get("vel") or (0.0, 0.0, 0.0)
        st = me["stats"]
        walk = st.get("walk_speed", 11.7)
        run = st.get("boost_speed", 19.5)
        acc = self.cfg["mech"].get("ground_accel", 58.5)
        slow = 0.5 if any(w and w["kind"] == "laser" and w.get("charge", 0) > 0 for w in me["weapons"]) else 1.0
        cands = [(0.0, 0.0, False, 0.0)]
        for k in range(16):
            a = k * math.pi / 8
            cands.append((math.cos(a), math.sin(a), False, walk * slow))
        if energy_ok:
            for k in range(8):
                a = k * math.pi / 4 + math.pi / 8
                cands.append((math.cos(a), math.sin(a), True, run * slow))
        out = []
        dv = acc * PLAN_DT
        for dx, dy, b, sp in cands:
            vx, vy, px, py = v0[0], v0[1], x, y
            pts = [(px, py, vx, vy)]
            tx, ty = dx * sp, dy * sp
            for _ in range(PLAN_STEPS):
                ex_, ey_ = tx - vx, ty - vy
                n = math.hypot(ex_, ey_)
                if n > dv:
                    ex_, ey_ = ex_ * dv / n, ey_ * dv / n
                vx, vy = vx + ex_, vy + ey_
                px, py = px + vx * PLAN_DT, py + vy * PLAN_DT
                pts.append((px, py, vx, vy))
            out.append((dx, dy, b, pts))
        return out

    def _cm_incoming(self, obs, me, pts):
        """Damage the bullets already flying at us would do along this path (closest approach per time step)."""
        z = me["pos"][2]
        h = me["stats"].get("height", 9.0)
        rad = me["stats"].get("radius", 2.5)
        cz = z + 0.5 * h
        margin = PLAN_MARGIN
        cost = 0.0
        npc_dmg = self.cfg.get("npc", {}).get("damage", 12.0)
        gun_dmg = self.wcfg.get("gun", {}).get("damage", 45.0)
        for p in obs["projectiles"]:
            if p["kind"] == "mortar":
                continue
            bx, by, bz = p["pos"]
            vx, vy, vz = p["vel"]
            worst = 0.0
            for k in range(len(pts) - 1):
                t0 = k * PLAN_DT
                qx, qy = bx + vx * t0, by + vy * t0
                qz = bz + vz * t0
                ax, ay, avx, avy = pts[k]
                if abs(qz - cz) > 0.5 * h + margin and abs(qz + vz * PLAN_DT - cz) > 0.5 * h + margin:
                    continue
                rx, ry = qx - ax, qy - ay
                rvx, rvy = vx - avx, vy - avy
                rv2 = rvx * rvx + rvy * rvy
                tc = 0.0 if rv2 < 1e-6 else max(0.0, min(PLAN_DT, -(rx * rvx + ry * rvy) / rv2))
                dxy = math.hypot(rx + rvx * tc, ry + rvy * tc)
                reach = rad + 0.3 + margin
                if dxy < reach:
                    worst = max(worst, min(1.0, (reach - dxy) / margin))
            if worst > 0:
                cost += worst * (npc_dmg if p["kind"] == "npc" else gun_dmg)
        # a laser charging with its aim on us: paths that end still on its line take the beam
        ex_, ey_ = pts[-1][0], pts[-1][1]
        for m in obs["mechs"]:
            if m["kind"] != "mech" or not m.get("charging"):
                continue
            a = m["aim"][0]
            ux, uy = math.cos(a), math.sin(a)
            rx, ry = ex_ - m["pos"][0], ey_ - m["pos"][1]
            if rx * ux + ry * uy > 0 and abs(rx * uy - ry * ux) < rad + 1.5:
                cost += 0.8 * self.wcfg.get("laser", {}).get("damage", 315.0)
        return cost

    def reload_ducking(self, me):
        """RELOAD_DUCK: gun only, and it can't shoot right now (empty / reloading)."""
        if not RELOAD_DUCK or any(w and w["kind"] in ("laser", "lightning") for w in me["weapons"]):
            return False
        gw = next((w for w in me["weapons"] if w and w["kind"] == "gun"), None)
        return gw is not None and (gw.get("ammo", 0) <= 0 or gw.get("reload", 0) > 0.25)

    def combat_move(self, obs, me, m, t, t_start):
        """([mx, my], mode) or None (target not in our line of fire: leave movement to the fight goal).
        While reloading (gun only) it also runs out of the line of fire, to keep us hidden until the gun is ready."""
        ducking = self.reload_ducking(me)
        if self.nav is None or m["kind"] != "mech" or not (ducking or self.can_hit(me, m)):
            return None
        if self.cm_rng is None:
            self.cm_rng = random.Random(self.cfg.get("match", {}).get("seed", 0) or 7)
        nav = self.nav
        x, y, z = me["pos"]
        if t >= self.cm_flip_t:
            if self.cm_rng.random() < 0.6:
                self.cm_side = -self.cm_side
            self.cm_flip_t = t + 0.3 + 0.7 * self.cm_rng.random()
        self.cm_boost = False
        if not COMBAT_PLAN:
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
        # gun only and it can't shoot right now (empty / reloading): step out of the target's line of fire until it can
        want = -1.0 if ducking else 1.0
        if COMBAT_PLAN:
            return self._cm_plan(obs, me, m, t, ex, ey, sx, sy, pref, tz, mh, others, cube, want)
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
            los = nav.los(cx, cy, cz + mh, ex, ey, tz)
            if los:
                s += 45.0 * want
            for o in others:
                if nav.los(o["pos"][0], o["pos"][1], o["pos"][2] + 8.0, cx, cy, cz + 5.0):
                    s -= 30.0
            if not nav.in_safe(c):
                s -= 40.0
            if cube is not None:
                s += 1.5 * (cube[2] - math.hypot(cube[0] - cx, cube[1] - cy))
            if want > 0:
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
                if want < 0 and not los:
                    s += 14.0  # already out of its sight while reloading: stay
            if s > best:
                best, bmove = s, [dx, dy]
        if side_blocked:
            self.cm_side = -self.cm_side
            self.cm_flip_t = t + 0.5
        return bmove, "STRAFE"

    def _cm_plan(self, obs, me, m, t, ex, ey, sx, sy, pref, tz, mh, others, cube, want):
        """COMBAT_PLAN: the same scoring as the one-step version, judged where each simulated path ends (stand, 16
        walking and 8 boosted directions over PLAN_STEPS x PLAN_DT s), plus the damage of bullets already flying at us
        along the path, a juke across a gun aimed at us, and a small price on boosting."""
        nav = self.nav
        x, y = me["pos"][0], me["pos"][1]
        walk = me["stats"].get("walk_speed", 11.7)
        # juke: a gun holder 30-80 m away with its aim on us -> sidestep across its line, flipping side every 0.35-0.95 s
        jx = jy = 0.0
        for o in obs["mechs"]:
            if o["kind"] != "mech" or "gun" not in (o.get("weapons") or []):
                continue
            dd = math.hypot(x - o["pos"][0], y - o["pos"][1])
            if PLAN_JUKE_R[0] <= dd <= PLAN_JUKE_R[1] and \
                    abs(wrap(o["aim"][0] - math.atan2(y - o["pos"][1], x - o["pos"][0]))) < 0.35:
                jx, jy = -(y - o["pos"][1]) / dd, (x - o["pos"][0]) / dd
                break
        if (jx or jy) and t >= getattr(self, "cm_juke_next", 0.0):
            self.cm_juke_side = -getattr(self, "cm_juke_side", 1.0)
            self.cm_juke_next = t + 0.35 + 0.6 * self.cm_rng.random()
        jside = getattr(self, "cm_juke_side", 1.0)
        best, bmove, bboost = -1e18, [0.0, 0.0], False
        side_blocked = False
        for k, (dx, dy, boost, pts) in enumerate(self._cm_paths(me, me["energy"] > PLAN_BOOST_E)):
            cx, cy = pts[-1][0], pts[-1][1]
            r = math.hypot(cx - x, cy - y)
            if k > 0 and r > 0.5 and not self._cm_walk(x, y, (cx - x) / r, (cy - y) / r, r):
                if not boost and dx * sx + dy * sy > 0.9:
                    side_blocked = True
                continue
            c = nav.cell_of(cx, cy)
            cz = nav.surf[c]
            d = math.hypot(ex - cx, ey - cy)
            s = -1.1 * abs(d - pref) - 0.4 * r
            los = nav.los(cx, cy, cz + mh, ex, ey, tz)
            if los:
                s += 45.0 * want
            for o in others:
                if nav.los(o["pos"][0], o["pos"][1], o["pos"][2] + 8.0, cx, cy, cz + 5.0):
                    s -= 30.0
            if not nav.in_safe(c):
                s -= 40.0
            if cube is not None:
                s += 1.5 * (cube[2] - math.hypot(cube[0] - cx, cube[1] - cy))
            if want > 0 and k > 0:
                s += 9.0 * (dx * sx + dy * sy)
                if jx or jy:
                    s += PLAN_JUKE_W * jside * (dx * jx + dy * jy) * min(1.3, (pts[-1][2] ** 2 + pts[-1][3] ** 2) ** 0.5 / walk)
            if REAR_ARC:
                off = abs(wrap(math.atan2(cy - ey, cx - ex) - m["aim"][0]))
                if off > math.pi / 2:
                    s += REAR_ARC_W
                elif off > math.pi / 3:
                    s += REAR_ARC_W * 0.4
            if k == 0:
                s -= 8.0
                if want < 0 and not los:
                    s += 14.0
            if boost:
                s -= PLAN_BOOST_COST
            s -= PLAN_PROJ_W * self._cm_incoming(obs, me, pts)
            if s > best:
                best, bmove, bboost = s, [dx, dy], boost
        if side_blocked:
            self.cm_side = -self.cm_side
            self.cm_flip_t = t + 0.5
        self.cm_boost = bboost
        return bmove, "STRAFE"
