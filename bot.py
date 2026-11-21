"""Mech bot: loot early (cubes, crates, drones, weapons), then hold cover inside the next zone circle.
Fights back: faces attackers, leads shots, picks targets (attacker > low HP > back turned), and
escapes out of sight of attackers it can't hit (roof snipers, enemies behind walls)."""
import math
import time
import traceback
from collections import deque

import mechfight as mf
from nav import Nav, Search
from loot import Memory, pick_upgrade, weapon_take
from aim import AimLearner
from tactics import EnemyModel, Jinker, mortar_landing

# ---- switches (flip to compare submissions)
LOOT = True            # go for items at all
LOOT_UNTIL = 150.0     # match time after which we only loot items close by
LOOT_LATE_DIST = 40.0  # ... "close by" after LOOT_UNTIL
LOOT_MAX_DIST = 160.0
SAFE_AFTER = 6.0       # s without seeing an enemy mech / being hit by one before looting again
DANGER_R = 130.0       # skip items this close to where an enemy mech was seen recently
DANGER_S = 25.0        # ... "recently"
ENERGY_RESERVE = 25.0  # lightning won't spend below this (kept for boost/jet escapes)
ENERGY_RESERVE_LATE = 9.0  # late game: spend almost all of it
SWEEP = True           # sweep the torso while walking to spot more loot
FACE_ATTACKER = True   # turn toward where damage comes from when we can't see the shooter
LEAD = True            # lead gun shots at mechs
SMART_TARGET = True    # attacker > low HP > back turned > close; else nearest
ESCAPE_UNHITTABLE = True  # break line of sight from attackers we can't hit back
NO_HIT_S = 2.5         # ... "can't hit" also means: shooting it this long with no hit landing
LEARN_AIM = True       # learn per-enemy lead factor from where targets really went
HOLD_DODGY = True      # don't spend gun ammo on long shots at targets that change direction a lot
PLAYSTYLE = True       # move by loadout: gun keeps mid range, laser keeps long range, lightning ambushes
DRONE_FARM = True      # shoot back at drones that shoot us, treat them as loot
WEIGH_THREATS = True   # several attackers: fight the best trade, stand where the others can't see us
HIDE_LOW_HP = 0.30     # below this HP fraction, under fire: hide from every attacker (0 = off)
EXPLORE = True         # early, with nothing to loot: walk to unexplored areas to find loot
EXPLORE_UNTIL = 100.0  # match time
EXPLORE_BIN = 25.0     # m, size of the "have we looked here" bins
FIELD = False          # in fights, stand where (expected damage taken) - (expected damage dealt) is lowest
FIELD_H = 3.0          # ... over this many seconds
FIELD_KILL_W = 1.0     # ... weight of "fraction of an enemy we kill" vs "fraction of our HP we lose"
JINK = False           # step side to side (learned pattern) while a gun is shooting at us
DODGE = False          # sidestep lasers aimed at us while charging; leave mortar landing spots
SOUNDS = True          # face suspicious sounds, treat nearby movement as a threat, mark fights on the danger map
SOUND_FACE_S = 2.5     # how long to keep facing a sound
DROPS = True           # go for supply drops when it looks safe
DROP_MAX_DIST = 220.0  # m (x1.3 when HP is below half)
DROP_DANGER_R = 100.0  # skip a drop if an enemy was seen/heard this close to it in the last 20 s
CONTEST = True         # one mech capturing a drop, we reach the ring before it finishes and would win: go in
CONTEST_MIN_HP = 0.5   # ... only above this HP fraction
CONTEST_MAX_DIST = 80.0  # ... and only this close (less open ground to cross); sniping has no distance limit
PARK_EXPOSURE = True   # park in corners/alcoves (few open directions) and face the open side
EXPO_W = 6.0           # m of extra walking we accept per open direction avoided (of 12)
HUNT = True            # one enemy in sight, nobody else on us, healthy: close to our weapon's range and fight
HUNT_HP = 0.6          # ... "healthy"
HUNT_RANGE = {"lightning": 30.0, "laser": 110.0, "gun": 70.0}
SLICE_S = 0.020        # A* time per decision

# sound kind -> how much it says "an enemy mech is there" (x band multiplier). npc_gun/crate/drop/death: ignored
SOUND_PRIO = {"step": 3.0, "boost": 3.0, "jet": 3.0, "reload": 3.0, "laser_charge": 3.0,
              "lightning": 2.5, "gun": 2.0, "laser": 2.0, "mortar": 1.5, "explosion": 1.0}
