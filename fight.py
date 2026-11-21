"""Fighting: aiming and firing, target choice, escape from attackers we can't hit, and where to
stand in a fight (hunt, flank, avoid, hide, threat map, loadout spacing)."""
import math
import time

import mechfight as mf
from config import *  # noqa: F401,F403  (switches and tuning numbers)
from util import wrap  # noqa: F401
from playbook import band, our_role


class FightMixin:
    # ---------- aiming / firing
    def aim_at(self, me, pos, vel, zoff, speed, k=1.0):
        """Aim to hit `pos` (+zoff) moving at `vel`, with a bullet of `speed` (0 = hitscan).
        `k` scales the horizontal lead (learned). Returns ([yaw, pitch], horizontal distance)."""
        mx, my, mz = me["pos"]
        mz += me["stats"].get("muzzle_height", 7)
        px, py, pz = pos
        vx, vy, vz = vel if vel else (0.0, 0.0, 0.0)
        vx, vy = vx * k, vy * k
        # falling (not jetting) target: gravity pulls it down during the flight; vertical lead isn't scaled by k
        g, gz = 0.0, -1e9
        if vz < 0 and self.nav is not None:
            gz = self.nav.ground_z(px, py)
            if pz > gz + 1.5:
                g = self.gravity
        tt = 0.0
        for _ in range(3):
            ax, ay = px + vx * tt, py + vy * tt
            az = max(pz + vz * tt - 0.5 * g * tt * tt, gz) + zoff
            d = math.sqrt((ax - mx) ** 2 + (ay - my) ** 2 + (az - mz) ** 2)
            tt = d / speed if speed else 0.0
        h = math.hypot(ax - mx, ay - my)
        st = me["stats"]
        pitch = max(st.get("min_pitch", -0.7), min(st.get("max_pitch", 1.3), math.atan2(az - mz, h)))
        return [math.atan2(ay - my, ax - mx), pitch], h

    def fire(self, me, dist, aim, size, is_mech, late=False, gun_ok=True):
        """fire flags for both slots at a target `dist` away along commanded `aim`; size = target radius."""
        reserve = ENERGY_RESERVE_LATE if late else ENERGY_RESERVE
        cur = me["aim"]
        err = abs(wrap(cur[0] - aim[0])) + abs(cur[1] - aim[1])
        tol = max(0.015, math.atan2(size, max(dist, 1.0)))
        out = [False, False]
        for i, w in enumerate(me["weapons"]):
            if not w:
                continue
            k, st = w["kind"], w["stats"]
            if k == "gun":
                out[i] = gun_ok and err < tol * 1.5 and dist < st.get("range", 230) and w.get("ammo", 1) > 0
            elif k == "lightning":
                cone = math.radians(st.get("cone_deg", 30)) / 2
                out[i] = dist < st.get("range", 45) * 0.95 and abs(wrap(cur[0] - aim[0])) < cone * 0.8 \
                    and me["energy"] > reserve
            elif k == "laser" and is_mech:
                charge = w.get("charge", 0)
                full = st.get("charge", 0.75)
                # hold to charge while on target; let go once full (fires on release)
                # don't start a charge with a lightning carrier close by: its arc would cancel it
                out[i] = dist < st.get("range", 280) and w.get("cooldown", 0) <= 0.05 and charge < full \
                    and (charge > 0 or (err < tol * 3 and not (PLAYBOOK and getattr(self, "lit_near", False))))
        return out

    def hit_zoff(self, me, m):
        """Height above the target's feet we have a clear line of fire to (center, then upper body), or None."""
        if m["kind"] == "npc":
            zoffs = (1.75, 3.0)
        else:
            scale = 1.0 + self.size_per_cube * m.get("cubes", 0)
            zoffs = (4.5 * scale, 7.5 * scale)
        if self.nav is None:
            return zoffs[0]
        x, y, z = me["pos"]
        mz = z + me["stats"].get("muzzle_height", 7)
        p = m["pos"]
        for zoff in zoffs:
            if self.nav.los(x, y, mz, p[0], p[1], p[2] + zoff):
                return zoff
        return None

    def can_hit(self, me, m):
        return self.hit_zoff(me, m) is not None

    def fight_odds(self, me, m, t, at=None):
        """Who wins the damage race against m: (seconds until we die to everyone shooting us) divided by
        (seconds for us to kill m). Above 1 we win. `at`: judge it at that distance (a hunt we're about to start;
        the target is assumed to shoot back at full rate)."""
        x, y = me["pos"][0], me["pos"][1]
        d = math.hypot(m["pos"][0] - x, m["pos"][1] - y) if at is None else at
        hittable = True if at is not None else self.can_hit(me, m)
        kill_s = (m.get("hp") or 1400.0) / self.our_dps(me, m, d, hittable)
        if at is not None:
            log = self.dmg_log.get(m["id"])
            seen = sum(a for (ht, a) in log if t - ht < 3.0) / 3.0 if log else 0.0
            theirs = max(self.emodel.dps_at(m["id"], d, t), seen)
        else:
            theirs = self.threat_dps(m, d, t)
        others = sum(dps for o, dps in self.active if o["id"] != m["id"] and
                     (t - self.attackers.get(o["id"], -1e9) < 4.0 or t - self.npc_attackers.get(o["id"], -1e9) < 4.0))
        die_s = me["hp"] / max(1.0, theirs + others)
        return die_s / max(0.1, kill_s)

    def weaker(self, me, m):
        """An easy early pick: less HP than us (by AGGRO_WEAKER), or its back turned to us."""
        hp = m.get("hp")
        if hp is not None and hp <= AGGRO_WEAKER * me["hp"]:
            return True
        x, y = me["pos"][0], me["pos"][1]
        return abs(wrap(math.atan2(y - m["pos"][1], x - m["pos"][0]) - m["aim"][0])) > math.pi / 2

    # ---------- threat weighing
    def threat_dps(self, m, d, t):
        """Damage per second foe m does (or could do) to us: max of what we measured and an estimate."""
        log = self.dmg_log.get(m["id"])
        seen = sum(a for (ht, a) in log if t - ht < 3.0) / 3.0 if log else 0.0
        if m["kind"] == "npc":
            est = self.npc_dps * 0.7 if d < 90 else 0.0
        else:
            est = self.emodel.dps_at(m["id"], d, t)  # known / inferred / expected weapons by range
            attacking = t - self.attackers.get(m["id"], -1e9) < 4.0
            est *= 1.0 if attacking else 0.5
        return max(est, seen)

    def our_dps(self, me, m, d, hittable):
        """Damage per second we can expect to deal to foe m."""
        if not hittable:
            return 1.0
        total = 0.0
        npc = m["kind"] == "npc"
        for w in me["weapons"]:
            if not w:
                continue
            k, st = w["kind"], w["stats"]
            if k == "gun" and d < st.get("range", 230):
                if npc:
                    p = 0.7
                else:
                    scale = 1.0 + self.size_per_cube * m.get("cubes", 0)
                    s = self.aim.surprise(m["id"], d / max(1.0, st.get("speed", 252)))
                    p = max(0.15, min(0.85, 1.0 - s / (5.0 * scale)))
                total += p * st.get("damage", 45) / max(0.05, st.get("cooldown", 0.24))
            elif k == "lightning" and d < st.get("range", 45) and me["energy"] > ENERGY_RESERVE_LATE:
                total += st.get("damage", 60) / max(0.05, st.get("cooldown", 0.25))
            elif k == "laser" and not npc and d < st.get("range", 280):
                total += st.get("damage", 315) / (st.get("charge", 0.75) + st.get("cooldown", 1.65))
        if not npc:
            x, y = me["pos"][0], me["pos"][1]
            if abs(wrap(math.atan2(y - m["pos"][1], x - m["pos"][0]) - m["aim"][0])) > math.pi / 2:
                total *= self.cfg["mech"].get("rear_mult", 1.5)  # its back is to us
        return max(total, 1.0)

    def pick_target_weighted(self, me, foes, t):
        """Fight whoever removes the most incoming damage per second of our shooting:
        value = (its dps on us) / (time for us to kill it). Fills self.active with foes that threaten us."""
        x, y = me["pos"][0], me["pos"][1]
        best, bs = None, -1e18
        self.active = []
        for m in foes:
            d = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
            tdps = self.threat_dps(m, d, t)
            if t - self.attackers.get(m["id"], -1e9) < 4.0 or t - self.npc_attackers.get(m["id"], -1e9) < 4.0 \
                    or (m["kind"] == "mech" and d < 200):
                self.active.append((m, tdps))
            hittable = self.can_hit(me, m)
            hp = m.get("hp") or (350.0 if m["kind"] == "npc" else 1400.0)
            ttk = hp / self.our_dps(me, m, d, hittable)
            v = (tdps + 5.0) / ttk  # +5: a foe that isn't shooting yet is still worth something
            if LIGHTNING_CLUSTER and m["kind"] == "mech" and d < 45.0 and \
                    any(w and w["kind"] == "lightning" for w in me["weapons"]):
                # the arc chains to others within chain_range: prefer the one with the most neighbours
                chain = self.wcfg.get("lightning", {}).get("chain_range", 14.0)
                nb = sum(1 for o in foes if o is not m and o["kind"] == "mech" and
                         math.hypot(o["pos"][0] - m["pos"][0], o["pos"][1] - m["pos"][1]) <= chain)
                v *= 1.0 + 0.7 * min(nb, 2)
            if m["id"] == self.cur:
                v *= 1.15  # don't flip-flop
            if v > bs:
                best, bs = m, v
        return best

    def pick_target(self, me, mechs, t):
        x, y = me["pos"][0], me["pos"][1]
        if WEIGH_THREATS:
            best = self.pick_target_weighted(me, mechs, t)  # also fills self.active (threats on us)
            if FOCUS and getattr(self, "situation", "") == "squad":
                return self.pick_target_focus(me, mechs, t) or best
            return best
        if not SMART_TARGET:
            return min(mechs, key=lambda m: math.dist(m["pos"], me["pos"]))
        best, bs = None, -1e18
        for m in mechs:
            mx, my = m["pos"][0], m["pos"][1]
            d = math.hypot(mx - x, my - y)
            s = -d / 10.0
            if t - self.attackers.get(m["id"], -1e9) < 4.0:
                s += 100.0
            if m.get("hp") is not None and m.get("max_hp"):
                s += 40.0 * (1.0 - m["hp"] / m["max_hp"])
            # their back to us: we're outside their front arc
            if abs(wrap(math.atan2(y - my, x - mx) - m["aim"][0])) > math.pi / 2:
                s += 15.0
            if m["id"] == self.cur:
                s += 10.0
            if not self.can_hit(me, m):
                s -= 60.0
            if s > bs:
                best, bs = m, s
        return best

    def laser_killshot(self, me, m, aim, dist, size, fire):
        """LASER_KILLSHOT: our laser is charging and what's in it already kills the target (with a safety margin):
        let go now (release fires) instead of charging on and giving it time to duck away."""
        cur = me["aim"]
        tol = max(0.015, math.atan2(size, max(dist, 1.0)))
        if abs(wrap(cur[0] - aim[0])) > tol or abs(cur[1] - aim[1]) > tol * 1.5:
            return fire  # not on target yet
        hp = m.get("hp")
        if not hp:
            return fire
        for i, w in enumerate(me["weapons"]):
            if not w or w["kind"] != "laser":
                continue
            st, charge = w["stats"], w.get("charge", 0.0)
            full = st.get("charge", 0.75)
            if charge < st.get("min_charge", 0.15) or charge >= full or dist > st.get("range", 280) * 0.97:
                continue
            frac = st.get("min_frac", 0.35) + (1 - st.get("min_frac", 0.35)) * charge / full
            dmg = st.get("damage", 315) * frac * (1 + self.cfg["cubes"].get("damage_per_cube", 0.04) *
                                                   min(me.get("cubes", 0), self.cfg["cubes"].get("stat_cap", 10)))
            if "mortar" in (m.get("weapons") or []):
                dmg *= 1 + st.get("cookoff", 0.6)
            if self._behind_us(me, m):
                dmg *= self.cfg["mech"].get("rear_mult", 1.5)
            if dmg * LASER_KILLSHOT_MARGIN >= hp:
                fire = list(fire)
                fire[i] = False  # release: the beam goes out now
        return fire

    def _behind_us(self, me, m):
        """We're outside m's front arc (its back / side is to us): our hits do rear damage."""
        x, y = me["pos"][0], me["pos"][1]
        return abs(wrap(math.atan2(y - m["pos"][1], x - m["pos"][0]) - m["aim"][0])) > math.pi / 2

    def fight_cube(self, me):
        """FIGHT_CUBES: the closest loose cube within reach mid-fight (closer when we're hurt), or None."""
        x, y = me["pos"][0], me["pos"][1]
        r = FIGHT_CUBES_R * (FIGHT_CUBES_HURT if me["hp"] < 0.5 * me["max_hp"] else 1.0)
        best, bd = None, r
        for it in self.mem.items.values():
            if it["kind"] != "cube":
                continue
            d = math.hypot(it["x"] - x, it["y"] - y)
            if d < bd and abs(it["z"] - me["pos"][2]) < 4.0:
                best, bd = (it["x"], it["y"]), d
        return best

    def allin_range(self, me, m):
        """All in: the distance we fight m from, by what we hold."""
        kinds = {w["kind"] for w in me["weapons"] if w}
        if m["kind"] == "npc":
            return ALLIN_RANGE["npc"]
        if "lightning" in kinds and me["energy"] > 12:
            return ALLIN_RANGE["lightning"]
        r = ALLIN_RANGE["laser"] if "laser" in kinds else ALLIN_RANGE["gun"]
        if "lightning" not in kinds:
            for k in (m.get("weapons") or []):
                r = max(r, ALLIN_RANGE_VS.get(k, 0.0))
        return r

    def allin_retreat(self, me, m, t, t_start, hp_frac):
        """SQUAD_ALL_IN: the one retreat rule. (x, y) to back off to, or None to keep fighting."""
        x, y = me["pos"][0], me["pos"][1]
        in_sight = [o for o in self._obs_mechs if o["kind"] == "mech" and
                    math.hypot(o["pos"][0] - x, o["pos"][1] - y) < ALLIN_NEAR_R]
        n = len(in_sight)
        bad = (n >= 2 and hp_frac < ALLIN_TWO_HP) or (n >= 3 and hp_frac < ALLIN_THREE_HP)
        tf = (m.get("hp") or 1400.0) / max(1.0, m.get("max_hp") or 1400.0)
        if hp_frac < ALLIN_LOW_HP and tf > hp_frac + 0.1:
            bad = True
        if tf < ALLIN_TARGET_LOW and hp_frac > 0.2 and n <= 2:
            bad = False  # it's nearly dead: finish it
        if bad:
            self.allin_bad_t = t
        if t - getattr(self, "allin_bad_t", -1e9) >= ALLIN_BACK_S:
            return None
        eyes = [(o["pos"][0], o["pos"][1], o["pos"][2] + 8.0) for o in in_sight] or \
            [(m["pos"][0], m["pos"][1], m["pos"][2] + 8.0)]
        if RETREAT_WALK:
            # nearest hidden spot by walking distance, out of sight of up to 4 enemies (head and middle)
            near4 = sorted(in_sight, key=lambda o: math.hypot(o["pos"][0] - x, o["pos"][1] - y))[:4] or [m]
            weyes = [(o["pos"][0], o["pos"][1], o["pos"][2] + 8.0) for o in near4]
            c = self.nav.walk_cover(weyes, x, y, me["stats"].get("height", 9.0), deadline=t_start + 0.014)
            if c >= 0:
                return self.nav.center(c)
        c = self.nav.flank_cell(eyes, None, x, y, ALLIN_COVER_R, t_start + 0.018)
        if c >= 0:
            return self.nav.center(c)
        return self.away_goal(me, m["pos"][0], m["pos"][1], 30.0, t_start)

    def engage_goal(self, me, m, t, d, theirs):
        """ENGAGE (squad): ((x, y), mode) to go at enemy m, or None to leave the position to the other rules.
        In our line of fire and further than our range: close in. Out of our line of fire: pursue it."""
        x, y = me["pos"][0], me["pos"][1]
        ex, ey = m["pos"][0], m["pos"][1]
        to_me = math.atan2(y - ey, x - ex)
        closing = (m["vel"][0] * (x - ex) + m["vel"][1] * (y - ey)) / max(d, 1.0)
        hp = m.get("hp") or 1400.0
        weak_f, weak_r = ENGAGE_WEAK
        near = ENGAGE_BAND_MIN
        if PLAYBOOK:
            near = max(near, band(our_role(me["weapons"]), theirs)[1])
        engage = d < near or t - self.attackers.get(m["id"], -1e9) < ENGAGE_HIT_S or \
            (closing > ENGAGE_CLOSING[0] and d < ENGAGE_CLOSING[1]) or \
            (m.get("max_hp") and hp < weak_f * m["max_hp"] and d < weak_r) or \
            (d < ENGAGE_AIMED_R and abs(wrap(m["aim"][0] - to_me)) < 0.25 and self.can_hit(me, m))
        if not engage:
            return None
        pref = HUNT_RANGE.get(self.style, 70.0)
        if getattr(self, "all_in", False):
            pref = self.allin_range(me, m)
        elif PLAYBOOK:
            lo, hi = band(our_role(me["weapons"]), theirs)
            pref = max(15.0, (lo + hi) / 2.0)
        if self.can_hit(me, m):
            if d <= pref + 10.0:
                return None  # in range with a shot: the usual in-fight positioning
            r, mode = pref, "ENGAGE"
        else:
            r, mode = min(pref, d) * 0.6, "PURSUE"  # it ducked out of our line of fire: go where we see it again
        px, py = self.clamp_safe(ex + r * math.cos(to_me), ey + r * math.sin(to_me))
        return self.nav.center(self.nav.nearest_main(px, py)), mode

    def pick_target_focus(self, me, mechs, t):
        """FOCUS (squad): the enemy mech we can hit that's closest to dying, sticking to our current target.
        None if no enemy mech is in our line of fire."""
        x, y = me["pos"][0], me["pos"][1]
        best, bs = None, 1e18
        for m in mechs:
            if m["kind"] != "mech":
                continue
            ex, ey = m["pos"][0], m["pos"][1]
            d = math.hypot(ex - x, ey - y)
            if d > FOCUS_MAX_D or not self.can_hit(me, m):
                continue
            hp = m.get("hp") or 1400.0
            s = d + FOCUS_HP_W * hp
            if m.get("max_hp") and hp <= 0.3 * m["max_hp"]:
                s -= FOCUS_LOW
            if m["id"] == self.cur:
                s -= FOCUS_STICK
            if t - self.attackers.get(m["id"], -1e9) < 4.0:
                s -= FOCUS_HITME
            closing = (m["vel"][0] * (x - ex) + m["vel"][1] * (y - ey)) / max(d, 1.0)
            if closing > 3.0:
                s -= FOCUS_CLOSING
            if s < bs:
                best, bs = m, s
        return best

    # ---------- escape from attackers we can't hit
    def laser_hide(self, me, mechs, t, t_start):
        """No laser of our own: a laser shooting us from beyond our reach (or charging on us) can't be answered.
        Break line of sight to it, if a hidden spot is close. Starts an escape and returns True when it does."""
        if any(w and w["kind"] == "laser" for w in me["weapons"]):
            return False  # we can shoot back at the same range
        x, y, z = me["pos"]
        vis = {m["id"]: m for m in mechs}
        threats = [i for i, ht in self.laser_hits.items() if t - ht < 3.0]
        for m in mechs:  # telegraphed: a visible laser charging with its aim on us
            if m.get("charging") and m["id"] not in threats and \
                    abs(wrap(m["aim"][0] - math.atan2(y - m["pos"][1], x - m["pos"][0]))) < 0.15:
                threats.append(m["id"])
        for i in threats:
            m = vis.get(i)
            if m is not None:
                src = m["pos"]
            elif i in self.epos and t - self.epos[i][0] < 10.0:
                src = self.epos[i][1]
            else:
                continue
            if math.hypot(src[0] - x, src[1] - y) < LASER_HIDE_MIN:
                continue  # close enough for our gun / lightning: fight it (the playbook rushes lasers)
            c = self.nav.hide_cell(src[0], src[1], src[2] + 8.0, x, y, LASER_HIDE_R, t_start + 0.012)
            if c < 0:
                continue
            self.escape = {"src": i, "goal": self.nav.center(c), "t": t, "from": list(src), "laser": True}
            mf.log(f"t={t:.1f} hide from laser {i} at {src[0]:.0f},{src[1]:.0f}")
            return True
        return False

    def update_escape(self, me, mechs, t, t_start):
        if not ESCAPE_UNHITTABLE or self.nav is None:
            self.escape = None
            return
        x, y, z = me["pos"]
        if self.escape is not None:
            src = self.escape["src"]
            hold = LASER_HOLD_S if self.escape.get("laser") else 4.0
            came = None
            if self.escape.get("laser"):
                came = next((m for m in mechs if m["id"] == src and self.can_hit(me, m) and
                             math.hypot(m["pos"][0] - x, m["pos"][1] - y) < LASER_HIDE_MIN), None)
            if came is not None:
                self.escape = None  # it followed us round the corner: close range, our gun's fight - shoot now
                return
            if t - max(self.attackers.get(src, -1e9), self.escape["t"]) > hold or t - self.escape["t"] > 20.0:
                if self.escape.get("laser") and self.cover is not None:
                    # don't walk back into its sightline: a cover spot it could see from there gets replaced
                    lx, ly, lz = self.escape["from"]
                    cx, cy = self.cover
                    if self.nav.los(lx, ly, lz + 8.0, cx, cy, self.nav.surf[self.nav.cell_of(cx, cy)] + 8.5):
                        self.bad.add(self.nav.cell_of(cx, cy))
                        self.zone_key = None  # choose a new cover spot next decision
                self.escape = None  # it stopped hurting us (or we gave it long enough)
            return
        if LASER_HIDE and self.laser_hide(me, mechs, t, t_start):
            return
        if t - self.escape_check_t < 0.5:
            return
        self.escape_check_t = t
        vis = {m["id"]: m for m in mechs}
        for aid, at in self.attackers.items():
            if t - at > 1.0 or aid >= 100:
                continue
            m = vis.get(aid)
            unhittable = False
            if m is not None:
                if not self.can_hit(me, m):
                    unhittable = True
                elif t - self.fire_since.get(aid, t) > NO_HIT_S and t - self.hits.get(aid, -1e9) > NO_HIT_S:
                    unhittable = True  # shooting for a while and nothing lands
                src = m["pos"]
            else:
                st = self.epos.get(aid)
                unhittable = True  # hurting us from out of sight
                if st is not None and t - st[0] < 10:
                    src = st[1]
                else:
                    b = self.att_bearing.get(aid, self.dmg_bearing[1])
                    src =[x + 80 * math.cos(b), y + 80 * math.sin(b), z + 10]
            if not unhittable:
                continue
            eye = src[2] + 8.0
            c = self.nav.hide_cell(src[0], src[1], eye, x, y, 50.0, t_start + 0.015)
            if c >= 0:
                goal = self.nav.center(c)
            else:  # nothing hides us close by: run directly away
                a = math.atan2(y - src[1], x - src[0])
                px, py = self.clamp_safe(x + 40 * math.cos(a), y + 40 * math.sin(a))
                goal = self.nav.center(self.nav.nearest_main(px, py))
            self.escape = {"src": aid, "goal": goal, "t": t, "from": list(src)}
            mf.log(f"t={t:.1f} escape from {aid} at {src[0]:.0f},{src[1]:.0f},{src[2]:.0f} -> {goal[0]:.0f},{goal[1]:.0f}")
            return

    # ---------- playstyle by loadout
    @staticmethod
    def loadout_style(weapons):
        kinds = {w["kind"] for w in weapons if w}
        if "lightning" in kinds:
            return "lightning"
        if "laser" in kinds:
            return "laser"
        return "gun"

    def away_goal(self, me, ex, ey, dist, t_start):
        """Somewhere ~dist m further from (ex, ey): a spot it can't see if one is close, else straight back."""
        x, y, z = me["pos"]
        nav = self.nav
        others = [o for o in getattr(self, "_obs_mechs", ()) if o["kind"] == "mech" and
                  math.hypot(o["pos"][0] - ex, o["pos"][1] - ey) > 3.0]
        if AWAY_HIDE_ALL and others:
            # hidden from this one AND every other visible enemy (don't back off into someone else's sights)
            eyes = [(ex, ey, nav.ground_z(ex, ey) + 8.0)] + [(o["pos"][0], o["pos"][1], o["pos"][2] + 8.0) for o in others]
            c = nav.flank_cell(eyes, None, x, y, 30.0, t_start + 0.008)
        else:
            c = nav.hide_cell(ex, ey, nav.ground_z(ex, ey) + 8.0, x, y, 30.0, t_start + 0.008)
        if c >= 0:
            cx, cy = nav.center(c)
            if math.hypot(cx - ex, cy - ey) > math.hypot(x - ex, y - ey) - 5:  # not toward it
                return (cx, cy)
        a = math.atan2(y - ey, x - ex)
        px, py = self.clamp_safe(x + dist * math.cos(a), y + dist * math.sin(a))
        return nav.center(nav.nearest_main(px, py))

    def choose_field(self, me, t, t_start, obs_mechs=()):
        """Threat map: score spots around us (stay, or 12 / 25 m in 12 directions) by
        expected damage we take there over FIELD_H s (+ while walking there) as a fraction of our HP,
        minus FIELD_KILL_W x the fraction of an enemy we'd kill there. Lowest wins. None if no foes known."""
        nav = self.nav
        x, y, z = me["pos"]
        foes = []
        for i, (et, pos, vel) in self.emodel.pos.items():
            age = t - et
            if age > 5.0 or math.hypot(pos[0] - x, pos[1] - y) > 300:
                continue
            foes.append((i, pos, 1.0 if age < 0.15 else 0.6))  # remembered (not seen now): less sure
        for m in obs_mechs:  # drones that are shooting at us
            if m["kind"] == "npc" and t - self.npc_attackers.get(m["id"], -1e9) < 4.0:
                foes.append((m["id"], m["pos"], 1.0))
        if not foes:
            return None
        foes.sort(key=lambda f: math.hypot(f[1][0] - x, f[1][1] - y))
        foes = foes[:4]
        my_hp = max(1.0, me["hp"])
        walk = me["stats"].get("walk_speed", 11.7)
        npc_hp = {m["id"]: m.get("hp", 350.0) for m in obs_mechs if m["kind"] == "npc"}

        def foe_dps(i, d):
            if i >= 100:
                return self.npc_dps * 0.7 if d < 90 else 0.0
            return self.emodel.dps_at(i, d, t)

        def eye_z(i, pos):
            return pos[2] + (3.0 if i >= 100 else 8.0)

        def incoming(px, py, top):
            s = 0.0
            for i, pos, w in foes:
                dps = foe_dps(i, math.hypot(pos[0] - px, pos[1] - py))
                if dps > 0 and nav.los(pos[0], pos[1], eye_z(i, pos), px, py, top, skip=1.0, step=2.0):
                    s += w * dps
            return s

        inc_now = incoming(x, y, z + 8.5)
        cands = [(x, y, z, True)]
        for k in range(12):  # rings interleaved so a time cut-off doesn't drop whole directions
            for r in (12.0, 25.0):
                a = k * math.pi / 6 + (0.26 if r > 20 else 0.0)
                c = nav.cell_of(x + r * math.cos(a), y + r * math.sin(a))
                if nav.comp[c] == nav.main and nav.in_safe(c):
                    cx, cy = nav.center(c)
                    cands.append((cx, cy, nav.surf[c], False))
        best, bs = None, 1e18
        for n, (px, py, pz, cur) in enumerate(cands):
            if n and time.perf_counter() > t_start + 0.012:
                break
            inc = inc_now if cur else incoming(px, py, pz + 8.5)
            off = 0.0
            for i, pos, w in foes:
                npc = i >= 100
                if not nav.los(px, py, pz + 7.0, pos[0], pos[1], pos[2] + (1.75 if npc else 4.5), step=2.0):
                    continue
                d = math.hypot(pos[0] - px, pos[1] - py)
                pseudo = {"id": i, "kind": "npc" if npc else "mech", "pos": pos, "cubes": 0,
                          "aim": [math.atan2(py - pos[1], px - pos[0]), 0.0]}  # assume it faces us
                hp = npc_hp.get(i, 350.0) if npc else self.emodel.hp.get(i, 1400.0)
                rate = self.our_dps(me, pseudo, d, True) / max(1.0, hp)
                off = max(off, w * rate)
            travel = math.hypot(px - x, py - y) / walk
            score = (inc * FIELD_H + travel * inc_now * 0.6) / my_hp - FIELD_KILL_W * off * FIELD_H
            if cur:
                score -= 0.05  # don't move for nothing
            if score < bs:
                best, bs = (px, py), score
        return best

    # ---------- late game / finishing kills
    def late_fire_ok(self, me, m, d, t):
        """Late game: shoot only if it's shooting us, or it dies quickly, or we're finishing it.
        (Dealing damage resets our 5 s regen timer, and every bit of HP counts in the final collapse.)"""
        if t - self.attackers.get(m["id"], -1e9) < 5.0:
            return True
        if self.finish is not None and self.finish[0] == m["id"]:
            return True
        # closing in on us: don't let it walk up unpunished
        x, y = me["pos"][0], me["pos"][1]
        ex, ey = m["pos"][0], m["pos"][1]
        if d < 50.0:
            return True
        to_us = math.atan2(y - ey, x - ex)
        closing = (m["vel"][0] * (x - ex) + m["vel"][1] * (y - ey)) / max(d, 1.0) > 2.0
        if d < 80.0 and closing and abs(wrap(m["aim"][0] - to_us)) < 0.5:
            return True
        hp = m.get("hp") or 1400.0
        return hp / self.our_dps(me, m, d, True) < LATE_QUICK_KILL_S

    def _finish(self, me, mechs, t):
        self.finish_now = self.finish_goal(me, mechs, t)
        return self.finish_now is not None

    def finish_goal(self, me, mechs, t):
        """A low-HP enemy that's leaving (or out of our line of fire): follow it for a few seconds to the
        range our weapon kills from. Returns where to go, or None. Sets self.finish_m to it when visible."""
        x, y = me["pos"][0], me["pos"][1]
        self.finish_m = None
        if me["hp"] / max(1.0, me["max_hp"]) < FINISH_MIN_HP:
            self.finish = None
            return None
        vis = {m["id"]: m for m in mechs}
        if self.finish is None:
            for m in mechs:
                if not m.get("hp") or not m.get("max_hp"):
                    continue
                frac = m["hp"] / m["max_hp"]
                ex, ey = m["pos"][0], m["pos"][1]
                # a weak one heading for a death pile we know about: take it, and the pile
                near_pile = PILE_FINISH and frac < PILE_FINISH_HP and any(
                    it["kind"] == "pile" and math.hypot(it["x"] - ex, it["y"] - ey) < 30.0
                    for it in self.mem.items.values())
                fin_hp = AGGRO_FINISH_HP if (EARLY_AGGRO and getattr(self, "phase", "") == "early") else FINISH_HP
                if frac >= fin_hp and not near_pile:
                    continue
                d = math.hypot(ex - x, ey - y) or 1.0
                leaving = (m["vel"][0] * (ex - x) + m["vel"][1] * (ey - y)) / d > 1.0
                if leaving or not self.can_hit(me, m) or near_pile:
                    self.finish = [m["id"], t + FINISH_S, list(m["pos"]), frac]
                    break
        if self.finish is None:
            return None
        fid, until, pos = self.finish[:3]
        if any(i != fid and t - at < 3.0 for i, at in self.attackers.items()):
            self.finish = None  # someone else is on us: not the time to chase
            return None
        m = vis.get(fid)
        if m is not None:
            start_frac = self.finish[3] if len(self.finish) > 3 else FINISH_HP
            if m.get("hp") and m.get("max_hp") and m["hp"] / m["max_hp"] > max(FINISH_HP, start_frac) * 1.6:
                self.finish = None  # it healed (drop?): not a quick kill any more
                return None
            pos = self.finish[2] = list(m["pos"])
            until = self.finish[1] = t + FINISH_S
            self.finish_m = m
        if t > until:
            self.finish = None
            return None
        pref = FINISH_RANGE.get(self.style, 45.0)
        d = math.hypot(pos[0] - x, pos[1] - y)
        if m is not None and d <= pref and self.can_hit(me, m):
            return (x, y)  # in range with a clear shot: stand and shoot
        a = math.atan2(y - pos[1], x - pos[0])
        r = min(pref * 0.6, d)
        px, py = self.clamp_safe(pos[0] + r * math.cos(a), pos[1] + r * math.sin(a))
        return self.nav.center(self.nav.nearest_main(px, py))

    def update_fight_goal(self, me, m, t, t_start):
        """Where to stand against target m, by our loadout. None = keep the normal goal (cover)."""
        if not PLAYSTYLE or self.nav is None or m is None:
            self.fight_goal = None
            return
        rushing = FINISH_RUSH and self.fight_mode == "RUSH" and t - self.fight_goal_t >= 0.3
        if t - self.fight_goal_t < 1.0 and m["id"] == self.fight_goal_id and not rushing:
            return  # same target: refresh once a second (0.3 s while rushing it); new target: right away
        self.fight_goal_t = t
        self.fight_goal_id = m["id"]
        x, y = me["pos"][0], me["pos"][1]
        ex, ey = m["pos"][0], m["pos"][1]
        d = math.hypot(ex - x, ey - y)
        theirs = set(m.get("weapons") or [])
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        goal = None

        all_in = getattr(self, "all_in", False)
        if all_in and m["kind"] == "mech":
            g = self.allin_retreat(me, m, t, t_start, hp_frac)
            if g is not None:
                self.fight_goal, self.fight_mode = g, "RETREAT"
                return

        if FIELD:
            g = self.choose_field(me, t, t_start, self._obs_mechs)
            if g is not None:
                self.fight_goal, self.fight_mode = g, "FIELD"
                return

        if FINISH_RUSH and getattr(self, "situation", "") == "squad" and m["kind"] == "mech" and \
                hp_frac > FINISH_RUSH_MIN and m.get("hp") and m.get("max_hp") and \
                m["hp"] < FINISH_RUSH_HP * m["max_hp"] and \
                sum(1 for o in self._obs_mechs if o["kind"] == "mech" and
                    math.hypot(o["pos"][0] - x, o["pos"][1] - y) < 150.0) <= 2:
            # nearly dead: get in close and finish it (a kill drops its cubes + 2, and it can't heal back up)
            a = math.atan2(y - ey, x - ex)
            r = min(FINISH_RUSH_R.get(self.style, 20.0), d)
            px, py = self.clamp_safe(ex + r * math.cos(a), ey + r * math.sin(a))
            self.fight_goal, self.fight_mode = self.nav.center(self.nav.nearest_main(px, py)), "RUSH"
            return

        # pick the fight: a lone enemy, nobody else on us, we're healthy -> close to our weapon's range
        aggro = EARLY_AGGRO and getattr(self, "phase", "") == "early"
        if EARLY_AGGRO:
            # early: pick off weaker enemies; after that, never go looking for fights (hide and heal instead)
            hunt_ok = aggro and hp_frac > AGGRO_HP and self.weaker(me, m)
        if SQUAD_HEAL and getattr(self, "situation", "") == "squad" and self.recovering:
            hunt_ok = False  # healing between hunters: let the next one come to us, don't go looking
        else:
            hunt_ok = hp_frac > HUNT_HP
        if HUNT and m["kind"] == "mech" and hunt_ok and not self.late_zone:
            n_visible = sum(1 for o in self._obs_mechs if o["kind"] == "mech")
            others_on_us = any(i != m["id"] and t - at < 4.0 for i, at in self.attackers.items())
            # anyone else seen/heard near us lately (squad reinforcements)? sounds near the target are its own
            for key, (et, qx, qy) in self.enemies.items():
                if key == m["id"] or t - et > 10.0 or math.hypot(qx - x, qy - y) > (AGGRO_CLEAR_R if aggro else 150.0):
                    continue
                if isinstance(key, tuple) and math.hypot(qx - ex, qy - ey) < 40.0:
                    continue
                others_on_us = True
                break
            pref = HUNT_RANGE.get(self.style, 70.0)
            if all_in:
                pref = self.allin_range(me, m)
            elif PLAYBOOK:  # close to the middle of the matchup's band
                lo, hi = band(our_role(me["weapons"]), m.get("weapons"))
                pref = max(15.0, (lo + hi) / 2.0)
            capturing = t - self.capturers.get(m["id"], -1e9) < 3.0  # about to be healed by a drop
            unaware = VANISH and self.is_unaware(m, t)  # lost us after a vanish, back to us: ambush it
            winning = not FIGHT_ODDS or self.fight_odds(me, m, t, at=pref) >= (UNAWARE_WIN_R if unaware else FIGHT_WIN_R)
            if n_visible == 1 and not others_on_us and not capturing and d > pref + 10.0 and winning:
                # low on ammo (or reloading): reload first, then go in
                g = next((w for w in me["weapons"] if w and w["kind"] == "gun"), None)
                if RELOAD_BEFORE_HUNT and g and self.style == "gun" and (
                        g.get("reload", 0) > 0 or g.get("ammo", 0) < g["stats"].get("mag", 12) * RELOAD_HUNT_FRAC):
                    self.want_reload = g.get("reload", 0) == 0
                    self.fight_goal, self.fight_mode = None, "RELOAD"
                    return
                a = math.atan2(y - ey, x - ex)
                px, py = self.clamp_safe(ex + pref * math.cos(a), ey + pref * math.sin(a))
                self.fight_goal, self.fight_mode = self.nav.center(self.nav.nearest_main(px, py)), "HUNT"
                return

        if FIGHT_ODDS and not all_in and m["kind"] == "mech" and t - self.last_dmg_t < 3.0:
            # being shot and losing the race (we'd die well before it does): leave now, while we still have HP
            hittable = self.can_hit(me, m)
            quick = hittable and (m.get("hp") or 1400.0) / self.our_dps(me, m, d, True) < 2.0
            lose_r = POWER_BIG_LOSE_R if getattr(self, "big", False) else FIGHT_LOSE_R
            if not quick and self.fight_odds(me, m, t) < lose_r:
                # only with a hiding spot close by: backing off across open ground just gets us shot longer
                eyes = [(o["pos"][0], o["pos"][1], o["pos"][2] + 8.0) for o in self._obs_mechs if o["kind"] == "mech"]
                c = self.nav.flank_cell(eyes, None, x, y, DISENGAGE_R, t_start + 0.010)
                if c >= 0:
                    self.fight_goal = self.nav.center(c)
                    self.fight_mode = "DISENGAGE"
                    return

        if ENGAGE and getattr(self, "situation", "") == "squad" and m["kind"] == "mech" and \
                not self.recovering and (all_in or (not self.late_zone and hp_frac > ENGAGE_MIN_HP)):
            g = self.engage_goal(me, m, t, d, theirs)
            if g is not None:
                self.fight_goal, self.fight_mode = g
                return

        if WEIGH_THREATS and self.active:
            def eye(o):
                return (o["pos"][0], o["pos"][1], o["pos"][2] + (3.0 if o["kind"] == "npc" else 8.0))
            under_fire = t - self.last_dmg_t < 3.0
            # low HP under fire: get out of everyone's sight and regenerate
            # ... unless the target dies in ~2 s: finish it instead
            hittable = self.can_hit(me, m)
            ttk = (m.get("hp") or 1400.0) / self.our_dps(me, m, d, hittable)
            hide_hp = POWER_BIG_HIDE_HP if getattr(self, "big", False) else HIDE_LOW_HP
            if HIDE_LOW_HP and not all_in and hp_frac < hide_hp and under_fire and not (hittable and ttk < 2.0):
                c = self.nav.flank_cell([eye(o) for o, _ in self.active], None, x, y, 45.0, t_start + 0.010)
                if c >= 0:
                    self.fight_goal, self.fight_mode = self.nav.center(c), "HIDE"
                    return
            # only foes actually shooting us count as "others" to hide from
            others = [(o, dps) for o, dps in self.active if o["id"] != m["id"] and dps > 0 and
                      (t - self.attackers.get(o["id"], -1e9) < 4.0 or t - self.npc_attackers.get(o["id"], -1e9) < 4.0)]
            if others:
                zc = m["pos"][2] + (1.75 if m["kind"] == "npc" else 4.5)
                c = self.nav.flank_cell([eye(o) for o, _ in others], (ex, ey, zc), x, y, 40.0, t_start + 0.010)
                if c >= 0:
                    self.fight_goal, self.fight_mode = self.nav.center(c), "FLANK"
                    return
                # no spot does both: if another foe hurts us more than our target does, hide from that one
                o, odps = max(others, key=lambda od: od[1])
                tdps = next((dp for f, dp in self.active if f["id"] == m["id"]), 0.0)
                if odps > tdps:
                    self.fight_goal = self.away_goal(me, o["pos"][0], o["pos"][1], 30.0, t_start)
                    self.fight_mode = "AVOID"
                    return
        self.fight_mode = "FIGHTMOVE"
        if all_in:
            self.fight_goal = None  # combat movement (combat.py) keeps our range every decision
            return
        if PLAYBOOK and m["kind"] == "mech":
            # too close for this matchup -> back off to the band (behind cover if one is near)
            lo, hi = band(our_role(me["weapons"]), theirs)
            if d < lo - 5.0:
                self.fight_goal = self.away_goal(me, ex, ey, min(40.0, lo - d + 10.0), t_start)
                self.fight_mode = "KEEPOUT"
            else:
                self.fight_goal = None
            return
        if self.style == "gun":
            # gun: keep mid range (leading works, lightning can't reach us)
            if d < 50 or ("lightning" in theirs and d < 65):
                goal = self.away_goal(me, ex, ey, 30.0, t_start)
        elif self.style == "laser":
            # laser: keep long range; lightning shorts our laser, so stay well out of its reach
            if d < 70 or ("lightning" in theirs and d < 90):
                goal = self.away_goal(me, ex, ey, 35.0, t_start)
        else:
            # lightning: ambush. Hold position and let them come within arc range;
            # close in on laser carriers (lightning shorts their laser) when healthy
            if "laser" in theirs and d < 80 and hp_frac > 0.5:
                goal = (ex, ey)
            elif "mortar" in theirs and d < 30:
                goal = self.away_goal(me, ex, ey, 25.0, t_start)  # mortar beats lightning up close
        self.fight_goal = goal

    @staticmethod
    def _gun_speed(me):
        for w in me["weapons"]:
            if w and w["kind"] == "gun":
                return w["stats"].get("speed", 252)
        return 252.0
