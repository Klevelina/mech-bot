"""Local match simulator (approximate): one bot under test vs 15 scripted house bots.

Use it to compare bot versions and catch bugs without spending a submission. It is NOT the real server:
the house bots here are a guess (one hunter at first, more join over time, they know where we are), physics
is simplified (no jumping/jetting, no mortar). Check that it ranks versions like the real scores did.

    python sim/simulate.py --bot-dir . --matches 12            # this working tree
    python sim/simulate.py --bot-dir path/to/other/version     # e.g. a `git worktree` of an older commit
    python sim/simulate.py --example                           # the kit's example bot (baseline 1.15)
"""
import argparse
import gzip
import json
import math
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "mechfight-kit")

Search = None  # set from the simulator's nav module in one_match
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


# ---------------------------------------------------------------- world objects
class Mech:
    def __init__(self, mid, pos, cfg, ours=False):
        mc = cfg["mech"]
        self.id = mid
        self.ours = ours
        self.pos = list(pos)
        self.vel = [0.0, 0.0]
        self.aim = [0.0, 0.0]
        self.cmd_aim = [0.0, 0.0]
        self.move = [0.0, 0.0]
        self.boost = False
        self.fire = [False, False]
        self.stats = dict(mc)
        self.max_hp = float(mc["hp"])
        self.hp = self.max_hp
        self.energy = float(mc["energy"])
        self.energy_spent_t = -1e9
        self.cubes = 0
        self.alive = True
        self.place = None
        self.kills = 0
        self.last_combat_t = -1e9
        self.armor = 0.0
        self.upgrades = []
        self.wstats = {k: dict(v) for k, v in cfg["weapons"].items()}
        g = cfg["weapons"]["gun"]
        self.weapons = [{"kind": "gun", "cooldown": 0.0, "ammo": g["mag"], "reload": 0.0, "charge": 0.0}, None]
        self.step_t = 0.0
        self.seen_t = {}  # (observer id) -> last time seen
        self.death_t = None
        self.cause = None