BAND_MULT = {"near": 1.0, "mid": 0.6, "far": 0.25}
BAND_DIST = {"near": 35.0, "mid": 110.0, "far": 230.0}  # rough distance for each band
MOVE_SOUNDS = ("step", "boost", "jet", "reload", "laser_charge")


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Bot:
    name = "mech-fight"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.cfg = cfg
        self.reach = cfg["loot"]["pickup_radius"]
        self.size_per_cube = cfg["cubes"].get("size_per_cube", 0.05)
        try:
            self.nav = Nav(hello["map"]["dir"], cfg["mech"])
        except Exception:
            mf.log("nav build failed, walking straight:\n" + traceback.format_exc())
            self.nav = None
        self.mem = Memory(self.nav, cfg)
        self.goal = None        # (x, y) we are pathing to
        self.cover = None       # (x, y) cover spot in the next circle
        self.zone_key = None
        self.search = None
        self.path = None        # list of (x, y)
        self.pidx = 0
        self.hist = deque(maxlen=11)  # (t, x, y) over ~1 s
        self.unstick = 0
        self.stuck_n = 0
        self.bad = set()        # cover cells that failed
        self.threat_t = -1e9    # last time an enemy mech was seen / hit us
        self.enemies = {}       # id -> (t, x, y)       (for loot danger)
        self.epos = {}          # id -> (t, pos, vel)   last known 3D state
        self.attackers = {}     # id -> last time it damaged us
        self.att_bearing = {}   # id -> bearing of its latest hit on us
        self.dmg_bearing = (-1e9, 0.0)  # (t, bearing) of the latest mech damage
        self.hits = {}          # id -> last time we hit it
        self.fire_since = {}    # id -> when we started shooting at it
        self.cur = None         # current fight target id
        self.escape = None      # dict(src=id, goal=(x, y), t=start) while breaking line of sight
        self.escape_check_t = -1e9
        self.target = None      # loot item id
        self.target_since = 0.0
        self.retarget_t = -1e9
        self.aim = AimLearner()
        self.fight_goal = None  # (x, y) where the playstyle wants us during a fight
        self.fight_goal_t = -1e9
        self.fight_goal_id = None
        self.fight_mode = "FIGHTMOVE"
        self.last_dmg_t = -1e9
        self.style = "gun"
        self.seen_bins = set()  # explored (bx, by) bins
        self.explore_goal = None
        self.explore_t = -1e9
        self.npc_attackers = {}  # drone id -> last time it hit us
        self.sound_face = None  # (t heard, bearing, priority) of the most telling recent sound
        self.expo = {}          # cover cell -> (open directions, yaw to face)
        self.cover_face = None  # yaw to watch while parked at the current cover
        self.snd_i = 0
        self.drop_target = None
        self.drop_since = 0.0
        self.drop_check_t = -1e9
        self.drop_banned = set()
        self.drop_stage = {}    # drop id -> where to wait for it
        self.contest = None     # (goal, drop id) while contesting someone else's capture
        self.capturers = {}     # mech id -> last time it was capturing a drop (don't hunt into its full-HP payout)
        self.dmg_log = {}       # attacker id -> deque of (t, amount)
        self.active = []        # [(foe, its dps on us)] this decision
        npc = cfg.get("npc", {})
        self.npc_dps = npc.get("damage", 25) / max(0.1, npc.get("cooldown", 1.0))
        self.wcfg = cfg.get("weapons", {})
        self.emodel = EnemyModel(cfg)
        self.jinker = Jinker(hello.get("seed", 0))
        self.jink_hist = deque()
        self.gravity = cfg["mech"].get("gravity", 22)
        self.gun_dmg_tick = 0.0
        self.last_gun_dmg_t = -1e9

    # ---------- goal selection
    def choose_cover(self, mx, my, zone):
        nav = self.nav
        nc, nr = zone["next_center"], zone["next_radius"]
        R = nr - max(8.0, 0.25 * nr)
        if nav is None:
            return (nc[0], nc[1])
        if R < 15:
            return nav.center(nav.nearest_main(nc[0], nc[1]))
        R2 = R * R
        cands = []
        for c, x, y in nav.cover:
            dx, dy = x - nc[0], y - nc[1]
            d2 = dx * dx + dy * dy
            if d2 > R2 or c in self.bad:
                continue
            cands.append((math.hypot(x - mx, y - my) + 0.6 * math.sqrt(d2), c))
        if not cands:
            self.cover_face = None
            return nav.center(nav.nearest_main(nc[0], nc[1]))
        cands.sort()
        best, bs, bface = cands[0][1], 1e18, None
        if PARK_EXPOSURE:
            # among the closest candidates, prefer corners/alcoves: few directions an enemy can see us from
            for s, c in cands[:60]:
                e = self.expo.get(c)
                if e is None:
                    e = self.expo[c] = nav.exposure(c)
                n_open, face = e
                s += EXPO_W * n_open + (10.0 if n_open == 0 else 0.0)  # fully enclosed: can't see anyone coming
                if s < bs:
                    best, bs, bface = c, s, face
        self.cover_face = bface
        return nav.center(best)

    def set_goal(self, xy, x, y):
        if self.goal is not None and math.hypot(xy[0] - self.goal[0], xy[1] - self.goal[1]) < 3.0:
            return
        self.goal = xy
        self.path = None
        self.replan(x, y)

    def replan(self, mx, my):
        if self.nav is None or self.goal is None:
            self.search = None
            return
        self.search = Search(self.nav, self.nav.cell_of(mx, my), self.nav.cell_of(*self.goal))

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
                if d["id"] in self.drop_banned or d.get("holder") not in (None, me["id"]):
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
                out[i] = dist < st.get("range", 280) and w.get("cooldown", 0) <= 0.05 and charge < full \
                    and (err < tol * 3 or charge > 0)
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
            if m["id"] == self.cur:
                v *= 1.15  # don't flip-flop
            if v > bs:
                best, bs = m, v
        return best

    def pick_target(self, me, mechs, t):
        x, y = me["pos"][0], me["pos"][1]
        if WEIGH_THREATS:
            return self.pick_target_weighted(me, mechs, t)
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

    # ---------- escape from attackers we can't hit
    def update_escape(self, me, mechs, t, t_start):
        if not ESCAPE_UNHITTABLE or self.nav is None:
            self.escape = None
            return
        x, y, z = me["pos"]
        if self.escape is not None:
            src = self.escape["src"]
            if t - self.attackers.get(src, -1e9) > 4.0 or t - self.escape["t"] > 20.0:
                self.escape = None  # it stopped hurting us (or we gave it long enough)
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
        c = nav.hide_cell(ex, ey, nav.ground_z(ex, ey) + 8.0, x, y, 30.0, t_start + 0.008)
        if c >= 0:
            cx, cy = nav.center(c)
            if math.hypot(cx - ex, cy - ey) > math.hypot(x - ex, y - ey) - 5:  # not toward it
                return (cx, cy)
        a = math.atan2(y - ey, x - ex)
        px, py = self.clamp_safe(x + dist * math.cos(a), y + dist * math.sin(a))
        return nav.center(nav.nearest_main(px, py))

    def clamp_safe(self, px, py):
        """Pull a point back inside the nav's safe circle (so running away never runs into the zone)."""
        s = self.nav.safe if self.nav is not None else None
        if s is None:
            return px, py
        sx, sy, sr = s
        d = math.hypot(px - sx, py - sy)
        if d <= sr - 2.0 or d < 1e-6:
            return px, py
        f = (sr - 2.0) / d
        return sx + (px - sx) * f, sy + (py - sy) * f

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

    def update_fight_goal(self, me, m, t, t_start):
        """Where to stand against target m, by our loadout. None = keep the normal goal (cover)."""
        if not PLAYSTYLE or self.nav is None or m is None:
            self.fight_goal = None
            return
        if t - self.fight_goal_t < 1.0 and m["id"] == self.fight_goal_id:
            return  # same target: refresh once a second; new target: right away
        self.fight_goal_t = t
        self.fight_goal_id = m["id"]
        x, y = me["pos"][0], me["pos"][1]
        ex, ey = m["pos"][0], m["pos"][1]
        d = math.hypot(ex - x, ey - y)
        theirs = set(m.get("weapons") or [])
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        goal = None

        if FIELD:
            g = self.choose_field(me, t, t_start, self._obs_mechs)
            if g is not None:
                self.fight_goal, self.fight_mode = g, "FIELD"
                return

        # pick the fight: a lone enemy, nobody else on us, we're healthy -> close to our weapon's range
        if HUNT and m["kind"] == "mech" and hp_frac > HUNT_HP:
            n_visible = sum(1 for o in self._obs_mechs if o["kind"] == "mech")
            others_on_us = any(i != m["id"] and t - at < 4.0 for i, at in self.attackers.items())
            # anyone else seen/heard near us lately (squad reinforcements)? sounds near the target are its own
            for key, (et, qx, qy) in self.enemies.items():
                if key == m["id"] or t - et > 10.0 or math.hypot(qx - x, qy - y) > 150.0:
                    continue
                if isinstance(key, tuple) and math.hypot(qx - ex, qy - ey) < 40.0:
                    continue
                others_on_us = True
                break
            pref = HUNT_RANGE.get(self.style, 70.0)
            capturing = t - self.capturers.get(m["id"], -1e9) < 3.0  # about to be healed by a drop
            if n_visible == 1 and not others_on_us and not capturing and d > pref + 10.0:
                a = math.atan2(y - ey, x - ex)
                px, py = self.clamp_safe(ex + pref * math.cos(a), ey + pref * math.sin(a))
                self.fight_goal, self.fight_mode = self.nav.center(self.nav.nearest_main(px, py)), "HUNT"
                return

        if WEIGH_THREATS and self.active:
            def eye(o):
                return (o["pos"][0], o["pos"][1], o["pos"][2] + (3.0 if o["kind"] == "npc" else 8.0))
            under_fire = t - self.last_dmg_t < 3.0
            # low HP under fire: get out of everyone's sight and regenerate
            # ... unless the target dies in ~2 s: finish it instead
            hittable = self.can_hit(me, m)
            ttk = (m.get("hp") or 1400.0) / self.our_dps(me, m, d, hittable)
            if HIDE_LOW_HP and hp_frac < HIDE_LOW_HP and under_fire and not (hittable and ttk < 2.0):
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

    # ---------- main
    def act(self, obs: mf.Observation) -> mf.Action:
        t_start = time.perf_counter()
        me = obs["self"]
        x, y, z = me["pos"]
        t = obs["t"]
        zone = obs["zone"]
        weapons = me["weapons"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs["offer"]:
            act["pick"] = pick_upgrade(obs["offer"])

        # ---- world model
        self.mem.update(obs)
        self.emodel.update(obs)
        self._obs_mechs = obs["mechs"]
        self.gun_dmg_tick = 0.0
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        for m in mechs:
            self.enemies[m["id"]] = (t, m["pos"][0], m["pos"][1])
            self.epos[m["id"]] = (t, m["pos"], m["vel"])
        if mechs:
            self.threat_t = t
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None and e["by"] < 100:
                self.threat_t = t
                self.attackers[e["by"]] = t
                if e.get("bearing") is not None:
                    self.dmg_bearing = (t, e["bearing"])
                    self.att_bearing[e["by"]] = e["bearing"]
            elif e["type"] == "hit":
                self.hits[e["target"]] = t
            if e["type"] == "damaged" and e.get("by") is not None and e["by"] >= 100:
                self.npc_attackers[e["by"]] = t
            if e["type"] == "damaged" and e.get("weapon") == "gun":
                self.gun_dmg_tick += e.get("amount", 0)
                self.last_gun_dmg_t = t
            if e["type"] == "damaged" and e.get("by") is not None:
                self.last_dmg_t = t
                self.dmg_log.setdefault(e["by"], deque(maxlen=40)).append((t, e.get("amount", 0)))
        self._t = t
        if self.nav is not None:
            # hiding spots must stay out of the zone: the current circle, or the next one once a shrink is near
            if t >= zone["shrink_start"] - 10:
                sc, sr = zone["next_center"], zone["next_radius"]
            else:
                sc, sr = zone["center"], zone["radius"]
            self.nav.safe = (sc[0], sc[1], max(sr - 4.0, 5.0))
        self.aim.observe(t, obs["mechs"])
        self.style = self.loadout_style(weapons)
        self.mark_seen(x, y)
        for dr in obs.get("drops", []):
            if dr.get("holder") is not None and dr["holder"] != me["id"]:
                self.capturers[dr["holder"]] = t
        if SOUNDS:
            self.hear(obs, x, y, t)
        fighting = t - self.threat_t < 3.0
        late = obs["alive"] <= 5 or zone["next_radius"] <= 70
        self.update_escape(me, mechs, t, t_start)

        # ---- cover goal for the current circle
        key = (round(zone["next_center"][0], 1), round(zone["next_center"][1], 1), round(zone["next_radius"], 1))
        if key != self.zone_key:
            self.zone_key = key
            self.cover = self.choose_cover(x, y, zone)

        # ---- loot target
        target = None
        if LOOT and self.escape is None and t - self.threat_t > SAFE_AFTER:
            if self.target is not None and (self.target not in self.mem.items or t - self.target_since > 40):
                if self.target in self.mem.items:
                    self.mem.banned.add(self.target)  # took too long
                self.target = None
            # a drone shooting at us: deal with it first (it's worth 2 cubes anyway)
            if DRONE_FARM:
                for nid, nt in self.npc_attackers.items():
                    if t - nt < 3.0 and nid in self.mem.items and nid not in self.mem.banned:
                        if self.target != nid:
                            self.target, self.target_since = nid, t
                        self.retarget_t = t
                        break
            if self.target is None or t - self.retarget_t > 1.0:
                self.retarget_t = t
                md = LOOT_MAX_DIST if t < LOOT_UNTIL else LOOT_LATE_DIST
                tid, _ = self.mem.best_target(me, zone, weapons, late, md, lambda ix, iy: self.danger(ix, iy, t))
                if tid != self.target:
                    self.target, self.target_since = tid, t
            if self.target is not None:
                target = self.mem.items.get(self.target)
        else:
            self.target = None

        # ---- what to walk to / stop near
        stop_r = 4.0
        shoot = None  # (pos, vel, zoff, size) of a crate/drone to shoot
        # foes: visible mechs, plus visible drones that are shooting us (so a drone isn't ignored mid-fight)
        foes = mechs
        if mechs and WEIGH_THREATS:
            foes = mechs + [m for m in obs["mechs"] if m["kind"] == "npc"
                            and t - self.npc_attackers.get(m["id"], -1e9) < 4.0]
        fight_m = self.pick_target(me, foes, t) if mechs else None
        contesting = DROPS and CONTEST and self.nav is not None and self.escape is None and self._contest(obs, me, t)
        if contesting and self.contest[3] is not None:
            # sniping/contesting a capture: the capturer is the target, whoever else scores higher
            forced = next((m for m in mechs if m["id"] == self.contest[3]), None)
            if forced is not None:
                fight_m = forced
        if fight_m is not None and self.escape is None:
            self.update_fight_goal(me, fight_m, t, t_start)
        elif not mechs:
            self.fight_goal = None
            self.fight_goal_id = None
        safe = t - self.threat_t > SAFE_AFTER
        if self.escape is not None:
            self.set_goal(self.escape["goal"], x, y)
            dgoal = math.hypot(self.escape["goal"][0] - x, self.escape["goal"][1] - y)
            stop_r = 1.5
            mode = "ESCAPE"
        elif contesting:
            g = self.contest[0]
            self.set_goal(g, x, y)
            dgoal = math.hypot(g[0] - x, g[1] - y)
            mode = self.contest[2]
            stop_r = 3.5 if mode == "SNIPE" else 2.5
        elif self.fight_goal is not None:
            self.set_goal(self.fight_goal, x, y)
            dgoal = math.hypot(self.fight_goal[0] - x, self.fight_goal[1] - y)
            stop_r = 3.0
            mode = self.fight_mode
        elif DROPS and self.nav is not None and t - self.threat_t > 3.0 and self._drop(obs, me, t):
            g, stop_r, mode = self._drop_now
            self.set_goal(g, x, y)
            dgoal = math.hypot(g[0] - x, g[1] - y)
        elif target is None and EXPLORE and safe and t < EXPLORE_UNTIL and self.nav is not None and \
                self._explore(x, y, zone, t):
            gx, gy = self.explore_goal
            self.set_goal(self.explore_goal, x, y)
            dgoal = math.hypot(gx - x, gy - y)
            stop_r = 6.0
            mode = "explore"
        elif target is not None:
            k = target["kind"]
            tpos = (target["x"], target["y"])
            dist_t = math.hypot(tpos[0] - x, tpos[1] - y)
            visible = t - target["seen"] < 0.15
            if k == "cube":
                stop_r = 1.5
            elif k == "pickup":
                stop_r = 2.0
            elif k == "crate":
                stop_r = 25.0 if visible else 8.0
                if visible and dist_t < 60:
                    shoot = ([target["x"], target["y"], target["z"]], None, 1.2, 1.4)
            elif k == "npc":
                stop_r = 60.0 if visible else 20.0
                if visible and dist_t < 120:
                    npc = next((m for m in obs["mechs"] if m["id"] == self.target), None)
                    if npc:
                        shoot = (npc["pos"], npc["vel"], 1.75, 1.8)
            cell = self.mem.reachable_cell(target)
            gx, gy = self.nav.center(cell) if (self.nav and cell >= 0) else tpos
            # walk to the item itself when it's the thing we pick up
            if k in ("cube", "pickup") and dist_t < 8:
                gx, gy = tpos
            self.set_goal((gx, gy), x, y)
            dgoal = dist_t
            mode = k
        else:
            self.set_goal(self.cover, x, y)
            dgoal = math.hypot(self.cover[0] - x, self.cover[1] - y)
            mode = "cover"

        # ---- path search slice
        if self.search is not None and self.search.step(t_start + SLICE_S):
            s = self.search
            self.search = None
            self.path = [self.nav.center(c) for c in s.path]
            self.pidx = 0
            if not s.reached:
                mf.log(f"t={t:.1f} goal unreachable, partial path {len(s.path)}")
                if self.escape is not None:
                    pass  # partial path still moves us away
                elif target is not None:
                    self.mem.banned.add(self.target)
                    self.target = None
                else:
                    self.bad.add(s.goal)
                    self.zone_key = None  # pick another cover next tick

        # ---- steering
        gx, gy = self.goal
        arrived = dgoal < stop_r
        if self.path:
            p = self.path
            lo, hi = self.pidx, min(len(p), self.pidx + 20)
            bi, bd = lo, 1e18
            for k2 in range(lo, hi):
                d = (p[k2][0] - x) ** 2 + (p[k2][1] - y) ** 2
                if d < bd:
                    bi, bd = k2, d
            self.pidx = bi
            if bd > 12.0 ** 2 and self.search is None:  # fell off the path
                self.replan(x, y)
            tx, ty = p[min(len(p) - 1, bi + 3)]
            if bi >= len(p) - 4:
                tx, ty = gx, gy
        else:
            tx, ty = gx, gy
        move = [0.0, 0.0]
        if not arrived:
            dx, dy = tx - x, ty - y
            d = math.hypot(dx, dy)
            if d > 0.3:
                move = [dx / d, dy / d]
        moving = move != [0.0, 0.0]
        jinking, extra_boost = self.evade(obs, me, move, t)
        if jinking:
            move = jinking

        # ---- stuck detection
        self.hist.append((t, x, y))
        jump = False
        boost = not me["in_zone"] or (self.escape is not None and me["energy"] > 30) or extra_boost or \
            (mode == "CONTEST" and dgoal > 6.0 and me["energy"] > 40)
        if self.unstick > 0:
            self.unstick -= 1
            boost = True
            jump = me["grounded"]
        elif moving and not jinking and len(self.hist) == self.hist.maxlen:
            t0, x0, y0 = self.hist[0]
            moved = math.hypot(x - x0, y - y0)
            if t - t0 >= 0.9 and moved < 1.5:
                self.stuck_n += 1
                self.hist.clear()
                if self.stuck_n >= 3 and self.nav is not None:
                    self.nav.penalize(tx, ty)
                    self.replan(x, y)
                    self.stuck_n = 0
                else:
                    self.unstick = 8
                    jump = me["grounded"]
                    boost = True
            elif moved > 6.0:
                self.stuck_n = 0

        # ---- aim: look where we go, sweeping to spot loot; parked: face the zone center
        if moving:
            yaw = math.atan2(move[1], move[0])
            if SWEEP and not fighting:
                yaw += 0.9 * math.sin(t * 1.3)
        elif mode == "cover" and self.cover_face is not None:
            yaw = self.cover_face  # parked in a corner: watch the open side, that's where they come from
            if SWEEP and not fighting:
                yaw += 0.7 * math.sin(t * 0.5)
        else:
            yaw = math.atan2(zone["center"][1] - y, zone["center"][0] - x)
            if SWEEP and not fighting:
                yaw += 1.0 * math.sin(t * 0.5)
        pitch = 0.0
        # hit by someone we can't see: face them (front armor) and look for them
        if FACE_ATTACKER and not mechs and t - self.dmg_bearing[0] < 3.0:
            yaw = self.dmg_bearing[1]
            pitch = 0.15  # roofs / jets are above us more often than below
        # heard something telling (footsteps, a laser charging, gunfire not far): look that way first
        elif SOUNDS and not mechs and self.sound_face is not None and t - self.sound_face[0] < SOUND_FACE_S \
                and self.sound_face[2] > 0.6:
            yaw = self.sound_face[1]
            pitch = 0.05
        act.update(move=move, boost=boost, jump=jump, aim=[yaw, pitch])

        # ---- take weapons in reach (swap rules in loot.weapon_take)
        for p in obs["pickups"]:
            if p["id"] in self.mem.banned or math.dist(p["pos"][:2], (x, y)) >= self.reach:
                continue
            d = weapon_take(p["kind"], weapons, late, fighting)
            if d is not None:
                act["take"] = p["id"]
                if d != "empty":
                    act["drop"] = d
                break

        # ---- shoot crates / drones we're looting
        if shoot is not None and not mechs:
            aim, h = self.aim_at(me, shoot[0], shoot[1], shoot[2], self._gun_speed(me))
            act["aim"] = aim
            act["fire"] = self.fire(me, h, aim, shoot[3], False)

        # ---- fight
        if mechs:
            m = fight_m
            if m["id"] != self.cur:
                self.cur = m["id"]
            for i in list(self.fire_since):
                if i != m["id"]:
                    del self.fire_since[i]
            self.fire_since.setdefault(m["id"], t)
            is_mech = m["kind"] == "mech"
            scale = 1.0 + self.size_per_cube * m.get("cubes", 0) if is_mech else 1.0
            size = 2.5 * scale if is_mech else 1.8
            laser_charging = any(w and w["kind"] == "laser" and w.get("charge", 0) > 0 for w in weapons)
            speed = 0.0 if (laser_charging or not LEAD) else self._gun_speed(me)  # laser is hitscan
            zoff = self.hit_zoff(me, m)
            k = self.aim.lead(m["id"]) if (LEARN_AIM and is_mech) else 1.0
            aim, h = self.aim_at(me, m["pos"], m["vel"], zoff if zoff is not None else 4.5 * scale, speed, k)
            act["aim"] = aim
            if zoff is not None:
                tt = h / speed if speed else 0.0
                gun_ok = True
                if HOLD_DODGY and is_mech and speed and h > 60:
                    # long shot: only if it can't stray further than ~1.5 body radii during the flight
                    gun_ok = self.aim.surprise(m["id"], tt) < 2.5 * scale * 1.5
                fire = self.fire(me, h, aim, size, is_mech, late, gun_ok)
                act["fire"] = fire
                if LEARN_AIM and is_mech and speed and any(f and w and w["kind"] == "gun" for f, w in zip(fire, weapons)):
                    self.aim.record(t, m, tt)
            e = m["pos"]
            mf.draw.line([x, y, z + 7], [e[0], e[1], e[2] + 4.5], "#ff5040")
        else:
            self.cur = None
            self.fire_since.clear()

        # ---- reload when calm
        g = weapons[0] if weapons[0] and weapons[0]["kind"] == "gun" else None
        if g and not mechs and shoot is None and g.get("reload", 0) == 0 and g.get("ammo", 0) < g["stats"].get("mag", 12) / 2:
            act["reload"] = True

        # ---- debug
        if self.path:
            for k2 in range(self.pidx, min(len(self.path), self.pidx + 30), 3):
                px, py = self.path[k2]
                mf.draw.point([px, py, z + 1], "#40ff80", 3)
        mf.draw.point([gx, gy, z + 1], "#40c0ff", 8)
        for it in list(self.mem.items.values())[:20]:
            mf.draw.point([it["x"], it["y"], it["z"] + 1], "#ffd060", 4)
        what = f"{mode} {self.style} k{self.aim.gk:.2f}"
        mf.draw.label(f"hp {me['hp']:.0f} {what} d{dgoal:.0f} cubes {me['cubes']:.0f}"
                      f"{' tgt %d' % self.cur if self.cur is not None else ''}"
                      f"{' S' if self.unstick else ''}{' P' if self.search else ''}")
        return act

    @staticmethod
    def _gun_speed(me):
        for w in me["weapons"]:
            if w and w["kind"] == "gun":
                return w["stats"].get("speed", 252)
        return 252.0


if __name__ == "__main__":
    mf.run(Bot)
