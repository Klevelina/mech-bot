"""Mech bot: loot early (cubes, crates, drones, weapons), then hold cover inside the next zone circle.
Shoots the nearest visible mech (unchanged from the example, apart from using the right weapon)."""
import math
import time
import traceback
from collections import deque

import mechfight as mf
from nav import Nav, Search
from loot import Memory, pick_upgrade, weapon_take

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
SLICE_S = 0.020        # A* time per decision


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class Bot:
    name = "mech-loot"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.cfg = cfg
        self.reach = cfg["loot"]["pickup_radius"]
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
        self.enemies = {}       # id -> (t, x, y)
        self.target = None      # loot item id
        self.target_since = 0.0
        self.retarget_t = -1e9

    # ---------- goal selection
    def choose_cover(self, mx, my, zone):
        nav = self.nav
        nc, nr = zone["next_center"], zone["next_radius"]
        R = nr - max(8.0, 0.25 * nr)
        if nav is None:
            return (nc[0], nc[1])
        if R < 15:
            return nav.center(nav.nearest_main(nc[0], nc[1]))
        best, bs = None, 1e18
        R2 = R * R
        for c, x, y in nav.cover:
            dx, dy = x - nc[0], y - nc[1]
            d2 = dx * dx + dy * dy
            if d2 > R2 or c in self.bad:
                continue
            s = math.hypot(x - mx, y - my) + 0.6 * math.sqrt(d2)
            if s < bs:
                best, bs = c, s
        if best is None:
            best = nav.nearest_main(nc[0], nc[1])
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

    def danger(self, ix, iy, t):
        for et, ex, ey in self.enemies.values():
            if t - et < DANGER_S and (ex - ix) ** 2 + (ey - iy) ** 2 < DANGER_R ** 2:
                return True
        return False

    # ---------- aiming / firing
    def aim_at(self, me, pos, vel, zoff, speed):
        mx, my, mz = me["pos"]
        mz += me["stats"].get("muzzle_height", 7)
        px, py, pz = pos
        vx, vy, vz = vel if vel else (0.0, 0.0, 0.0)
        tt = 0.0
        for _ in range(3):
            ax, ay, az = px + vx * tt, py + vy * tt, pz + zoff + vz * tt
            d = math.sqrt((ax - mx) ** 2 + (ay - my) ** 2 + (az - mz) ** 2)
            tt = d / speed if speed else 0.0
        h = math.hypot(ax - mx, ay - my)
        st = me["stats"]
        pitch = max(st.get("min_pitch", -0.7), min(st.get("max_pitch", 1.3), math.atan2(az - mz, h)))
        return [math.atan2(ay - my, ax - mx), pitch], h

    def fire(self, me, dist, aim, size, is_mech, late=False):
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
                out[i] = err < tol * 1.5 and dist < st.get("range", 230) and w.get("ammo", 1) > 0
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
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        for m in mechs:
            self.enemies[m["id"]] = (t, m["pos"][0], m["pos"][1])
        if mechs:
            self.threat_t = t
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None and e["by"] < 100:
                self.threat_t = t
        fighting = t - self.threat_t < 3.0
        late = obs["alive"] <= 5 or zone["next_radius"] <= 70

        # ---- cover goal for the current circle
        key = (round(zone["next_center"][0], 1), round(zone["next_center"][1], 1), round(zone["next_radius"], 1))
        if key != self.zone_key:
            self.zone_key = key
            self.cover = self.choose_cover(x, y, zone)

        # ---- loot target
        target = None
        if LOOT and t - self.threat_t > SAFE_AFTER:
            if self.target is not None and (self.target not in self.mem.items or t - self.target_since > 40):
                if self.target in self.mem.items:
                    self.mem.banned.add(self.target)  # took too long
                self.target = None
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
        if target is not None:
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
        else:
            self.set_goal(self.cover, x, y)
            dgoal = math.hypot(self.cover[0] - x, self.cover[1] - y)

        # ---- path search slice
        if self.search is not None and self.search.step(t_start + SLICE_S):
            s = self.search
            self.search = None
            self.path = [self.nav.center(c) for c in s.path]
            self.pidx = 0
            if not s.reached:
                mf.log(f"t={t:.1f} goal unreachable, partial path {len(s.path)}")
                if target is not None:
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

        # ---- stuck detection
        self.hist.append((t, x, y))
        jump = False
        boost = not me["in_zone"]
        if self.unstick > 0:
            self.unstick -= 1
            boost = True
            jump = me["grounded"]
        elif moving and len(self.hist) == self.hist.maxlen:
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
        else:
            yaw = math.atan2(zone["center"][1] - y, zone["center"][0] - x)
            if SWEEP and not fighting:
                yaw += 1.0 * math.sin(t * 0.5)
        act.update(move=move, boost=boost, jump=jump, aim=[yaw, 0.0])

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

        # ---- fight: nearest visible mech (aim as in the example)
        if mechs:
            e = min(mechs, key=lambda m: math.dist(m["pos"], me["pos"]))["pos"]
            aim = [math.atan2(e[1] - y, e[0] - x), math.atan2(e[2] - z - 2.5, math.dist(e[:2], (x, y)))]
            act["aim"] = aim
            act["fire"] = self.fire(me, math.dist(e[:2], (x, y)), aim, 2.5, True, late)
            mf.draw.line([x, y, z + 7], [e[0], e[1], e[2] + 4.5], "#ff5040")

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
        what = f"{target['kind']}" if target else "cover"
        mf.draw.label(f"hp {me['hp']:.0f} {what} d{dgoal:.0f} cubes {me['cubes']:.0f}"
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
