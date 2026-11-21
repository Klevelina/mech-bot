"""Supply drops: go / wait at cover / capture, and snipe or contest someone else's capture."""
import math
import time

import mechfight as mf
from config import *  # noqa: F401,F403  (switches and tuning numbers)
from util import wrap  # noqa: F401


class DropsMixin:
    # ---------- supply drops
    def contest_goal(self, obs, me, t):
        """Someone else is capturing a drop. Two mechs in the ring freeze its clock, so we only need to get in
        before it finishes; then the fight decides. Go only if it's alone there, we arrive in time,
        and we'd win the fight at ring distance. Returns the ring center or None."""
        x, y = me["pos"][0], me["pos"][1]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        if hp_frac < CONTEST_MIN_HP:
            return None
        st = me["stats"]
        speed = st.get("boost_speed", 19.5) if me["energy"] > 40 else st.get("walk_speed", 11.7)
        for d in obs.get("drops", []):
            holder = d.get("holder")
            if holder is None or d.get("lands_in", 0) > 0:
                continue
            px, py = d["pos"][0], d["pos"][1]
            dist = math.hypot(px - x, py - y)
            if holder == me["id"]:
                if dist < 30.0:  # our clock is running (e.g. they stepped out): stay in the ring and finish
                    return self.nav.center(self.nav.nearest_main(px, py)), d["id"], "HOLDRING", None
                continue
            if self.late_zone or not CONTEST_OTHERS:
                continue  # keep our HP, no fights over drops
            rad = d.get("radius", 8.0)
            inside = dist < rad * 0.8
            remaining = d.get("capture_s", 4.0) - d.get("progress", 0.0)
            eta = max(0.0, dist - rad * 0.85) * 1.2 / speed  # time to just inside the ring edge
            seen = next((m for m in obs["mechs"] if m["id"] == holder), None)
            their_hp = seen["hp"] if seen and seen.get("hp") is not None else self.emodel.hp.get(holder, 1400.0)
            # 1) snipe: from where we stand, can we kill it before its clock runs out? (no running in the open)
            if seen is not None and not inside and self.can_hit(me, seen):
                hd = math.hypot(seen["pos"][0] - x, seen["pos"][1] - y)
                if self.our_dps(me, seen, hd, True) * max(0.0, remaining - 0.5) >= their_hp:
                    return (x, y), d["id"], "SNIPE", holder
            # 2) contest: run into the ring (freezes its clock) - only if close and we'd win
            if not inside and (dist > CONTEST_MAX_DIST or eta > remaining - 0.3):
                continue
            # anyone else around the drop? then it's a brawl: keep our HP
            crowded = False
            for key, (et, qx, qy) in self.enemies.items():
                if key != holder and t - et < 10.0 and math.hypot(qx - px, qy - py) < 80.0:
                    if isinstance(key, tuple) and holder in self.emodel.pos and \
                            math.hypot(qx - self.emodel.pos[holder][1][0], qy - self.emodel.pos[holder][1][1]) < 40:
                        continue  # a sound from the holder itself
                    crowded = True
                    break
            if crowded:
                continue
            # would we win at ring distance?
            pseudo = seen or {"id": holder, "kind": "mech", "pos": [px, py, d["pos"][2]], "cubes": 0,
                              "aim": [math.atan2(y - py, x - px), 0.0]}  # unseen: assume it faces us
            ours = self.our_dps(me, pseudo, 6.0, True)
            theirs = max(1.0, self.emodel.dps_at(holder, 6.0, t))
            if their_hp / ours < 0.8 * me["hp"] / theirs:
                c = self.nav.nearest_main(px, py)
                return self.nav.center(c), d["id"], "CONTEST", holder
        return None

    def _contest(self, obs, me, t):
        self.contest = self.contest_goal(obs, me, t)
        return self.contest is not None

    def _drop(self, obs, me, t):
        self._drop_now = self.drop_goal(obs, me, t)
        return self._drop_now is not None

    def drop_goal(self, obs, me, t):
        """(goal, stop radius, label) while going for a supply drop, else None."""
        nav = self.nav
        x, y = me["pos"][0], me["pos"][1]
        drops = {d["id"]: d for d in obs.get("drops", [])}
        walk = me["stats"].get("walk_speed", 11.7)
        if self.drop_target is not None:
            d = drops.get(self.drop_target)
            if d is None:
                self.drop_target = None  # captured (by us or someone else)
            elif (d.get("holder") not in (None, me["id"]) and d.get("progress", 0) > 0.5) or t - self.drop_since > 75:
                self.drop_banned.add(self.drop_target)  # someone else is taking it, or it's taking too long
                self.drop_target = None
        if self.drop_target is None and t - self.drop_check_t > 1.0:
            self.drop_check_t = t
            hp_frac = me["hp"] / max(1.0, me["max_hp"])
            maxd = DROP_MAX_DIST * (1.3 if hp_frac < 0.5 else 1.0)
            best, be = None, 1e18
            for d in drops.values():
                if d["id"] in self.drop_banned or d.get("holder") not in (None, me["id"]) or self.late_zone:
                    continue
                px, py = d["pos"][0], d["pos"][1]
                dist = math.hypot(px - x, py - y)
                eta = dist * 1.3 / walk
                if dist > maxd or (d.get("lands_in", 0) <= 0 and eta > 25):
                    continue
                if self.danger(px, py, t, DROP_DANGER_R, 20.0):
                    continue
                if eta < be:
                    best, be = d["id"], eta
            if best is not None:
                self.drop_target, self.drop_since = best, t
                mf.log(f"t={t:.1f} going for drop {best}")
        if self.drop_target is None:
            return None
        d = drops[self.drop_target]
        px, py = d["pos"][0], d["pos"][1]
        eta = math.hypot(px - x, py - y) * 1.3 / walk
        if d.get("lands_in", 0) > eta + 4:
            # early: wait at cover near it instead of standing in the open
            st = self.drop_stage.get(self.drop_target)
            if st is None:
                best, bd = None, 35.0
                for c, cx, cy in nav.cover:
                    dd = math.hypot(cx - px, cy - py)
                    if 10.0 < dd < bd:
                        best, bd = (cx, cy), dd
                st = best or nav.center(nav.nearest_main(px + 15.0, py))
                self.drop_stage[self.drop_target] = st
            return st, 3.0, "DROPWAIT"
        c = nav.nearest_main(px, py)
        return nav.center(c), d.get("radius", 8.0) * 0.4, "DROP"
