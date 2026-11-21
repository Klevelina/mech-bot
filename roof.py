"""Rooftop camping: the house bots know where we are, so instead of hiding we go where they can't reach and
can barely see. Pick a flat roof inside the next circle whose camping spot is hidden from the ground (the roof
edge blocks lines from below), walk to the foot of its lowest wall, wait for enough energy, jump + jet up,
walk to the spot and stay there: no escaping or repositioning off the roof, just shoot what gets a line on us."""
import math

import mechfight as mf
from config import *  # noqa: F401,F403


class RoofMixin:
    def roof_energy_needed(self, wall):
        # rise at ~max_rise_speed while jetting: drain per metre = jet_drain / max_rise_speed, plus a jump's worth
        st = self.cfg["mech"]
        per_m = st.get("jet_drain", 15) / max(1.0, st.get("max_rise_speed", 10))
        return per_m * wall + ROOF_ENERGY_MARGIN

    @staticmethod
    def zone_reaches(pos, zone, t, margin=5.0):
        """Match time at which the shrinking zone edge gets within `margin` m of pos (1e9 if never this phase).
        Assumes the shrink runs from the circle we see now to the next one, linearly until shrink_end."""
        c0, r0 = zone["center"], zone["radius"]
        c1, r1 = zone["next_center"], zone["next_radius"]
        ss, se = max(t, zone["shrink_start"]), zone["shrink_end"]
        if se <= ss:
            return 1e9 if math.hypot(pos[0] - c1[0], pos[1] - c1[1]) < r1 - margin else t
        tt = ss
        while tt <= se + 0.01:
            f = (tt - ss) / (se - ss)
            cx, cy = c0[0] + (c1[0] - c0[0]) * f, c0[1] + (c1[1] - c0[1]) * f
            if math.hypot(pos[0] - cx, pos[1] - cy) > r0 + (r1 - r0) * f - margin:
                return tt
            tt += 1.0
        return 1e9

    def choose_roof(self, me, zone, exclude=None):
        """Best roof plan for this circle, or None."""
        if not self.roofs:
            return None
        x, y = me["pos"][0], me["pos"][1]
        nc, nr = zone["next_center"], zone["next_radius"]
        cc, cr = zone["center"], zone["radius"]
        if nr < ROOF_MIN_CIRCLE:
            return None
        max_e = me.get("max_energy", 100.0)
        best, bs = None, 1e18
        for r in self.roofs:
            if r is exclude or r["expo"] > ROOF_MAX_EXPO or self.roof_energy_needed(r["wall"]) > max_e:
                continue
            sx, sy = r["pos"]
            if math.hypot(sx - nc[0], sy - nc[1]) > nr - 10 or math.hypot(sx - cc[0], sy - cc[1]) > cr - 10:
                continue
            gx, gy = self.nav.center(r["climb"])
            d = math.hypot(gx - x, gy - y)
            if d > ROOF_MAX_WALK:
                continue
            # low exposure, close, and deep inside the next circle (stays inside later circles too)
            s = d + 300.0 * r["expo"] + 100.0 * math.hypot(sx - nc[0], sy - nc[1]) / max(nr, 1.0)
            if s < bs:
                best, bs = r, s
        if best is not None:
            mf.log(f"roof plan: spot {best['pos'][0]:.0f},{best['pos'][1]:.0f} expo {best['expo']:.2f} "
                   f"wall {best['wall']:.0f} m")
        return best

    def on_roof(self, me):
        p = self.roof_plan
        if p is None or self.nav is None:
            return False
        c = self.nav.cell_of(me["pos"][0], me["pos"][1])
        return self.nav.comp[c] == p["comp"] and me["pos"][2] >= p["top"] - 1.5

    def roof_step(self, me, zone, t):
        """Updates the plan. Returns None (no roof play now) or (goal, stop radius, mode, climb_move or None)."""
        if not ROOF or self.nav is None:
            return None
        key = (round(zone["next_center"][0], 1), round(zone["next_center"][1], 1), round(zone["next_radius"], 1))
        if key != self.roof_key:
            self.roof_key = key
            keep = self.roof_plan
            self.roof_next, self.roof_leave_t = None, None
            if keep is not None:
                sx, sy = keep["pos"]
                if math.hypot(sx - zone["next_center"][0], sy - zone["next_center"][1]) > zone["next_radius"] - 10 or \
                        zone["next_radius"] < ROOF_MIN_CIRCLE:
                    # the next circle leaves our roof out: stay until the edge actually comes, then move on
                    self.roof_leave_t = self.zone_reaches(keep["pos"], zone, t)
                    self.roof_next = self.choose_roof(me, zone, exclude=keep)
            self.roof_plan = keep if keep is not None else self.choose_roof(me, zone)
        if self.roof_leave_t is not None and self.roof_plan is not None:
            nxt = self.roof_next
            if nxt is not None:
                tx, ty = self.nav.center(nxt["climb"])
            else:
                tx, ty = zone["next_center"]
            walk = me["stats"].get("walk_speed", 11.7)
            travel = math.hypot(tx - me["pos"][0], ty - me["pos"][1]) * 1.3 / walk
            if t >= self.roof_leave_t - travel - ROOF_LEAVE_SLACK:
                mf.log(f"t={t:.1f} leaving roof for {'next roof' if nxt else 'the circle'}")
                self.roof_plan, self.roof_next, self.roof_leave_t = nxt, None, None
        p = self.roof_plan
        if p is None or t < ROOF_FROM:
            return None
        nav = self.nav
        x, y, z = me["pos"]
        if self.on_roof(me):
            return p["pos"], 1.5, "ROOF", None
        gx, gy = nav.center(p["climb"])
        d = math.hypot(gx - x, gy - y)
        if d > (6.0 if self.climb_t is not None else 2.5):  # still walking to the foot of the wall
            return (gx, gy), 2.0, "TO_ROOF", None
        need = self.roof_energy_needed(p["wall"])
        ex, ey = nav.center(p["edge"])
        dx, dy = ex - gx, ey - gy
        dn = math.hypot(dx, dy) or 1.0
        into_wall = [dx / dn, dy / dn]
        if me["grounded"] and me["energy"] < need:
            return (gx, gy), 0.5, "CHARGE", [0.0, 0.0]  # stand still and let energy refill
        if self.climb_t is None:
            self.climb_t = t
        if t - self.climb_t > ROOF_CLIMB_TIMEOUT:
            mf.log(f"t={t:.1f} climb failed, dropping roof plan")
            self.roofs = [r for r in self.roofs if r is not p]
            self.roof_plan = None
            self.climb_t = None
            return None
        return (ex, ey), 0.5, "CLIMB", into_wall