class Sim:
    def __init__(self, cfg, nav, seed, hunt_every=25.0, house_special=0.3, house_aim_deg=1.2, omniscient=True):
        self.cfg = cfg
        self.nav = nav
        self.rng = random.Random(seed)
        self.t = 0.0
        self.tick = 0
        self.hunt_every = hunt_every
        self.house_special = house_special
        self.house_aim = math.radians(house_aim_deg)
        self.omni = omniscient
        self.mechs = []
        self.npcs = []
        self.bullets = []
        self.crates = {}
        self.cubes = {}
        self.pickups = {}
        self.drops = {}
        self.next_id = {"crate": 1000, "cube": 5000, "pickup": 8000, "drop": 20000, "npc": 100}
        self.sounds = []   # (t, kind, x, y, source id)
        self.events = {}   # mech id -> list of events since its last decision
        self.offer = None
        self.offers_q = []
        self.paths = {}    # house id -> (path, t computed)
        self.wander = {}   # house id -> goal
        self.strafe = {}   # house id -> (side, flip t)
        self._zone_setup()
        self._spawn()

    # ---------------- zone
    def _zone_setup(self):
        z = self.cfg["zone"]
        c, r = list(z["start_center"]), float(z["start_radius"])
        self.circles = [(c, r)]
        t0 = 0.0
        self.phases = []
        for ph in z["phases"]:
            nr = float(ph["radius"])
            room = max(0.0, r - nr)
            for _ in range(50):
                a = self.rng.uniform(0, 2 * math.pi)
                d = self.rng.uniform(0, room)
                nc = [c[0] + d * math.cos(a), c[1] + d * math.sin(a)]
                cell = self.nav.cell_of(*nc)
                if self.nav.comp[cell] == self.nav.main or nr > 30:
                    break
            hold_end = t0 + ph["hold_s"]
            shrink_end = hold_end + ph["shrink_s"]
            self.phases.append((t0, hold_end, shrink_end, c, r, nc, nr, ph["dps"]))
            c, r, t0 = nc, nr, shrink_end
            self.circles.append((c, r))
        self.final_t = t0

    def zone(self):
        t = self.t
        for (t0, hold_end, shrink_end, c, r, nc, nr, dps) in self.phases:
            if t < shrink_end:
                if t < hold_end:
                    cur_c, cur_r = c, r
                else:
                    f = (t - hold_end) / max(1e-6, shrink_end - hold_end)
                    cur_c = [c[0] + (nc[0] - c[0]) * f, c[1] + (nc[1] - c[1]) * f]
                    cur_r = r + (nr - r) * f
                return {"center": cur_c, "radius": cur_r, "next_center": list(nc), "next_radius": nr,
                        "shrink_start": hold_end, "shrink_end": shrink_end, "dps": dps}
        (t0, hold_end, shrink_end, c, r, nc, nr, dps) = self.phases[-1]
        return {"center": list(nc), "radius": 0.0, "next_center": list(nc), "next_radius": 0.0,
                "shrink_start": hold_end, "shrink_end": shrink_end, "dps": dps}

    @staticmethod
    def in_circle(x, y, z):
        return math.hypot(x - z["center"][0], y - z["center"][1]) <= z["radius"]

    # ---------------- spawning
    def random_ground(self, cx, cy, r, open_only=True):
        nav = self.nav
        for _ in range(400):
            a = self.rng.uniform(0, 2 * math.pi)
            d = r * math.sqrt(self.rng.random())
            x, y = cx + d * math.cos(a), cy + d * math.sin(a)
            c = nav.cell_of(x, y)
            if nav.comp[c] == nav.main and (not open_only or nav.mask[c] == 255):
                px, py = nav.center(c)
                return [px, py, nav.surf[c]]
        c = nav.nearest_main(cx, cy)
        px, py = nav.center(c)
        return [px, py, nav.surf[c]]

    def _spawn(self):
        z0 = self.cfg["zone"]
        c, r = z0["start_center"], z0["start_radius"] * self.cfg["match"].get("spawn_zone_fraction", 0.8)
        sep = self.cfg["match"].get("spawn_min_separation", 130)
        spots = []
        for i in range(16):
            for tries in range(300):
                p = self.random_ground(c[0], c[1], r)
                if all(math.hypot(p[0] - q[0], p[1] - q[1]) > (sep if tries < 200 else sep / 2) for q in spots):
                    break
            spots.append(p)
        self.ours_id = self.rng.randrange(16)
        for i, p in enumerate(spots):
            m = Mech(i, p, self.cfg, ours=(i == self.ours_id))
            m.aim = [self.rng.uniform(-math.pi, math.pi), 0.0]
            m.cmd_aim = list(m.aim)
            if not m.ours and self.rng.random() < self.house_special:
                self.give(m, "lightning", 1)
            self.mechs.append(m)
            self.events[i] = []
        for _ in range(int(self.cfg["crates"].get("count", 36) * 0.75)):
            p = self.random_ground(c[0], c[1], z0["start_radius"])
            self.crates[self._nid("crate")] = {"pos": p, "hp": float(self.cfg["crates"].get("hp", 300))}
        for _ in range(int(self.cfg["loot"].get("floor_count", 40) * 0.75)):
            p = self.random_ground(c[0], c[1], z0["start_radius"])
            self.pickups[self._nid("pickup")] = {"pos": p, "kind": self.rng.choice(["mortar", "laser", "lightning"])}
        npc = self.cfg["npc"]
        for _ in range(int(npc.get("count", 12))):
            p = self.random_ground(c[0], c[1], z0["start_radius"])
            self.npcs.append({"id": self._nid("npc"), "pos": p, "home": list(p), "hp": float(npc["hp"]),
                              "max_hp": float(npc["hp"]), "cd": 0.0, "alive": True, "aggro": None})

    def _nid(self, kind):
        i = self.next_id[kind]
        self.next_id[kind] += 1
        return i

    def give(self, m, kind, slot):
        m.weapons[slot] = {"kind": kind, "cooldown": self.cfg["loot"].get("equip_s", 1.0), "ammo": 0, "reload": 0.0,
                           "charge": 0.0}

    # ---------------- helpers
    def alive_mechs(self):
        return [m for m in self.mechs if m.alive]

    def los(self, a, b):
        return self.nav.los(a[0], a[1], a[2], b[0], b[1], b[2], skip=2.0, step=2.0)

    def scale(self, m):
        return 1.0 + self.cfg["cubes"].get("size_per_cube", 0.05) * m.cubes

    def sees(self, obs_m, tx, ty, tz, height):
        """Vision rule: within near_range all around, or range inside the fov wedge; line of sight to center/top."""
        v = self.cfg["vision"]
        d = math.hypot(tx - obs_m.pos[0], ty - obs_m.pos[1])
        if d > v["range"] + obs_m.stats.get("vision_bonus", 0):
            return False
        if d > v["near_range"] + obs_m.stats.get("near_range_bonus", 0):
            b = math.atan2(ty - obs_m.pos[1], tx - obs_m.pos[0])
            if abs(wrap(b - obs_m.aim[0])) > math.radians(v["fov_deg"]) / 2:
                return False
        eye = [obs_m.pos[0], obs_m.pos[1], obs_m.pos[2] + obs_m.stats["eye_height"] * self.scale(obs_m)]
        return self.los(eye, [tx, ty, tz + height / 2]) or self.los(eye, [tx, ty, tz + height])

    def emit(self, kind, x, y, src):
        self.sounds.append((self.t, kind, x, y, src))

    # ---------------- damage
    def damage(self, target, amount, weapon, src, src_pos):
        if not target.alive:
            return
        rear = False
        if src_pos is not None:
            b = math.atan2(src_pos[1] - target.pos[1], src_pos[0] - target.pos[0])
            if abs(wrap(b - target.aim[0])) > math.radians(self.cfg["mech"]["front_arc_deg"]) / 2:
                rear = True
                amount *= self.cfg["mech"]["rear_mult"]
        if weapon != "zone":
            amount *= max(0.0, 1.0 - target.armor)
        target.hp -= amount
        target.last_combat_t = self.t
        ev = {"type": "damaged", "amount": round(amount, 1), "weapon": weapon,
              "bearing": None if src_pos is None else math.atan2(src_pos[1] - target.pos[1], src_pos[0] - target.pos[0]),
              "by": None if src is None else src.get("id") if isinstance(src, dict) else src.id}
        if rear:
            ev["rear"] = True
        self.events[target.id].append(ev)
        if isinstance(src, Mech):
            src.last_combat_t = self.t
            self.events[src.id].append({"type": "hit", "target": target.id, "amount": round(amount, 1), "weapon": weapon})
        if target.hp <= 0:
            target.alive = False
            target.death_t = self.t
            target.cause = weapon
            self._dead_this_tick.append(target)
            if isinstance(src, Mech):
                src.kills += 1
                self.events[src.id].append({"type": "kill", "target": target.id})
            self.emit("death", target.pos[0], target.pos[1], target.id)
            for k in range(target.cubes + self.cfg["cubes"].get("death_drop_extra", 2)):
                p = [target.pos[0] + self.rng.uniform(-2, 2), target.pos[1] + self.rng.uniform(-2, 2), target.pos[2]]
                self.cubes[self._nid("cube")] = {"pos": p}
            for w in target.weapons:
                if w and w["kind"] != "gun":
                    self.pickups[self._nid("pickup")] = {"pos": list(target.pos), "kind": w["kind"]}

    def damage_npc(self, npc, amount, src):
        npc["hp"] -= amount
        npc["aggro"] = src.id if isinstance(src, Mech) else npc["aggro"]
        if isinstance(src, Mech):
            src.last_combat_t = self.t
            self.events[src.id].append({"type": "hit", "target": npc["id"], "amount": round(amount, 1), "weapon": "gun"})
        if npc["hp"] <= 0 and npc["alive"]:
            npc["alive"] = False
            for _ in range(int(self.cfg["npc"].get("cubes", 2))):
                p = [npc["pos"][0] + self.rng.uniform(-2, 2), npc["pos"][1] + self.rng.uniform(-2, 2), npc["pos"][2]]
                self.cubes[self._nid("cube")] = {"pos": p}

    def dmg_mult(self, m):
        c = self.cfg["cubes"]
        return 1.0 + c.get("damage_per_cube", 0.04) * min(m.cubes, c.get("stat_cap", 10))

    # ---------------- weapons
    def fire_weapons(self, m, dt):
        for slot, w in enumerate(m.weapons):
            if not w:
                continue
            st = m.wstats[w["kind"]]
            w["cooldown"] = max(0.0, w["cooldown"] - dt)
            k = w["kind"]
            if k == "gun":
                if w["reload"] > 0:
                    w["reload"] -= dt
                    if w["reload"] <= 0:
                        w["reload"] = 0.0
                        w["ammo"] = st["mag"]
                    continue
                if m.fire[slot] and w["cooldown"] <= 0 and w["ammo"] > 0:
                    w["cooldown"] = st["cooldown"]
                    w["ammo"] -= 1
                    if w["ammo"] <= 0:
                        w["reload"] = st["reload"]
                    sp = math.radians(st.get("spread_deg", 0.6)) * 0.5
                    a = m.aim[0] + self.rng.gauss(0, sp)
                    b = m.aim[1] + self.rng.gauss(0, sp)
                    d = (math.cos(b) * math.cos(a), math.cos(b) * math.sin(a), math.sin(b))
                    mz = m.pos[2] + m.stats["muzzle_height"] * self.scale(m)
                    r0 = m.stats["radius"] * self.scale(m) + 0.5
                    self.bullets.append({"pos": [m.pos[0] + d[0] * r0, m.pos[1] + d[1] * r0, mz + d[2] * r0], "dir": d,
                                         "speed": st["speed"], "left": st["range"], "owner": m,
                                         "dmg": st["damage"] * self.dmg_mult(m)})
                    self.emit("gun", m.pos[0], m.pos[1], m.id)
            elif k == "lightning":
                if m.fire[slot] and w["cooldown"] <= 0 and m.energy >= st["energy"]:
                    w["cooldown"] = st["cooldown"]
                    m.energy -= st["energy"]
                    m.energy_spent_t = self.t
                    self.emit("lightning", m.pos[0], m.pos[1], m.id)
                    self.lightning(m, st)
            elif k == "laser":
                full = st["charge"]
                if m.fire[slot] and w["cooldown"] <= 0:
                    if w["charge"] == 0:
                        self.emit("laser_charge", m.pos[0], m.pos[1], m.id)
                    w["charge"] = min(full + st.get("hold_s", 2), w["charge"] + dt)
                    if w["charge"] >= full + st.get("hold_s", 2):
                        self.laser(m, st, w)
                elif w["charge"] > 0:
                    if w["charge"] >= st.get("min_charge", 0.15):
                        self.laser(m, st, w)
                    else:
                        w["charge"] += dt
                        if w["charge"] >= st.get("min_charge", 0.15):
                            self.laser(m, st, w)

    def targets_near(self, m, r):
        out = []
        for o in self.alive_mechs():
            if o is not m and math.hypot(o.pos[0] - m.pos[0], o.pos[1] - m.pos[1]) <= r:
                out.append(("mech", o))
        for n in self.npcs:
            if n["alive"] and math.hypot(n["pos"][0] - m.pos[0], n["pos"][1] - m.pos[1]) <= r:
                out.append(("npc", n))
        for cid, c in self.crates.items():
            if math.hypot(c["pos"][0] - m.pos[0], c["pos"][1] - m.pos[1]) <= r:
                out.append(("crate", (cid, c)))
        return out

    def lightning(self, m, st):
        mz = m.pos[2] + m.stats["muzzle_height"] * self.scale(m)
        best, bd = None, 1e9
        for kind, o in self.targets_near(m, st["range"]):
            p = o.pos if kind == "mech" else (o["pos"] if kind == "npc" else o[1]["pos"])
            b = math.atan2(p[1] - m.pos[1], p[0] - m.pos[0])
            if abs(wrap(b - m.aim[0])) > math.radians(st["cone_deg"]) / 2:
                continue
            d = math.hypot(p[0] - m.pos[0], p[1] - m.pos[1])
            if d < bd and self.los([m.pos[0], m.pos[1], mz], [p[0], p[1], p[2] + 2.0]):
                best, bd = (kind, o), d
        if best is None:
            return
        dmg = st["damage"] * self.dmg_mult(m)
        hit = [best]
        cur = best
        for _ in range(int(st.get("chain", 2)) + 1):
            kind, o = cur
            self.apply_hit(m, kind, o, dmg, "lightning")
            dmg *= st.get("chain_falloff", 0.7)
            p = o.pos if kind == "mech" else (o["pos"] if kind == "npc" else o[1]["pos"])
            nxt = None
            for o2 in self.alive_mechs():
                if o2 is not m and all(o2 is not h[1] for h in hit) and \
                        math.hypot(o2.pos[0] - p[0], o2.pos[1] - p[1]) <= st.get("chain_range", 14):
                    nxt = ("mech", o2)
                    break
            if nxt is None:
                break
            hit.append(nxt)
            cur = nxt

    def laser(self, m, st, w):
        frac = min(1.0, w["charge"] / st["charge"])
        dmg = st["damage"] * (st.get("min_frac", 0.35) + (1 - st.get("min_frac", 0.35)) * frac) * self.dmg_mult(m)
        w["charge"] = 0.0
        w["cooldown"] = st["cooldown"]
        self.emit("laser", m.pos[0], m.pos[1], m.id)
        a, b = m.aim
        d = (math.cos(b) * math.cos(a), math.cos(b) * math.sin(a), math.sin(b))
        mz = m.pos[2] + m.stats["muzzle_height"] * self.scale(m)
        best, bt = None, st["range"]
        for kind, o in self.targets_near(m, st["range"]):
            if kind == "crate":
                continue
            p = o.pos if kind == "mech" else o["pos"]
            rr = (o.stats["radius"] * self.scale(o)) if kind == "mech" else 1.8
            vx, vy = p[0] - m.pos[0], p[1] - m.pos[1]
            tproj = vx * d[0] + vy * d[1]
            if tproj <= 0 or tproj > bt:
                continue
            perp = abs(vx * d[1] - vy * d[0])
            hz = mz + d[2] * tproj
            top = p[2] + (9.0 * self.scale(o) if kind == "mech" else 3.5)
            if perp <= rr and p[2] <= hz <= top and self.los([m.pos[0], m.pos[1], mz], [p[0], p[1], hz]):
                best, bt = (kind, o), tproj
        if best:
            self.apply_hit(m, best[0], best[1], dmg, "laser")

    def apply_hit(self, src, kind, o, dmg, weapon):
        if kind == "mech":
            self.damage(o, dmg, weapon, src, src.pos)
        elif kind == "npc":
            self.damage_npc(o, dmg, src)
        else:
            cid, c = o
            c["hp"] -= dmg
            if c["hp"] <= 0 and cid in self.crates:
                del self.crates[cid]
                self.emit("crate", c["pos"][0], c["pos"][1], src.id)
                self.cubes[self._nid("cube")] = {"pos": list(c["pos"])}
                if self.rng.random() < self.cfg["loot"].get("crate_weapon_chance", 0.5):
                    self.pickups[self._nid("pickup")] = {"pos": list(c["pos"]),
                                                          "kind": self.rng.choice(["mortar", "laser", "lightning"])}

    def step_bullets(self, dt):
        nav = self.nav
        keep = []
        for bl in self.bullets:
            dist = bl["speed"] * dt
            n = max(1, int(dist / 3.0))
            stp = dist / n
            alive = True
            for _ in range(n):
                p = bl["pos"]
                p[0] += bl["dir"][0] * stp
                p[1] += bl["dir"][1] * stp
                p[2] += bl["dir"][2] * stp
                bl["left"] -= stp
                if bl["left"] <= 0:
                    alive = False
                    break
                i, j = int((p[0] - nav.ox) / nav.sub), int((p[1] - nav.oy) / nav.sub)
                if 0 <= i < nav.SW and 0 <= j < nav.SH:
                    fc = j * nav.SW + i
                    if nav.fbase[fc] <= p[2] <= nav.ftop[fc] or nav.ground_z(p[0], p[1]) > p[2]:
                        alive = False
                        break
                hit = None
                for o in self.mechs:
                    if o.alive and o is not bl["owner"]:
                        sc = self.scale(o)
                        if math.hypot(p[0] - o.pos[0], p[1] - o.pos[1]) <= o.stats["radius"] * sc and \
                                o.pos[2] <= p[2] <= o.pos[2] + 9.0 * sc:
                            hit = ("mech", o)
                            break
                if hit is None:
                    for npc in self.npcs:
                        if npc["alive"] and math.hypot(p[0] - npc["pos"][0], p[1] - npc["pos"][1]) <= 1.8 and \
                                npc["pos"][2] <= p[2] <= npc["pos"][2] + 3.5:
                            hit = ("npc", npc)
                            break
                if hit is None:
                    for cid, c in list(self.crates.items()):
                        if math.hypot(p[0] - c["pos"][0], p[1] - c["pos"][1]) <= 1.4 and p[2] <= c["pos"][2] + 2.4:
                            hit = ("crate", (cid, c))
                            break
                if hit:
                    owner = bl["owner"]
                    if hit[0] == "mech":
                        src_pos = [p[0] - bl["dir"][0] * 50, p[1] - bl["dir"][1] * 50]
                        if isinstance(owner, Mech):
                            self.damage(hit[1], bl["dmg"], "gun", owner, src_pos)
                        else:  # npc bullet
                            self.damage(hit[1], bl["dmg"], "npc", owner, src_pos)
                    elif isinstance(owner, Mech):
                        self.apply_hit(owner, hit[0], hit[1], bl["dmg"], "gun")
                    alive = False
                    break
            if alive:
                keep.append(bl)
        self.bullets = keep

    # ---------------- movement
    def step_move(self, m, dt, zone):
        st = m.stats
        charging = any(w and w["kind"] == "laser" and w["charge"] > 0 for w in m.weapons)
        speed = st["boost_speed"] if (m.boost and m.energy > 1) else st["walk_speed"]
        if charging:
            speed *= st.get("charge_move_mult", 0.5)
        mx, my = m.move
        ln = math.hypot(mx, my)
        if ln > 1:
            mx, my = mx / ln, my / ln
        if m.boost and ln > 0.1 and m.energy > 1:
            m.energy -= st["boost_drain"] * dt
            m.energy_spent_t = self.t
        wvx, wvy = mx * speed, my * speed
        dvx, dvy = wvx - m.vel[0], wvy - m.vel[1]
        dn = math.hypot(dvx, dvy)
        acc = st["ground_accel"] * dt
        if dn > acc:
            dvx, dvy = dvx / dn * acc, dvy / dn * acc
        m.vel = [m.vel[0] + dvx, m.vel[1] + dvy]
        nx, ny = m.pos[0] + m.vel[0] * dt, m.pos[1] + m.vel[1] * dt
        nav = self.nav
        c0 = nav.cell_of(m.pos[0], m.pos[1])

        def ok(x, y):
            c = nav.cell_of(x, y)
            if c == c0:
                return True
            if nav.blocked[c]:
                return False
            j0, i0 = divmod(c0, nav.W)
            j1, i1 = divmod(c, nav.W)
            di, dj = i1 - i0, j1 - j0
            if abs(di) > 1 or abs(dj) > 1:
                return False
            k = DIRS.index((di, dj))
            return bool(nav.mask[c0] >> k & 1)
        if ok(nx, ny):
            m.pos[0], m.pos[1] = nx, ny
        elif ok(nx, m.pos[1]):
            m.pos[0] = nx
            m.vel[1] = 0.0
        elif ok(m.pos[0], ny):
            m.pos[1] = ny
            m.vel[0] = 0.0
        else:
            m.vel = [0.0, 0.0]
        m.pos[2] = nav.surf[nav.cell_of(m.pos[0], m.pos[1])]
        # step / boost sounds
        sp = math.hypot(*m.vel)
        if sp > self.cfg["sound"].get("step_speed", 5) and self.t - m.step_t >= self.cfg["sound"].get("step_every", 0.6):
            m.step_t = self.t
            self.emit("boost" if m.boost else "step", m.pos[0], m.pos[1], m.id)
        # aim slew
        dy = wrap(m.cmd_aim[0] - m.aim[0])
        tr = st["turn_rate"] * dt
        m.aim[0] = wrap(m.aim[0] + max(-tr, min(tr, dy)))
        pr = st["pitch_rate"] * dt
        m.aim[1] += max(-pr, min(pr, m.cmd_aim[1] - m.aim[1]))
        # energy regen
        if self.t - m.energy_spent_t > st["energy_regen_delay"]:
            m.energy = min(float(st["energy"]), m.energy + st["energy_regen"] * dt)

    # ---------------- house bot brain
    def house_decide(self, m, ours, zone, hunters):
        nav = self.nav
        t = self.t
        st = m.stats
        target_pos = None
        x, y = m.pos[0], m.pos[1]
        see_us = ours.alive and self.sees(m, ours.pos[0], ours.pos[1], ours.pos[2], 9.0 * self.scale(ours))
        if see_us:
            m.seen_t[ours.id] = t
        if not self.in_circle(x, y, zone):
            goal = zone["center"]
        elif m.id in hunters and ours.alive and (self.omni or t - m.seen_t.get(ours.id, -1e9) < 10):
            goal = ours.pos
            target_pos = ours.pos
        else:
            g = self.wander.get(m.id)
            if g is None or math.hypot(g[0] - x, g[1] - y) < 6 or \
                    math.hypot(g[0] - zone["next_center"][0], g[1] - zone["next_center"][1]) > zone["next_radius"]:
                g = self.random_ground(zone["next_center"][0], zone["next_center"][1], max(5.0, zone["next_radius"] * 0.8))
                self.wander[m.id] = g
            goal = g
        d_us = math.hypot(ours.pos[0] - x, ours.pos[1] - y) if ours.alive else 1e9
        # movement: path toward goal, strafe when engaging
        mv = [0.0, 0.0]
        engaging = see_us and d_us < 150
        if engaging and target_pos is not None and d_us < 70:
            side, flip = self.strafe.get(m.id, (1, 0.0))
            if t >= flip:
                side = -side
                flip = t + self.rng.uniform(0.4, 1.4)
            self.strafe[m.id] = (side, flip)
            b = math.atan2(ours.pos[1] - y, ours.pos[0] - x)
            mv = [-math.sin(b) * side, math.cos(b) * side]
        else:
            path, tc = self.paths.get(m.id, (None, -1e9))
            if path is None or t - tc > 2.0 or (path and math.hypot(path[-1][0] - goal[0], path[-1][1] - goal[1]) > 10):
                s = Search(nav, nav.cell_of(x, y), nav.cell_of(goal[0], goal[1]), weight=2.0, max_exp=6000)
                while not s.step(time.perf_counter() + 1.0):
                    pass
                path = [nav.center(c) for c in s.path]
                self.paths[m.id] = (path, t)
            if path:
                bi = min(range(len(path)), key=lambda k: (path[k][0] - x) ** 2 + (path[k][1] - y) ** 2)
                tx, ty = path[min(len(path) - 1, bi + 3)]
                dx, dy = tx - x, ty - y
                dn = math.hypot(dx, dy)
                if dn > 0.5:
                    mv = [dx / dn, dy / dn]
        m.move = mv
        m.boost = not self.in_circle(x, y, zone)
        # aim + fire
        m.fire = [False, False]
        if see_us:
            gsp = m.wstats["gun"]["speed"]
            px, py, pz = ours.pos
            tt = d_us / gsp
            ax, ay = px + ours.vel[0] * tt, py + ours.vel[1] * tt
            mz = m.pos[2] + st["muzzle_height"]
            yaw = math.atan2(ay - y, ax - x) + self.rng.gauss(0, self.house_aim)
            pitch = math.atan2(pz + 4.5 - mz, math.hypot(ax - x, ay - y)) + self.rng.gauss(0, self.house_aim)
            m.cmd_aim = [yaw, pitch]
            aligned = abs(wrap(m.aim[0] - yaw)) < 0.05
            if aligned and d_us < 200:
                m.fire[0] = True
            if m.weapons[1] and m.weapons[1]["kind"] == "lightning" and d_us < 44 and m.energy > 10:
                m.fire[1] = True
        elif mv != [0.0, 0.0]:
            m.cmd_aim = [math.atan2(mv[1], mv[0]), 0.0]

    # ---------------- drones
    def npc_step(self, dt):
        npc_cfg = self.cfg["npc"]
        for n in self.npcs:
            if not n["alive"]:
                continue
            n["cd"] = max(0.0, n["cd"] - dt)
            if n["cd"] > 0:
                continue
            tgt = None
            if n["aggro"] is not None:
                o = self.mechs[n["aggro"]]
                if o.alive and math.hypot(o.pos[0] - n["pos"][0], o.pos[1] - n["pos"][1]) < npc_cfg.get("deaggro_range", 90):
                    tgt = o
                else:
                    n["aggro"] = None
            if tgt is None:
                for o in self.alive_mechs():
                    if math.hypot(o.pos[0] - n["pos"][0], o.pos[1] - n["pos"][1]) < npc_cfg.get("aggro_range", 45):
                        tgt = o
                        break
            if tgt is None:
                continue
            src = [n["pos"][0], n["pos"][1], n["pos"][2] + 3.0]
            dst = [tgt.pos[0], tgt.pos[1], tgt.pos[2] + 4.5]
            if not self.los(src, dst):
                continue
            n["cd"] = npc_cfg.get("cooldown", 1.0)
            if self.rng.random() < 0.6:
                n["id_obj"] = n
                self.damage(tgt, npc_cfg.get("damage", 25), "npc", {"id": n["id"]}, n["pos"])

    # ---------------- observation for the bot under test
    def build_obs(self, m, zone):
        t = self.t
        vis_mechs = []
        for o in self.mechs:
            if o is m or not o.alive:
                continue
            if self.sees(m, o.pos[0], o.pos[1], o.pos[2], 9.0 * self.scale(o)):
                o.seen_t[m.id] = t
            if t - o.seen_t.get(m.id, -1e9) <= self.cfg["vision"].get("memory_s", 5):
                vis_mechs.append({"id": o.id, "kind": "mech", "pos": [round(v, 2) for v in o.pos],
                                  "vel": [o.vel[0], o.vel[1], 0.0], "aim": list(o.aim), "hp": round(o.hp),
                                  "max_hp": round(o.max_hp), "cubes": o.cubes,
                                  "weapons": [w["kind"] for w in o.weapons if w],
                                  "charging": any(w and w["kind"] == "laser" and w["charge"] > 0 for w in o.weapons)})
        for n in self.npcs:
            if n["alive"] and self.sees(m, n["pos"][0], n["pos"][1], n["pos"][2], 3.5):
                vis_mechs.append({"id": n["id"], "kind": "npc", "pos": list(n["pos"]), "vel": [0.0, 0.0, 0.0],
                                  "aim": [0.0, 0.0], "hp": n["hp"], "max_hp": n["max_hp"]})
        crates = [{"id": i, "pos": c["pos"], "hp": c["hp"]} for i, c in self.crates.items()
                  if self.sees(m, c["pos"][0], c["pos"][1], c["pos"][2], 2.4)]
        cubes = [{"id": i, "pos": c["pos"]} for i, c in self.cubes.items()
                 if self.sees(m, c["pos"][0], c["pos"][1], c["pos"][2], 0.5)]
        pickups = [{"id": i, "kind": p["kind"], "pos": p["pos"]} for i, p in self.pickups.items()
                   if self.sees(m, p["pos"][0], p["pos"][1], p["pos"][2], 0.5)]
        sounds = []
        rng_cfg = self.cfg["sound"]["range"]
        for (st_, kind, sx, sy, src) in self.sounds:
            if src == m.id or t - st_ > 0.11:
                continue
            d = math.hypot(sx - m.pos[0], sy - m.pos[1])
            if d > rng_cfg.get(kind, 0):
                continue
            band = "near" if d < self.cfg["sound"].get("near", 60) else "mid" if d < self.cfg["sound"].get("mid", 160) else "far"
            b = math.atan2(sy - m.pos[1], sx - m.pos[0]) + self.rng.gauss(0, math.radians(self.cfg["sound"]["bearing_noise_deg"]))
            sounds.append({"kind": kind, "bearing": wrap(b), "dist": band, "age": round(t - st_, 2)})
        weapons = []
        for w in m.weapons:
            if w is None:
                weapons.append(None)
                continue
            d = {"kind": w["kind"], "cooldown": round(w["cooldown"], 2), "stats": dict(m.wstats[w["kind"]])}
            if w["kind"] == "gun":
                d["ammo"] = w["ammo"]
                d["reload"] = round(w["reload"], 2)
            if w["kind"] == "laser":
                d["charge"] = round(w["charge"], 2)
            weapons.append(d)
        drops = [{"id": i, "pos": d["pos"], "radius": self.cfg["drops"]["radius"], "lands_in": max(0.0, d["land_t"] - t),
                  "capture_s": self.cfg["drops"]["capture_s"], "progress": d["progress"], "holder": d["holder"]}
                 for i, d in self.drops.items()]
        stats = dict(m.stats)
        ev = self.events[m.id]
        self.events[m.id] = []
        return {"type": "obs", "tick": self.tick, "t": round(t, 2), "alive": len(self.alive_mechs()),
                "self": {"id": m.id, "pos": list(m.pos), "vel": [m.vel[0], m.vel[1], 0.0], "aim": list(m.aim),
                         "hp": m.hp, "max_hp": m.max_hp, "energy": m.energy, "max_energy": float(m.stats["energy"]),
                         "grounded": True, "cubes": m.cubes, "upgrades": list(m.upgrades), "weapons": weapons,
                         "in_zone": self.in_circle(m.pos[0], m.pos[1], zone), "stats": stats},
                "mechs": vis_mechs, "projectiles": [], "crates": crates, "cubes": cubes, "pickups": pickups,
                "sounds": sounds, "beams": [], "events": ev, "drops": drops, "zone": zone, "offer": self.offer}

    def apply_action(self, m, a):
        if not a:
            return
        if "move" in a:
            m.move = list(a["move"])[:2]
        m.boost = bool(a.get("boost", False))
        if "aim" in a:
            st = m.stats
            m.cmd_aim = [a["aim"][0], max(st["min_pitch"], min(st["max_pitch"], a["aim"][1]))]
        m.fire = list(a.get("fire", [False, False]))[:2] + [False] * (2 - len(a.get("fire", [])))
        if a.get("reload") and m.weapons[0] and m.weapons[0]["kind"] == "gun" and m.weapons[0]["reload"] <= 0 and \
                m.weapons[0]["ammo"] < m.wstats["gun"]["mag"]:
            m.weapons[0]["reload"] = m.wstats["gun"]["reload"]
        if "pick" in a and self.offer is not None:
            idx = int(a["pick"])
            ch = self.offer["choices"]
            if 0 <= idx < len(ch):
                self.apply_upgrade(m, ch[idx]["id"])
            self.offer = None
        if "take" in a:
            pid = a["take"]
            p = self.pickups.get(pid)
            if p is None:
                self.events[m.id].append({"type": "take_failed", "id": pid, "reason": "gone"})
            elif math.hypot(p["pos"][0] - m.pos[0], p["pos"][1] - m.pos[1]) > self.cfg["loot"]["pickup_radius"]:
                self.events[m.id].append({"type": "take_failed", "id": pid, "reason": "range"})
            elif any(w and w["kind"] == p["kind"] for w in m.weapons):
                self.events[m.id].append({"type": "take_failed", "id": pid, "reason": "have"})
            else:
                slot = 1 if m.weapons[1] is None else 0 if m.weapons[0] is None else a.get("drop")
                if slot is None:
                    self.events[m.id].append({"type": "take_failed", "id": pid, "reason": "full"})
                else:
                    old = m.weapons[slot]
                    if old:
                        nid = self._nid("pickup")
                        self.pickups[nid] = {"pos": list(m.pos), "kind": old["kind"]}
                        self.events[m.id].append({"type": "dropped", "id": nid, "kind": old["kind"], "slot": slot})
                    del self.pickups[pid]
                    self.give(m, p["kind"], slot)
                    self.events[m.id].append({"type": "took", "id": pid, "kind": p["kind"], "slot": slot})

    def apply_upgrade(self, m, uid):
        u = next((u for u in self.cfg["upgrades"] if u["id"] == uid), None)
        if u is None:
            return
        m.upgrades.append(uid)
        self.events[m.id].append({"type": "upgrade", "id": uid})
        for e in u["effects"]:
            path = e["path"].split(".")
            if path[0] == "mech":
                key = path[1]
                if key == "hp":
                    old = m.max_hp
                    m.max_hp = m.max_hp * e.get("mul", 1.0) + e.get("add", 0.0)
                    m.hp += m.max_hp - old
                elif key == "armor":
                    m.armor += e.get("add", 0.0)
                elif key in m.stats:
                    m.stats[key] = m.stats[key] * e.get("mul", 1.0) + e.get("add", 0.0)
            elif path[0] == "weapons" and len(path) == 3 and path[1] in m.wstats:
                ws = m.wstats[path[1]]
                ws[path[2]] = ws.get(path[2], 0.0) * e.get("mul", 1.0) + e.get("add", 0.0)

    def pick_cube(self, m):
        cc = self.cfg["cubes"]
        for cid, c in list(self.cubes.items()):
            if math.hypot(c["pos"][0] - m.pos[0], c["pos"][1] - m.pos[1]) <= cc["pickup_radius"]:
                del self.cubes[cid]
                m.cubes += 1
                if m.cubes <= cc.get("stat_cap", 10):
                    old = m.max_hp
                    m.max_hp *= 1.0 + cc.get("hp_per_cube", 0.04)
                    m.hp += m.max_hp - old
                if m.ours:
                    self.events[m.id].append({"type": "pickup", "cubes": m.cubes})
                    if m.cubes in cc["upgrade_thresholds"]:
                        self.offers_q.append(m.cubes)

    def make_offer(self, m):
        if self.offer is not None or not self.offers_q:
            return
        self.offers_q.pop(0)
        held = {w["kind"] for w in m.weapons if w}
        avail = [u for u in self.cfg["upgrades"] if (not u.get("requires") or u["requires"] in held)
                 and m.upgrades.count(u["id"]) < u.get("max", 1)]
        ch = self.rng.sample(avail, min(3, len(avail)))
        self.offer = {"id": len(m.upgrades), "expires": self.t + 5.0,
                      "choices": [{"id": u["id"], "name": u["name"], "desc": u["desc"]} for u in ch]}

    def drops_step(self, dt, zone):
        dc = self.cfg.get("drops")
        if not dc:
            return
        for ct in dc["times"]:
            if self.t - dt < ct <= self.t:
                p = self.random_ground(zone["next_center"][0], zone["next_center"][1],
                                       max(5.0, zone["next_radius"] * dc.get("spread", 0.7)))
                self.drops[self._nid("drop")] = {"pos": p, "land_t": ct + dc["fall_s"], "progress": 0.0, "holder": None}
                self.emit("drop", p[0], p[1], -1)
        for did, d in list(self.drops.items()):
            if self.t < d["land_t"]:
                continue
            inside = [m for m in self.alive_mechs()
                      if math.hypot(m.pos[0] - d["pos"][0], m.pos[1] - d["pos"][1]) <= dc["radius"]]
            if len(inside) == 1:
                m = inside[0]
                if d["holder"] != m.id:
                    d["holder"], d["progress"] = m.id, 0.0
                d["progress"] += dt
                if d["progress"] >= dc["capture_s"]:
                    m.cubes += int(dc["cubes"])
                    m.hp = m.max_hp
                    m.energy = float(m.stats["energy"])
                    self.events[m.id].append({"type": "drop", "id": did, "cubes": m.cubes})
                    del self.drops[did]
            elif not inside:
                d["holder"], d["progress"] = None, 0.0

    # ---------------- main loop
    def run(self, bot, max_t=None):
        cfg = self.cfg
        dt = 1.0 / cfg["match"]["tick_hz"]
        every = int(cfg["match"]["decide_every"])
        max_t = max_t or cfg["match"]["duration_s"]
        ours = self.mechs[self.ours_id]
        act_ms = []
        while self.t < max_t:
            zone = self.zone()
            alive = self.alive_mechs()
            if len(alive) <= 1:
                break
            if self.tick % every == 0:
                hunters = set()
                if ours.alive:
                    houses = sorted((m for m in alive if not m.ours),
                                    key=lambda m: math.hypot(m.pos[0] - ours.pos[0], m.pos[1] - ours.pos[1]))
                    k = min(len(houses), 1 + int(self.t / self.hunt_every))
                    hunters = {m.id for m in houses[:k]}
                for m in alive:
                    if m.ours:
                        obs = self.build_obs(m, zone)
                        s = time.perf_counter()
                        try:
                            a = bot.act(obs)
                        except Exception as e:  # count, like a crash
                            import traceback
                            traceback.print_exc()
                            a = {}
                        act_ms.append((time.perf_counter() - s) * 1000)
                        self.apply_action(m, a)
                        if self.offer and self.t > self.offer["expires"]:
                            self.apply_upgrade(m, self.offer["choices"][0]["id"])
                            self.offer = None
                        self.make_offer(m)
                    else:
                        self.house_decide(m, ours, zone, hunters)
            self._dead_this_tick = []
            for m in alive:
                self.step_move(m, dt, zone)
                self.fire_weapons(m, dt)
            self.step_bullets(dt)
            self.npc_step(dt)
            for m in alive:
                if not m.alive:
                    continue
                if not self.in_circle(m.pos[0], m.pos[1], zone):
                    self.damage(m, zone["dps"] * dt, "zone", None, None)
                elif self.t - m.last_combat_t >= m.stats["regen_delay"] and m.hp < m.max_hp:
                    m.hp = min(m.max_hp, m.hp + m.stats["regen_per_s"] * dt)
                self.pick_cube(m)
            self.drops_step(dt, zone)
            if self._dead_this_tick:
                n_alive_before = len(alive)
                for m in self._dead_this_tick:
                    m.place = n_alive_before - len(self._dead_this_tick) + 1
            self.sounds = [s for s in self.sounds if self.t - s[0] < 0.2]
            self.t += dt
            self.tick += 1
        # survivors ranked by HP
        surv = sorted(self.alive_mechs(), key=lambda m: -m.hp)
        for i, m in enumerate(surv):
            m.place = i + 1
        return ours, act_ms


