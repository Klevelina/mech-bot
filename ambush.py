"""Ambush on the hunter's path (squad only). The house squad shares vision, so a hunter out of our sight still
walks straight at us along the streets. Plan its route (A* from where it is to us), find where on that route it
first comes into view of spots near us, and wait at the spot where it appears at our weapon's best range:
standing still (silent), aimed at that point, magazine topped up. When it shows, the normal fight logic takes over
with the first shots ours."""
import math
import time

import mechfight as mf
from config import *  # noqa: F401,F403
from nav import Search


class AmbushMixin:
    amb = None          # current plan (dict) or None
    amb_ban = None      # hunter id -> don't ambush it again before this time (it never came)

    def _amb_hunters(self, me, t, max_r=AMB_MAX_R):
        """Enemy mechs we know about (seen within AMB_MEM_S), not too far: [(dist, id, pos, vel, seen_t)]."""
        x, y = me["pos"][0], me["pos"][1]
        out = []
        for i, (et, pos, vel) in self.epos.items():
            if i >= 100 or t - et > AMB_MEM_S:
                continue
            d = math.hypot(pos[0] - x, pos[1] - y)
            if d > max_r:
                continue
            out.append((d, i, pos, vel, et))
        out.sort(key=lambda h: h[0])
        return out

    def ambush_update(self, me, mechs, t, t_start):
        """True while we ambush (goal = self.amb["spot"], aim = self.amb["P"] once planned)."""
        if self.amb_ban is None:
            self.amb_ban = {}
        nav = self.nav
        x, y, z = me["pos"]
        # someone can shoot us right now, or just did: that's a fight, not an ambush
        if any(self.can_hit(me, m) for m in mechs) or t - self.last_dmg_t < 3.0 or self.late_zone:
            self.amb = None
            return False
        # free-for-all: nobody shares vision, so only someone seen walking our way is worth waiting for
        ffa = getattr(self, "situation", "squad") == "ffa"
        if ffa and not AMBUSH_FFA:
            self.amb = None
            return False
        hunters = [h for h in self._amb_hunters(me, t, AMB_FFA_MAX_R if ffa else AMB_MAX_R)
                   if t >= self.amb_ban.get(h[1], -1e9)]
        if not hunters:
            self.amb = None
            return False
        # the nearest one is the one we wait for: a second enemy closing in becomes the target (fresh plan)
        d, hid, hpos, hvel, het = hunters[0]
        a = self.amb
        if ffa and (a is None or a["hid"] != hid):
            ux, uy = (x - hpos[0]) / max(d, 1.0), (y - hpos[1]) / max(d, 1.0)
            if t - het > 0.5 or hvel[0] * ux + hvel[1] * uy < AMB_FFA_APPROACH:
                self.amb = None
                return False
        # leaving (fast, away from us) and not close: not coming for us
        if d > 60.0 and t - het < 0.5:
            ux, uy = (x - hpos[0]) / max(d, 1.0), (y - hpos[1]) / max(d, 1.0)
            if hvel[0] * ux + hvel[1] * uy < -3.0:
                self.amb = None
                return False
        if a is not None and t - a["t0"] > (AMB_FFA_HOLD_S if ffa else AMB_HOLD_S):
            self.amb_ban[hid] = t + AMB_BAN_S  # it never came: go back to normal play for a while
            self.amb = None
            return False
        replan = a is None or a["hid"] != hid or t - a["t_plan"] > AMB_REPLAN_S or \
            math.hypot(hpos[0] - a["hpos"][0], hpos[1] - a["hpos"][1]) > AMB_REPLAN_M
        others = [(h[2][0], h[2][1], h[2][2] + 8.0) for h in hunters[1:4]]
        if not replan and a.get("spot") is not None and a["pts"] is not None and a["ci"] >= len(a["cands"]):
            # another enemy could see our waiting spot now (flanking, or following behind): pick a new one
            sx, sy = a["spot"]
            stop = nav.surf[nav.cell_of(sx, sy)] + 8.5
            replan = any(nav.los(ox, oy, oz, sx, sy, stop, skip=1.0) for ox, oy, oz in others)
        if replan:
            t0 = a["t0"] if (a is not None and a["hid"] == hid) else t
            hc = nav.nearest_main(hpos[0], hpos[1])
            self.amb = a = {"hid": hid, "hpos": list(hpos), "t0": t, "t_plan": t, "search": Search(nav, hc, nav.cell_of(x, y)),
                            "pts": None, "cands": None, "ci": 0, "best": None, "bs": 1e18,
                            "spot": a["spot"] if a is not None and a["hid"] == hid else None,
                            "P": a["P"] if a is not None and a["hid"] == hid else None,
                            "eta_at": a["eta_at"] if a is not None and a["hid"] == hid else t + 99.0}
            a["t0"] = t0
        deadline = t_start + AMB_SLICE_S
        if a["pts"] is None:
            if not a["search"].step(deadline):
                return True  # still planning: hold (or keep going to the last spot) meanwhile
            cells = a["search"].path or []
            a["search"] = None
            # route points every ~4 m, hunter end first, with cumulative walking distance from the hunter
            pts, acc, prev = [], 0.0, None
            for k, c in enumerate(cells):
                cx, cy = nav.center(c)
                if prev is not None:
                    acc += math.hypot(cx - prev[0], cy - prev[1])
                prev = (cx, cy)
                if k % 2 == 0:
                    pts.append((cx, cy, nav.surf[c] + 4.5, acc))
            a["pts"] = pts
            a["cands"] = self._amb_cands(x, y, hpos)
            a["ci"] = 0
        pts, cands = a["pts"], a["cands"]
        if not pts:
            self.amb = None
            return False
        pref = AMB_RANGE.get(self.style, 40.0)
        heye = (hpos[0], hpos[1], hpos[2] + 8.0)
        n = 0
        while a["ci"] < len(cands):
            c = cands[a["ci"]]
            a["ci"] += 1
            n += 1
            cx, cy = nav.center(c)
            top, eye = nav.surf[c] + 8.5, nav.surf[c] + 8.0
            if nav.los(heye[0], heye[1], heye[2], cx, cy, top, skip=1.0) or \
                    any(nav.los(ox, oy, oz, cx, cy, top, skip=1.0) for ox, oy, oz in others):
                continue  # it (or another hunter) would see us there already
            hit = None
            for px, py, pz, acc in pts:  # first route point (from the hunter's end) in view of this spot
                if math.hypot(px - cx, py - cy) < 200.0 and nav.los(cx, cy, eye, px, py, pz, skip=1.0):
                    hit = (px, py, pz, acc)
                    break
            if hit is not None:
                e = math.hypot(hit[0] - cx, hit[1] - cy)
                if AMB_MIN_EMERGE <= e <= max(AMB_MAX_EMERGE, 2.0 * pref):
                    s = abs(e - pref) + AMB_WALK_W * math.hypot(cx - x, cy - y)
                    if s < a["bs"]:
                        a["bs"], a["best"] = s, (c, hit)
            if n & 3 == 0 and time.perf_counter() > deadline:
                break
        if a["ci"] >= len(cands) and a["best"] is not None:
            c, hit = a["best"]
            a["spot"], a["P"] = nav.center(c), hit[:3]
            # seconds until it reaches that point at walking pace (from when we last knew where it was)
            a["eta_at"] = t + hit[3] / me["stats"].get("walk_speed", 11.7) - (t - het)
            a["het"] = het
            if a.get("logged") != a["spot"]:
                a["logged"] = a["spot"]
                mf.log(f"t={t:.1f} ambush {hid} at {a['spot'][0]:.0f},{a['spot'][1]:.0f} -> appears at "
                       f"{hit[0]:.0f},{hit[1]:.0f} ({math.hypot(hit[0] - a['spot'][0], hit[1] - a['spot'][1]):.0f} m) "
                       f"in ~{a['eta_at'] - t:.1f} s")
        elif a["ci"] >= len(cands) and a["spot"] is None:
            self.amb_ban[hid] = t + AMB_BAN_S  # nowhere good near us: normal play
            self.amb = None
            return False
        return True

    def _amb_cands(self, x, y, hpos):
        """Candidate waiting spots: our own cell and rings up to AMB_R m around us, safe and walkable,
        nearest first, a bit more on the side away from the hunter."""
        nav = self.nav
        out, seen = [], set()
        c0 = nav.cell_of(x, y)
        if nav.comp[c0] == nav.main:
            out.append(c0)
            seen.add(c0)
        away = math.atan2(y - hpos[1], x - hpos[0])
        r = 6.0
        while r <= AMB_R:
            k = max(8, int(2 * math.pi * r / 7.0))
            for i in range(k):
                ang = away + 2 * math.pi * i / k
                c = nav.cell_of(x + r * math.cos(ang), y + r * math.sin(ang))
                if c in seen or nav.comp[c] != nav.main or not nav.in_safe(c):
                    continue
                seen.add(c)
                out.append(c)
            r += 6.0
        return out

    def ambush_reload_ok(self, me, t, reload_s):
        """Safe to start a reload while waiting: the hunter is still tracked (so its arrival time is real) and won't
        appear before the reload ends, and no other enemy is known close by (it could come round another corner)."""
        a = self.amb
        if a is None or a.get("P") is None or t - a.get("het", -1e9) > 5.5:
            return False
        if a["eta_at"] - t < reload_s + 0.5:
            return False
        return not any(h[1] != a["hid"] for h in self._amb_hunters(me, t, AMB_RELOAD_CLEAR_R))

    def ambush_aim(self, me):
        """[yaw, pitch] at where the hunter will appear, or None while still planning."""
        a = self.amb
        if a is None or a.get("P") is None:
            return None
        x, y, z = me["pos"]
        mz = z + me["stats"].get("muzzle_height", 7)
        px, py, pz = a["P"]
        h = math.hypot(px - x, py - y)
        st = me["stats"]
        pitch = max(st.get("min_pitch", -0.7), min(st.get("max_pitch", 1.3), math.atan2(pz - mz, max(h, 1.0))))
        return [math.atan2(py - y, px - x), pitch]
