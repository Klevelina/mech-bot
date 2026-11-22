"""Supply drops: go / wait at cover / capture, and snipe or contest someone else's capture."""
import math
import time

import mechfight as mf
from config import *  # noqa: F401,F403  (switches and tuning numbers)
from util import wrap  # noqa: F401
from playbook import our_role


class DropsMixin:
    # ---------- supply drops
    def contest_goal(self, obs, me, t):
        """Someone else is capturing a drop. Two mechs in the ring freeze its clock, so we only need to get in
        before it finishes; then the fight decides. Go only if it's alone there, we arrive in time,
        and we'd win the fight at ring distance. Returns the ring center or None."""
        x, y = me["pos"][0], me["pos"][1]
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        # late (5 or fewer alive / small circle): contest only with the old 50% floor, no low-HP dives into a crossfire
        if hp_frac < (CONTEST_MIN_HP_LATE if getattr(self, "phase", "") == "late" else CONTEST_MIN_HP):
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
            if self.late_zone:
                continue  # late game: keep our HP, no fights over drops
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
            cwin = 3.0 if (self.wait_calm and dist < 70.0) else 10.0  # watching from close by: what's there now
            for key, (et, qx, qy) in self.enemies.items():
                if key != holder and t - et < cwin and math.hypot(qx - px, qy - py) < 80.0:
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
            # odds = (seconds until we die) / (seconds to kill it); the drop refills our HP, so outlasting it is enough
            if (me["hp"] / theirs) / max(0.1, their_hp / ours) >= DROP_WIN_R:
                c = self.nav.nearest_main(px, py)
                return self.nav.center(c), d["id"], "CONTEST", holder
        return None

    def watch_spot(self, me, d, t):
        """Where to wait out a fight at drop d, by our weapons (DROP_WATCH_BANDS): a cover spot in that distance
        band from the drop, hidden from every fighter we saw or heard there - or, holding a laser, one with a
        clear line to the drop so we can snipe whoever captures. Nearest such spot to us."""
        nav = self.nav
        x, y = me["pos"][0], me["pos"][1]
        px, py, pz = d["pos"]
        lo, hi, need = DROP_WATCH_BANDS.get(our_role(me["weapons"]), (DROP_WATCH_R[0], DROP_WATCH_R[1], "hidden"))
        eyes = []
        for k, (et, qx, qy) in self.enemies.items():
            if t - et < 5.0 and math.hypot(qx - px, qy - py) < DROP_DANGER_R + 20.0:
                st = self.emodel.pos.get(k) if not isinstance(k, tuple) else None
                qz = st[1][2] if st else nav.ground_z(qx, qy)
                eyes.append((qx, qy, qz + 8.0))
        deadline = time.perf_counter() + 0.010
        first = None
        for extra in (0.0, 25.0, 50.0):  # nothing suitable in our band: look a little further out
            band = sorted((math.hypot(cx - x, cy - y), cx, cy) for c, cx, cy in nav.cover
                          if lo < math.hypot(cx - px, cy - py) < hi + extra and nav.in_safe(c))  # never outside the zone
            if band and first is None:
                first = (band[0][1], band[0][2])
            for _, cx, cy in band[:40]:
                if time.perf_counter() > deadline:
                    break
                top = nav.surf[nav.cell_of(cx, cy)] + 8.5
                if need == "los":
                    if nav.los(cx, cy, top - 0.5, px, py, pz + 4.5):
                        return (cx, cy)
                elif not any(nav.los(ex, ey, ez, cx, cy, top) for ex, ey, ez in eyes):
                    return (cx, cy)
        if first is not None:
            return first  # nothing suitable found in time: the nearest cover spot in the band
        a = math.atan2(y - py, x - px)
        return nav.center(nav.nearest_main(px + hi * math.cos(a), py + hi * math.sin(a)))

    def drop_fight_ok(self, me, px, py, t):
        """Enemies seen/heard near the drop recently: go anyway if it's one enemy we'd outlast (the capture refills
        our HP, so losing HP doesn't matter as long as we live). Two or more: a brawl, skip."""
        x, y = me["pos"][0], me["pos"][1]
        if SAFE_DROPS and getattr(self, "all_in", False):
            # vs the squad: only a drop with nobody seen or heard near it lately - never a fight over one
            return not any(t - et < SAFE_DROP_DANGER_S and math.hypot(qx - px, qy - py) < SAFE_DROP_DANGER_R
                           for et, qx, qy in self.enemies.values())
        # watching from close by we see/hear what's there now: only the last 3 s count (a dead brawler drops out)
        win = 3.0 if (self.wait_calm and math.hypot(px - x, py - y) < 70.0) else DROP_DANGER_S
        near = [(k, qx, qy) for k, (et, qx, qy) in self.enemies.items()
                if t - et < win and math.hypot(qx - px, qy - py) < DROP_DANGER_R]
        if not near:
            return True
        if not DROP_FIGHT or getattr(self, "phase", "") == "late":  # late: no fighting over drops (crossfire)
            return False
        mechs = [(k, qx, qy) for k, qx, qy in near if not isinstance(k, tuple)]
        # sounds within 40 m of a mech we saw there are that mech's own
        sounds = [(k, qx, qy) for k, qx, qy in near if isinstance(k, tuple) and
                  not any(math.hypot(qx - mx, qy - my) < 40.0 for _, mx, my in mechs)]
        if len(mechs) + (1 if sounds else 0) > 1:
            return False
        if mechs:
            i = mechs[0][0]
            st = self.emodel.pos.get(i)
            pos = list(st[1]) if st else [mechs[0][1], mechs[0][2], 0.0]
            hp = self.emodel.hp.get(i, 1400.0)
        else:  # only heard: an unknown mech at full HP
            i, pos, hp = sounds[0][0], [sounds[0][1], sounds[0][2], 0.0], 1400.0
        pseudo = {"id": i, "kind": "mech", "pos": pos, "cubes": 0, "hp": hp,
                  "aim": [math.atan2(y - pos[1], x - pos[0]), 0.0]}  # assume it faces us
        return self.fight_odds(me, pseudo, t, at=30.0) >= DROP_WIN_R

    def heal_drop_goal(self, obs, me, t):
        """HEAL_DROP (all in, hurt, in a fight): the nearest supply drop we can get to - it repairs us in full, so it
        comes before cover and before the fight. (goal, stop radius) or None."""
        if not HEAL_DROP or not getattr(self, "all_in", False) or self.nav is None or self.late_zone:
            return None
        if me["hp"] >= HEAL_DROP_HP * me["max_hp"]:
            return None
        x, y = me["pos"][0], me["pos"][1]
        walk = me["stats"].get("walk_speed", 11.7)
        z = obs["zone"]
        best, bd = None, HEAL_DROP_R
        for d in obs.get("drops", []):
            h = d.get("holder")
            if h is not None and h != me["id"] and d.get("progress", 0) > 0.5 * d.get("capture_s", 4.0):
                continue  # someone else is most of the way through its capture
            px, py = d["pos"][0], d["pos"][1]
            dist = math.hypot(px - x, py - y)
            if dist >= bd or d.get("lands_in", 0) > dist * 1.3 / walk + HEAL_DROP_EARLY_S:
                continue
            if math.hypot(px - z["center"][0], py - z["center"][1]) > z["radius"] - 4.0:
                continue
            best, bd = d, dist
        if best is None:
            return None
        c = self.nav.nearest_main(best["pos"][0], best["pos"][1])
        return self.nav.center(c), best.get("radius", 8.0) * 0.4

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
            elif self.late_zone and d.get("holder") != me["id"]:
                self.drop_target = None  # the final small circle started: drop it (unless we're capturing it now)
            elif SAFE_DROPS and getattr(self, "all_in", False) and                     not self.drop_fight_ok(me, d["pos"][0], d["pos"][1], t):
                self.drop_banned.add(self.drop_target)  # someone showed up near it: not worth a fight vs the squad
                self.drop_target = None
            elif (not self.wait_calm and d.get("holder") not in (None, me["id"]) and d.get("progress", 0) > 0.5) \
                    or t - self.drop_since > 75:
                self.drop_banned.add(self.drop_target)  # someone else is taking it, or it's taking too long
                self.drop_target = None
        if self.drop_target is None and t - self.drop_check_t > 1.0:
            self.drop_check_t = t
            hp_frac = me["hp"] / max(1.0, me["max_hp"])
            maxd = DROP_MAX_DIST * (1.3 if hp_frac < 0.5 else 1.0)
            if SAFE_DROPS and getattr(self, "all_in", False):
                maxd = SAFE_DROP_R
            best, be = None, 1e18
            for d in drops.values():
                if d["id"] in self.drop_banned or d.get("holder") not in (None, me["id"]) or self.late_zone:
                    continue
                px, py = d["pos"][0], d["pos"][1]
                dist = math.hypot(px - x, py - y)
                eta = dist * 1.3 / walk
                if dist > maxd or (d.get("lands_in", 0) <= 0 and eta > 25):
                    continue
                if not self.drop_fight_ok(me, px, py, t):
                    if not self.wait_calm:
                        continue
                    eta += 1000.0  # a fight there: still a target (wait nearby for it to calm down), but last choice
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
                    if 10.0 < dd < bd and nav.in_safe(c):
                        best, bd = (cx, cy), dd
                st = best or nav.center(nav.nearest_main(px + 15.0, py))
                self.drop_stage[self.drop_target] = st
            return st, 3.0, "DROPWAIT"
        if self.wait_calm and (d.get("holder") not in (None, me["id"]) or not self.drop_fight_ok(me, px, py, t)):
            # a fight (or someone else's capture) at the drop: wait at cover nearby, hold fire, re-judge every
            # decision; the contest rules snipe or rush a lone capturer we'd beat
            cached = self.drop_watch.get(self.drop_target)
            if cached is None or t - cached[1] > 2.0:  # fighters move: re-check the spot every 2 s
                cached = self.drop_watch[self.drop_target] = (self.watch_spot(me, d, t), t)
            return cached[0], 3.0, "DROPWATCH"
        c = nav.nearest_main(px, py)
        return nav.center(c), d.get("radius", 8.0) * 0.4, "DROP"