# ---------------------------------------------------------------- runner
def load_hello():
    with gzip.open(os.path.join(KIT, "check", "stream.jsonl.gz"), "rt") as f:
        hello = json.loads(f.readline())
    hello["map"]["dir"] = os.path.join(KIT, "arena")
    return hello


def load_bot_class(bot_dir, example=False):
    bot_dir = os.path.abspath(bot_dir)
    sys.path.insert(0, bot_dir)
    os.chdir(bot_dir)
    if example:
        src = open(os.path.join(bot_dir, "example_bot.py"), encoding="utf-8").read()
        src = src.replace("mf.run(Bot)", "")
        ns = {"__name__": "example_bot"}
        exec(compile(src, "example_bot.py", "exec"), ns)
        return ns["Bot"]
    import bot as B
    return B.Bot


def one_match(args):
    bot_dir, seed, example, kw = args
    import io
    import contextlib
    Bot = load_bot_class(bot_dir, example)
    import mechfight as mf
    import importlib.util
    spec = importlib.util.spec_from_file_location("sim_nav", os.path.join(ROOT, "nav.py"))
    sim_nav = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sim_nav)  # the simulator's own map code, whatever version the bot is
    Nav = sim_nav.Nav
    global Search
    Search = sim_nav.Search
    hello = load_hello()
    hello["slot"] = 0
    hello["seed"] = seed
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        nav = Nav(hello["map"]["dir"], hello["config"]["mech"])
        sim = Sim(hello["config"], nav, seed, **kw)
        hello["slot"] = sim.ours_id
        bot = Bot(hello)
        mf.draw.caps = hello["config"].get("debug", {})
        orig_act = bot.act

        def act(obs):
            mf.draw.clear()
            a = orig_act(obs) or {}
            mf.draw.take()
            return a
        bot.act = act
        ours, act_ms = sim.run(bot)
    errors = err.getvalue().count("Traceback")
    act_ms.sort()
    return {"seed": seed, "place": ours.place, "kills": ours.kills, "death_t": ours.death_t, "cause": ours.cause,
            "cubes": ours.cubes, "points": 16 - ours.place, "errors": errors,
            "act_p99": act_ms[int(len(act_ms) * 0.99)] if act_ms else 0, "act_max": act_ms[-1] if act_ms else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot-dir", default=ROOT)
    ap.add_argument("--example", action="store_true", help="run the kit's example bot (example_bot.py in --bot-dir)")
    ap.add_argument("--matches", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--hunt-every", type=float, default=25.0, help="a new house hunter joins every N s")
    ap.add_argument("--house-aim-deg", type=float, default=1.2, help="house bots' aim noise")
    ap.add_argument("--house-special", type=float, default=0.3, help="chance a house bot starts with lightning")
    ap.add_argument("--no-omni", action="store_true", help="house hunters only chase us after seeing us")
    a = ap.parse_args()
    kw = {"hunt_every": a.hunt_every, "house_aim_deg": a.house_aim_deg, "house_special": a.house_special,
          "omniscient": not a.no_omni}
    jobs = [(a.bot_dir, a.seed + i, a.example, kw) for i in range(a.matches)]
    t0 = time.time()
    if a.workers > 1:
        import multiprocessing as mp
        with mp.Pool(a.workers) as pool:
            res = pool.map(one_match, jobs)
    else:
        res = [one_match(j) for j in jobs]
    pts = [r["points"] for r in res]
    mean = sum(pts) / len(pts)
    sd = (sum((p - mean) ** 2 for p in pts) / max(1, len(pts) - 1)) ** 0.5
    print(f"matches {len(res)}  score {mean:.2f}  (+-{sd / max(1, len(pts)) ** 0.5:.2f})  "
          f"kills/match {sum(r['kills'] for r in res) / len(res):.2f}  "
          f"cubes {sum(r['cubes'] for r in res) / len(res):.1f}  "
          f"died at {sum((r['death_t'] or 480) for r in res) / len(res):.0f}s avg  "
          f"errors {sum(r['errors'] for r in res)}  act p99 {max(r['act_p99'] for r in res):.1f}ms  "
          f"max {max(r['act_max'] for r in res):.1f}ms  ({time.time() - t0:.0f}s)")
    causes = {}
    for r in res:
        causes[r["cause"] or "survived"] = causes.get(r["cause"] or "survived", 0) + 1
    print("  places:", sorted(r["place"] for r in res), " killed by:", causes)


if __name__ == "__main__":
    main()
