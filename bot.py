"""Mech bot entry point: the per-decision loop that ties the parts together.
Loot early (cubes, crates, drones, weapons), then hold cover inside the next zone circle; fight back, take
supply drops, hunt lone enemies. The parts live in movement / senses / drops / fight / evade.py,
all switches in config.py."""
import math
import time
import traceback
from collections import deque

import mechfight as mf
from nav import Nav
from loot import Memory, pick_upgrade, weapon_take
from aim import AimLearner
from tactics import EnemyModel, Jinker
from config import *  # noqa: F401,F403
from util import wrap  # noqa: F401
from movement import MoveMixin
from senses import SensesMixin
from drops import DropsMixin
from fight import FightMixin
from evade import EvadeMixin
from artillery import ArtilleryMixin
from stealth import StealthMixin
from combat import CombatMixin
from scan import ScanMixin


class Bot(MoveMixin, SensesMixin, DropsMixin, FightMixin, EvadeMixin, ArtilleryMixin, StealthMixin, CombatMixin, ScanMixin):
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
        self.laser_hits = {}    # id -> last time it hit us with a laser (LASER_HIDE)
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
        self.late_zone = False  # next circle <= LATE_RADIUS
        self.finish = None      # [enemy id, keep-chasing-until t, last pos] for a fleeing low-HP enemy
        self.finish_m = None
        self.finish_now = None
        self.want_reload = False  # set by hunting: reload before going in
        self.recovering = False   # healing up after a fight (see RECOVER / PHASES)
        self.phase = "early"
        self.open_here = 0        # open directions (of 12) at our current spot, refreshed now and then
        self.open_t = -1e9
        self.mortar_commit = (-1e9, None)  # (until t, target id): keep the torso on the mortar solution
        self.spawn_yaw = None
        self.snd_i = 0
        self.drop_target = None
        self.drop_since = 0.0
        self.drop_check_t = -1e9
        self.drop_banned = set()
        self.drop_stage = {}    # drop id -> where to wait for it
        self.drop_watch = {}    # drop id -> where to wait out a fight at it (DROP_WAIT_CALM)
        self.situation = "squad"  # "squad" (house bots hunting only us) until other mechs die by others' hands: "ffa"
        self.our_kills = 0
        self.odd_deaths_t = None  # when more mechs had died than we killed (confirmed after SITUATION_CONFIRM_S)
        self.wait_calm = False
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
            act["pick"] = pick_upgrade(obs["offer"], me["weapons"], SQUAD_ALL_IN and self.situation == "squad",
                                       me.get("cubes", 0))

        # ---- world model
        self.mem.update(obs)
        self.emodel.update(obs)
        self._obs_mechs = obs["mechs"]
        self.gun_dmg_tick = 0.0
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        for m in mechs:
            self.enemies[m["id"]] = (t, m["pos"][0], m["pos"][1])
            self.epos[m["id"]] = (t, m["pos"], m["vel"])
        prev_threat_t = self.threat_t
        if mechs:
            self.threat_t = t
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None and e["by"] < 100:
                self.threat_t = t
                self.attackers[e["by"]] = t
                if e.get("weapon") == "laser":
                    self.laser_hits[e["by"]] = t
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
        self.read_situation(obs, t)
        self.all_in = SQUAD_ALL_IN and self.situation == "squad"
        self.gearing = GEAR_FIRST and self.all_in and t < GEAR_UNTIL
        if self.gearing and mechs:
            # gear first: mechs that haven't hurt us, carry no laser close by and aren't on top of us are left alone
            calm = [m for m in mechs if self.gear_harmless(me, m)]
            if calm:
                mechs = [m for m in mechs if m not in calm]
                if not mechs and self.threat_t == t and t - self.last_dmg_t > 0.05:
                    self.threat_t = prev_threat_t  # only harmless ones in sight: keep looting
        if KILL_PILE:
            for e in obs["events"]:
                if e["type"] == "kill" and e.get("target") in self.epos:
                    # its cubes (+2) and weapons drop where it died: a loot target right away, at the exact spot
                    _, kp, _ = self.epos[e["target"]]
                    self.mem.items[("kill", e["target"])] = {"kind": "pile", "x": kp[0], "y": kp[1], "z": kp[2],
                                                             "seen": t, "cell": None, "ok": None}
                    # it's dead: no longer a danger there (loot), nor a target for blind mortar shells
                    self.enemies.pop(e["target"], None)
                    self.epos.pop(e["target"], None)
                    self.emodel.pos.pop(e["target"], None)
        self.big = POWER and me.get("cubes", 0) >= POWER_BIG_CUBES  # bigger mech, easier to hit: more careful
        if self.big and not getattr(self, "_was_big", False):
            self.zone_key = None  # just got big: re-pick a more enclosed cover spot now
        self._was_big = self.big
        # a visible enemy with lightning within 50 m: its arc would cancel a laser charge (playbook)
        self.lit_near = any(o["kind"] == "mech" and "lightning" in (o.get("weapons") or []) and
                            math.hypot(o["pos"][0] - x, o["pos"][1] - y) < 50.0 for o in obs["mechs"])
        self.mark_seen(x, y)
        for dr in obs.get("drops", []):
            if dr.get("holder") is not None and dr["holder"] != me["id"]:
                self.capturers[dr["holder"]] = t
        if SOUNDS:
            self.hear(obs, x, y, t)
        fighting = t - self.threat_t < 3.0
        self._me_xy = (x, y)
        # recovery: after a fight, below RECOVER_HP -> no shooting things (it resets the 5 s regen timer),
        # no exploring/hunting; pick up only what needs no shooting, close by; hold cover until healed
        hp_frac = me["hp"] / max(1.0, me["max_hp"])
        late = obs["alive"] <= 5 or zone["next_radius"] <= 70
        # game phase: loot hard early (cubes/weapons win later fights), conserve HP late (it decides the end)
        if late:
            self.phase = "late"
        elif obs["alive"] >= PHASE_EARLY_ALIVE and zone["next_radius"] >= PHASE_EARLY_RADIUS:
            self.phase = "early"
        else:
            self.phase = "mid"
        heal_lo, heal_hi = (RECOVER_HP, RECOVER_UNTIL) if RECOVER else (None, None)
        if PHASES:
            group_weapon = any(w and w["kind"] in ("lightning", "mortar") for w in weapons)
            heal_lo, heal_hi = {"early": (None, None),
                                "mid": (AGGRO_MID_HEAL if EARLY_AGGRO else
                                        (PHASE_MID_HEAL, 0.9) if group_weapon else (None, None)),
                                "late": (PHASE_LATE_HEAL, 1.0)}[self.phase]
        if SQUAD_HEAL and self.situation == "squad":
            heal_lo, heal_hi = SQUAD_HEAL[self.phase]  # full HP before the next hunter comes
        if self.all_in:
            heal_lo, heal_hi = None, None  # all in: no stopping to heal (the short retreat rule covers danger)
            self.recovering = False
        if heal_lo is not None and not self.recovering and hp_frac < heal_lo and t - self.threat_t > 3.0:
            self.recovering = True
        elif self.recovering and (heal_hi is None or hp_frac >= heal_hi):
            self.recovering = False
        self.late_zone = LATE_GAME and zone["next_radius"] <= LATE_RADIUS
        if self.all_in:
            self.escape = None  # all in: no running from lasers / attackers we can't hit - pursue them instead
        else:
            self.update_escape(me, mechs, t, t_start)

        # ---- cover goal for the current circle
        key = (round(zone["next_center"][0], 1), round(zone["next_center"][1], 1), round(zone["next_radius"], 1))
        if key != self.zone_key:
            self.zone_key = key
            self.cover = self.choose_cover(x, y, zone)

        # ---- loot target
        target = None
        if LOOT and self.escape is None and t - self.threat_t > (ALLIN_SAFE_S if self.all_in else SAFE_AFTER):
            if self.target is not None and (self.target not in self.mem.items or t - self.target_since > LOOT_GIVEUP_S):
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
                if PHASES:
                    md = {"early": LOOT_MAX_DIST, "mid": PHASE_MID_LOOT, "late": LOOT_LATE_DIST}[self.phase]
                skip = ()
                if self.recovering:
                    md, skip = min(md, RECOVER_LOOT_DIST), ("crate", "npc")
                if self.all_in:
                    md = ALLIN_LOOT_R
                    skip = ("npc",) if me["hp"] < ALLIN_DRONE_HP * me["max_hp"] else ()
                tid, _ = self.mem.best_target(me, zone, weapons, late, md, lambda ix, iy: self.danger(ix, iy, t), skip,
                                              phase=self.phase,
                                              laser_seek=(GEAR_LASER_SEEK_R if self.gearing else LASER_SEEK_R)
                                              if (LASER_SEEK and self.all_in) else 0.0,
                                              weapon_r=GEAR_WEAPON_R if self.gearing else 0.0,
                                              drone_v=ALLIN_DRONE_V if self.all_in else 0.0)
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
        contesting = DROPS and CONTEST and not (self.all_in and ALLIN_NO_DROPS) and self.nav is not None and self.escape is None and self._contest(obs, me, t)
        if contesting and self.contest[3] is not None:
            # sniping/contesting a capture: the capturer is the target, whoever else scores higher
            forced = next((m for m in mechs if m["id"] == self.contest[3]), None)
            if forced is not None:
                fight_m = forced
        finishing = FINISH and not (self.all_in and ALLIN_STRIP) and self.nav is not None and self.escape is None and not contesting and             self._finish(me, mechs, t)
        if finishing and self.finish_m is not None:
            fight_m = self.finish_m  # the fleeing low-HP one is the target
        if fight_m is not None and self.escape is None:
            self.update_fight_goal(me, fight_m, t, t_start)
        elif not mechs:
            self.fight_goal = None
            self.fight_goal_id = None
        # surrounded: vanish (break every line of sight, go silent, heal, see whether they lose us)
        vanish_goal = self.update_vanish(me, obs["mechs"], fight_m, t, t_start) if (self.escape is None and not self.all_in) else None
        hidden = vanish_goal is not None and self.vanish_hidden()
        safe = t - self.threat_t > SAFE_AFTER
        zone_goal = self.zone_must(me, zone, t) if (ZONE_FIRST and self.nav is not None) else None
        if self.escape is not None:
            self.set_goal(self.escape["goal"], x, y)
            dgoal = math.hypot(self.escape["goal"][0] - x, self.escape["goal"][1] - y)
            stop_r = 1.5
            mode = "ESCAPE"
        elif zone_goal is not None:
            # outside the circle, or it would catch us: get in now, whatever the fight
            self.set_goal(zone_goal, x, y)
            dgoal = math.hypot(zone_goal[0] - x, zone_goal[1] - y)
            stop_r = 3.0
            mode = "ZONE"
        elif vanish_goal is not None:
            self.set_goal(vanish_goal, x, y)
            dgoal = math.hypot(vanish_goal[0] - x, vanish_goal[1] - y)
            stop_r = 1.5
            mode = "HIDDEN" if hidden else "VANISH"
        elif contesting:
            g = self.contest[0]
            self.set_goal(g, x, y)
            dgoal = math.hypot(g[0] - x, g[1] - y)
            mode = self.contest[2]
            stop_r = 3.5 if mode == "SNIPE" else 2.5
        elif finishing:
            g = self.finish_now
            self.set_goal(g, x, y)
            dgoal = math.hypot(g[0] - x, g[1] - y)
            stop_r = 2.0
            mode = "FINISH"
        elif FIGHT_CUBES and self.all_in and mechs and self.fight_mode not in ("RETREAT", "RUSH") and \
                self.fight_cube(me) is not None:
            g = self.fight_cube(me)  # all in: a cube close by mid-fight is +4% HP and damage right now
            self.set_goal(g, x, y)
            dgoal = math.hypot(g[0] - x, g[1] - y)
            stop_r = 1.0
            mode = "FIGHTCUBE"
        elif self.fight_goal is not None:
            self.set_goal(self.fight_goal, x, y)
            dgoal = math.hypot(self.fight_goal[0] - x, self.fight_goal[1] - y)
            stop_r = 3.0
            mode = self.fight_mode
        elif DROPS and not (self.all_in and ALLIN_NO_DROPS) and self.nav is not None and (t - self.threat_t > 3.0 or (
                self.wait_calm and self.drop_target is not None and t - self.last_dmg_t > 3.0)) and \
                self._drop(obs, me, t):
            # (already going for a drop: enemies in sight don't stop it, only being hit does)
            g, stop_r, mode = self._drop_now
            self.set_goal(g, x, y)
            dgoal = math.hypot(g[0] - x, g[1] - y)
        elif target is None and EXPLORE and not (self.all_in and ALLIN_NO_EXPLORE) and safe and not self.recovering and t < EXPLORE_UNTIL and                 self.nav is not None and \
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
                if visible and dist_t < LOOT_CRATE_SHOOT_R:
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
        # squad all in, in a fight with a line of fire: keep moving (strafe at our range, hidden from the others, dodge)
        if COMBAT_MOVE and self.all_in and fight_m is not None and mode not in COMBAT_MOVE_SKIP:
            cm = self.combat_move(obs, me, fight_m, t, t_start)
            if cm is not None:
                move, mode = cm
        moving = move != [0.0, 0.0]
        jinking, extra_boost = self.evade(obs, me, move, t)
        if jinking:
            move = jinking

        # ---- stuck detection
        self.hist.append((t, x, y))
        jump = False
        boost = not me["in_zone"] or (self.escape is not None and me["energy"] > 30) or extra_boost or \
            (mode == "CONTEST" and dgoal > 6.0 and me["energy"] > 40) or             (mode == "FINISH" and dgoal > 8.0 and me["energy"] > 30)
        if mode == "VANISH" and dgoal > 3.0 and me["energy"] > 20:
            boost = True  # they see us anyway: get out of sight fast
        if mode == "HIDDEN":
            boost = False  # silent: no boost / jet noise ...
            move = [move[0] * VANISH_WALK, move[1] * VANISH_WALK]  # ... and no footsteps (under 5 m/s)
        if mode == "RUSH" and dgoal > 25.0 and me["energy"] > 30:
            boost = True  # finish rush: get there before it gets away / heals
        if mode == "ZONE" and dgoal > 6.0 and me["energy"] > 5:
            boost = True  # the circle is coming: run
        if mode == "DISENGAGE" and dgoal > 3.0 and me["energy"] > 30:
            boost = True  # reach the hiding spot fast
        if LOOT_BOOST and mode in ("cube", "pickup", "crate", "npc", "pile", "explore") and dgoal > 15.0 and \
                me["energy"] > LOOT_BOOST_E:
            boost = True  # run (19.5 m/s, not 11.7) on loot trips, keeping energy for fights and escapes
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
                # standing in the open with enemies around: they can come from anywhere -> watch wider
                if OPEN_WATCH and self.nav is not None:
                    if t - self.open_t > 2.0:
                        self.open_t = t
                        self.open_here = self.nav.exposure(self.nav.cell_of(x, y))[0]
                    if self.open_here >= 8 and self.danger(x, y, t, 150.0, 15.0):
                        yaw = self.cover_face + 2.0 * math.sin(t * 0.8)
        else:
            yaw = math.atan2(zone["center"][1] - y, zone["center"][0] - x)
            if SWEEP and not fighting:
                yaw += 1.0 * math.sin(t * 0.5)
        if OPENING_SCAN and t < 3.0 and not mechs:
            # first 3 s: one full turn of the torso to spot loot and the first hunter
            if self.spawn_yaw is None:
                self.spawn_yaw = me["aim"][0]
            yaw = self.spawn_yaw + t * 2.2
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
        hiding_laser = self.escape is not None and self.escape.get("laser")
        if hiding_laser and not mechs:
            # hidden from a laser: watch the way it would come (see it first, front armor toward it)
            fx, fy = self.escape["from"][0], self.escape["from"][1]
            yaw, pitch = math.atan2(fy - y, fx - x), 0.0
        if not mechs and shoot is None:
            sy, sj, sb = self.scan_step(me, obs["mechs"], mode, t)
            if sy is not None:
                yaw, pitch = sy, 0.0  # gear sweep: a full turn to spot loot behind us
                jump, boost = jump or sj, boost or sb
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
        no_shot = False
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
            has_gun = any(w and w["kind"] == "gun" for w in weapons)
            # laser and lightning are instant: no lead without a gun, or while the laser charges
            speed = 0.0 if (laser_charging or not LEAD or not has_gun) else self._gun_speed(me)
            zoff = self.hit_zoff(me, m)
            k = self.aim.lead(m["id"]) if (LEARN_AIM and is_mech and not (self.all_in and ALLIN_FULL_LEAD)) else 1.0
            tpos = m["pos"]
            if LASER_LEAD_S and speed == 0.0 and laser_charging:
                # hitscan, but it fires on our next decision: aim where it will be then
                tpos = [m["pos"][0] + m["vel"][0] * LASER_LEAD_S, m["pos"][1] + m["vel"][1] * LASER_LEAD_S, m["pos"][2]]
            aim, h = self.aim_at(me, tpos, m["vel"], zoff if zoff is not None else 4.5 * scale, speed, k)
            act["aim"] = aim
            no_shot = zoff is None  # target behind cover right now: a free moment to reload
            if zoff is not None:
                tt = h / speed if speed else 0.0
                gun_ok = True
                if HOLD_DODGY and is_mech and speed and h > 60 and not (self.all_in and ALLIN_FULL_LEAD):
                    # long shot: only if it can't stray further than ~1.5 body radii during the flight
                    gun_ok = self.aim.surprise(m["id"], tt) < 2.5 * scale * 1.5
                fire = self.fire(me, h, aim, size, is_mech, late or (self.all_in and ALLIN_LOW_RESERVE), gun_ok)
                if self.late_zone and is_mech and not self.all_in and not self.late_fire_ok(me, m, h, t):
                    fire = [False, False]  # late: keep regenerating, don't start fights we can't end fast
                if mode == "DROPWATCH" and is_mech and t - self.attackers.get(m["id"], -1e9) > 3.0:
                    fire = [False, False]  # waiting out a fight at a drop: don't join it unless shot at
                if LASER_KILLSHOT and is_mech:
                    fire = self.laser_killshot(me, m, aim, h, size, fire)
                act["fire"] = fire
                if LEARN_AIM and is_mech and speed and any(f and w and w["kind"] == "gun" for f, w in zip(fire, weapons)):
                    self.aim.record(t, m, tt)
            # mortar: over cover, at a slow/standing target, or at a bunched pair (gun can't do those well)
            if is_mech and not (self.late_zone and not self.late_fire_ok(me, m, h, t)):
                gw = next((w for w in weapons if w and w["kind"] == "gun"), None)
                gun_ready = gw is not None and gw.get("ammo", 0) > 0 and gw.get("reload", 0) <= 0
                # empty / reloading gun: the mortar takes the shot instead of holding fire
                plan = self.mortar_plan(me, m, t, gun_has_shot=zoff is not None and gun_ready, others=mechs)
                if plan is None and self.mortar_commit[1] == m["id"] and t < self.mortar_commit[0]:
                    plan = self.mortar_plan(me, m, t, gun_has_shot=False, others=mechs)  # finish turning onto it
                if plan is not None:
                    maim, slot = plan
                    if self.mortar_commit[1] != m["id"] or t >= self.mortar_commit[0]:
                        self.mortar_commit = (t + 1.5, m["id"])
                    act["aim"] = maim
                    ok = abs(wrap(me["aim"][0] - maim[0])) < 0.03 and abs(me["aim"][1] - maim[1]) < 0.03
                    act["fire"] = [ok and i == slot for i in range(2)]
            e = m["pos"]
            mf.draw.line([x, y, z + 7], [e[0], e[1], e[2] + 4.5], "#ff5040")
        else:
            self.cur = None
            self.fire_since.clear()
            # nobody visible: lob a shell at an enemy that just ducked behind a building (seen < 2 s ago)
            if MORTAR_USE and self.mortar_slot(weapons) is not None and not self.late_zone:
                for eid, (et, epos, evel) in self.epos.items():
                    if eid < 100 and t - et < 2.0:
                        plan = self.mortar_plan(me, {"id": eid, "kind": "mech", "pos": epos, "vel": [0.0, 0.0, 0.0]},
                                                t, gun_has_shot=False)
                        if plan is not None:
                            maim, slot = plan
                            act["aim"] = maim
                            ok = abs(wrap(me["aim"][0] - maim[0])) < 0.03 and abs(me["aim"][1] - maim[1]) < 0.03
                            act["fire"] = [ok and i == slot for i in range(2)]
                            break

        # ---- hidden: hold fire (a shot is heard 320 m away); nobody can see us, or we wouldn't be hidden
        if hidden:
            act["fire"] = [False, False]

        # ---- reload: when calm, during a pause in a fight, or before picking one (never while landing shots)
        g = next((w for w in weapons if w and w["kind"] == "gun"), None)
        if g and g.get("reload", 0) == 0:
            mag = g["stats"].get("mag", 12)
            ammo = g.get("ammo", 0)
            calm = not mechs and shoot is None and ammo < mag / 2
            if self.all_in and ALLIN_RELOAD and not mechs and shoot is None:
                # nobody in sight: top up at 60% or less, or anything missing after 3 s of quiet
                calm = ammo <= 0.6 * mag or (ammo < mag and t - self.threat_t > 3.0)
            pause = RELOAD_IN_PAUSE and mechs and no_shot and ammo < mag * RELOAD_PAUSE_FRAC
            topup = hiding_laser and (not mechs or no_shot) and ammo < mag * LASER_RELOAD_FRAC
            if hidden and any(math.hypot(ex - x, ey - y) < 60.0 for _, ex, ey, _ in self._known_enemies(obs["mechs"], t)):
                calm = pause = topup = False  # a reload is heard within 60 m
                self.want_reload = False
            if calm or pause or self.want_reload or topup:
                act["reload"] = True
        self.want_reload = False

        # ---- debug
        if self.path:
            for k2 in range(self.pidx, min(len(self.path), self.pidx + 30), 3):
                px, py = self.path[k2]
                mf.draw.point([px, py, z + 1], "#40ff80", 3)
        mf.draw.point([gx, gy, z + 1], "#40c0ff", 8)
        for it in list(self.mem.items.values())[:20]:
            mf.draw.point([it["x"], it["y"], it["z"] + 1], "#ffd060", 4)
        what = f"{mode} {self.style} {self.situation} k{self.aim.gk:.2f}"
        mf.draw.label(f"hp {me['hp']:.0f} {what} d{dgoal:.0f} cubes {me['cubes']:.0f}"
                      f"{' tgt %d' % self.cur if self.cur is not None else ''}"
                      f"{' S' if self.unstick else ''}{' P' if self.search else ''}")
        return act

    def read_situation(self, obs, t):
        """Practice: house bots are a squad that never fights itself, so before the zone gets deadly every death is
        one of our kills. More deaths than our kills (for SITUATION_CONFIRM_S, in case a kill event lags) means
        bots are killing each other: a free-for-all against other players' bots. Once "ffa", it stays."""
        k = sum(1 for e in obs["events"] if e["type"] == "kill")
        if k:
            self.last_kill_t = t
        self.our_kills += k
        if SITUATION and self.situation == "squad":
            dead = self.cfg["match"].get("mechs", 16) - obs["alive"]
            if dead > self.our_kills and t < SITUATION_UNTIL:
                if self.odd_deaths_t is None:
                    self.odd_deaths_t = t
                elif t - self.odd_deaths_t >= SITUATION_CONFIRM_S:
                    self.situation = "ffa"
                    mf.log(f"t={t:.1f} {dead} dead, {self.our_kills} by us: free-for-all")
            else:
                self.odd_deaths_t = None
        # wait out fights at drops only where bots weaken each other
        self.wait_calm = DROP_WAIT_CALM and self.situation == "ffa"


if __name__ == "__main__":
    mf.run(Bot)
