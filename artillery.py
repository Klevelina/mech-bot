"""Mortar: lob shells over cover at enemies we can't shoot directly, at slow/standing targets, and at bunched
pairs (the 7 m splash hits both). Ballistic aim (speed v, gravity g): for horizontal range R and height
difference h, tan(theta) = (v^2 +- sqrt(v^4 - g (g R^2 + 2 h v^2))) / (g R); the high arc goes over buildings,
the low arc is faster when the line is clear. The flight path is checked against the map so we don't shell
our own cover, and we never fire when the shell would land near us (self-splash, knock-back)."""
import math

from config import *  # noqa: F401,F403


class ArtilleryMixin:
    @staticmethod
    def mortar_slot(weapons):
        for i, w in enumerate(weapons):
            if w and w["kind"] == "mortar":
                return i
        return None

    def mortar_path_clear(self, ox, oy, oz, yaw, th, v, g, target, T):
        """True if the shell flies to within ~8 m of `target` without hitting a building or the ground."""
        nav = self.nav
        if nav is None:
            return True
        ch, sh = math.cos(th), math.sin(th)
        cy_, sy_ = math.cos(yaw), math.sin(yaw)
        tt = 0.15
        while tt < T:
            hd = v * ch * tt
            px, py = ox + cy_ * hd, oy + sy_ * hd
            pz = oz + v * sh * tt - 0.5 * g * tt * tt
            if math.hypot(px - target[0], py - target[1]) < 3.0 and pz < target[2] + 6.0:
                return True
            i, j = int((px - nav.ox) / nav.sub), int((py - nav.oy) / nav.sub)
            if not (0 <= i < nav.SW and 0 <= j < nav.SH):
                return False
            fc = j * nav.SW + i
            if nav.fbase[fc] <= pz <= nav.ftop[fc] or nav.ground_z(px, py) > pz:
                return False
            tt += 0.02
        return True

    def mortar_solve(self, me, tpos, tvel, high):
        """(yaw, pitch, flight time, impact point) to land a shell on tpos (moving at tvel), or None."""
        slot = self.mortar_slot(me["weapons"])
        if slot is None:
            return None
        st = me["weapons"][slot]["stats"]
        v, g = st.get("speed", 72.0), st.get("gravity", 22.0)
        ms = me["stats"]
        x, y, z = me["pos"]
        scale = ms.get("height", 9.0) / 9.0
        oz = z + (ms.get("height", 9.0) + 0.5)  # mortar tube
        lo, hi = ms.get("min_pitch", -0.7), ms.get("max_pitch", 1.3)
        vx, vy = (tvel[0], tvel[1]) if tvel else (0.0, 0.0)
        T = 1.5
        th = None
        ax = ay = az = 0.0
        for _ in range(3):
            ax, ay, az = tpos[0] + vx * T, tpos[1] + vy * T, tpos[2] + 0.5 * scale
            R = math.hypot(ax - x, ay - y)
            hgt = az - oz
            disc = v ** 4 - g * (g * R * R + 2 * hgt * v * v)
            if disc < 0 or R < 1.0:
                return None
            sq = math.sqrt(disc)
            arcs = [math.atan((v * v + sq) / (g * R)), math.atan((v * v - sq) / (g * R))]
            if not high:
                arcs.reverse()
            th = next((a for a in arcs if lo <= a <= hi), None)
            if th is None:
                return None
            T = R / (v * math.cos(th))
        yaw = math.atan2(ay - y, ax - x)
        if not self.mortar_path_clear(x, y, oz, yaw, th, v, g, (ax, ay, az), T):
            return None
        return yaw, th, T, (ax, ay, az)

    def mortar_plan(self, me, m, t, gun_has_shot, others=()):
        """Fire the mortar at m now? Returns (aim [yaw, pitch], slot) or None."""
        if not MORTAR_USE:
            return None
        slot = self.mortar_slot(me["weapons"])
        if slot is None or me["weapons"][slot].get("cooldown", 0) > 0.05:
            return None
        x, y = me["pos"][0], me["pos"][1]
        d = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
        if d < MORTAR_MIN_DIST or d > MORTAR_MAX_DIST:
            return None
        vel = m.get("vel") or [0.0, 0.0, 0.0]
        slow = math.hypot(vel[0], vel[1]) < 3.0
        bunched = any(o is not m and o["kind"] == "mech" and
                      math.hypot(o["pos"][0] - m["pos"][0], o["pos"][1] - m["pos"][1]) < 10.0 for o in others)
        if gun_has_shot and not (slow or bunched):
            return None  # a moving target in plain sight: the gun is better (shells take seconds to land)
        sol = self.mortar_solve(me, m["pos"], vel, high=not gun_has_shot) or \
            self.mortar_solve(me, m["pos"], vel, high=gun_has_shot)
        if sol is None:
            return None
        yaw, pitch, T, imp = sol
        if math.hypot(imp[0] - x, imp[1] - y) < MORTAR_MIN_DIST - 5.0:
            return None
        return [yaw, pitch], slot
