"""Brain 2: a simpler practice-mode pilot on top of our world model.

The old brain still runs first every decision (memory, events, upgrades, pickups, reloads, situation); against the
squad this brain then decides movement, boost, aim and triggers:

  legs, in order: zone run > hide below 25% HP (until 60%) > supply drop (hurt or short of cubes, nobody near it)
    > a known laser (run, any time we hold none) > another weapon / cube close by while nobody is close
    > fight: hold the preferred distance to the target, strafing across its line
    > early with nothing to do: head for the next circle's centre (loot shows up on the way)
  every step is then chosen among 12 headings around the wanted one: walkable, inside the zone, away from every
  enemy within its keep-out distance, out of the way of bullets in flight
  boost when in danger (hurt, out of the zone, a mech close while we hold a laser)
  aim: the target (nearest in our line of fire, HP a small tiebreak), else whoever hit us, else the nearest enemy,
    else a slow sweep
"""
import math

from bot import Bot
from config import *  # noqa: F401,F403
from nav import Search
from util import wrap
import mechfight as mf


def unit(dx, dy):
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-6 else (0.0, 0.0)


class Bot2(Bot):
    def __init__(self, hello):
        super().__init__(hello)
        self.b2 = {"goal": None, "path": None, "pidx": 0, "search": None, "side": 1.0 if hello.get("slot", 0) % 2 else -1.0,
                   "boosting": False, "healing": False, "cover": None, "cover_t": -1e9, "tgt": None,
                   "prog": None, "prog_t": 0.0, "jet_until": -1.0}

    # ---------- paths (own goal, own search)
    def _b2_goto(self, gx, gy, x, y, t_start):
        s = self.b2
        if s["goal"] is None or math.hypot(gx - s["goal"][0], gy - s["goal"][1]) > 4.0:
            s["goal"] = (gx, gy)
            s["path"], s["pidx"] = None, 0
            s["search"] = Search(self.nav, self.nav.cell_of(x, y), self.nav.cell_of(gx, gy))
        if s["search"] is not None and s["search"].step(t_start + 0.008):
            s["path"] = [self.nav.center(c) for c in s["search"].path]
            s["search"], s["pidx"] = None, 0
        if not s["path"]:
            return unit(gx - x, gy - y)
        p = s["path"]
        lo, hi = s["pidx"], min(len(p), s["pidx"] + 20)
        bi = min(range(lo, hi), key=lambda k: (p[k][0] - x) ** 2 + (p[k][1] - y) ** 2) if hi > lo else lo
        s["pidx"] = bi
        if (p[bi][0] - x) ** 2 + (p[bi][1] - y) ** 2 > 144.0:
            s["goal"] = None  # fell off: replan next time
        tx, ty = p[min(len(p) - 1, bi + 3)] if bi < len(p) - 4 else (gx, gy)
        return unit(tx - x, ty - y)

    # ---------- legs
    def _b2_steer(self, obs, me, want, keep_out):
        """Best of 12 headings around `want`: walkable, in the zone, away from enemies inside keep_out, out of bullets."""
        x, y, z = me["pos"]
        if want == (0.0, 0.0):
            return [0.0, 0.0]
        yaw = math.atan2(want[1], want[0])
        zone = obs["zone"]
        walk = me["stats"].get("walk_speed", 11.7)
        v = me.get("vel") or (0.0, 0.0, 0.0)
        foes = sorted((m for m in obs["mechs"] if m["kind"] == "mech" and (m.get("hp") or 1400) > 150),
                      key=lambda m: math.hypot(m["pos"][0] - x, m["pos"][1] - y))[:4]
        best, bmove = -1e18, [want[0], want[1]]
        for off in (0.0, 0.35, -0.35, 0.7, -0.7, 1.2, -1.2, 1.7, -1.7, 2.3, -2.3, math.pi):
            dx, dy = math.cos(yaw + off), math.sin(yaw + off)
            if not self._cm_walk(x, y, dx, dy, 6.0):
                continue
            fx, fy = x + dx * 10.0, y + dy * 10.0
            s = math.cos(off)
            s -= 0.3 * max(0.0, math.hypot(fx - zone["center"][0], fy - zone["center"][1]) - max(0.0, zone["radius"] - 5.0))
            s += 0.15 * (dx * v[0] + dy * v[1]) / walk
            for m in foes:
                dd = math.hypot(fx - m["pos"][0], fy - m["pos"][1])
                s -= 8.0 * max(0.0, 1.0 - dd / keep_out) ** 2
            for p in obs["projectiles"][:30]:
                rx, ry = p["pos"][0] - x, p["pos"][1] - y
                vx, vy = p["vel"][0] - dx * walk, p["vel"][1] - dy * walk
                tt = -(rx * vx + ry * vy) / max(1.0, vx * vx + vy * vy)
                if 0.0 < tt < 0.6:
                    gap = math.hypot(rx + vx * tt, ry + vy * tt)
                    pz = p["pos"][2] + p["vel"][2] * tt
                    rad = me["stats"].get("radius", 2.5)
                    if gap < rad + 2.0 and z <= pz <= z + me["stats"].get("height", 9.0):
                        s -= (rad + 2.0 - gap) * 0.35
            if s > best:
                best, bmove = s, [dx, dy]
        return bmove

    def act(self, obs):
        import time
        t0 = time.perf_counter()
        act = super().act(obs)
        if not (B2 and getattr(self, "all_in", False) and self.nav is not None):
            return act
        # our own searches share the decision's budget with the old brain: everything done by t0 + 28 ms
        t_start = min(time.perf_counter(), t0 + 0.016)
        me, zone, t = obs["self"], obs["zone"], obs["t"]
        x, y, z = me["pos"]
        s = self.b2
        hpf = me["hp"] / max(1.0, me["max_hp"])
        weapons = me["weapons"]
        kinds = {w["kind"]: (i, w) for i, w in enumerate(weapons) if w}
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        nearest = min((math.hypot(m["pos"][0] - x, m["pos"][1] - y) for m in mechs), default=1e9)
        close3 = sum(1 for m in mechs if math.hypot(m["pos"][0] - x, m["pos"][1] - y) < 100) >= 3
        if hpf >= B2_HEAL_TO:
            s["healing"], s["cover"] = False, None
        elif hpf < B2_HEAL_BELOW:
            s["healing"] = True
        healing = s["healing"] and nearest > 55

        # ---- target: nearest in our line of fire, HP a small tiebreak; drones only when no mech is near
        cands = []
        for m in obs["mechs"]:
            d = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
            if m["kind"] == "npc" and (nearest < 90 or d > 65):
                continue
            if not self.can_hit(me, m):
                continue
            v = d + B2_HP_W * (m.get("hp") or 1400.0)
            if m["kind"] == "npc":
                v += 45.0
            if m["id"] == s["tgt"]:
                v -= 18.0
            if (m.get("hp") or 1400.0) < 300:
                v -= 40.0
            cands.append((v, m))
        tgt = min(cands, key=lambda c: c[0])[1] if cands else None
        s["tgt"] = tgt["id"] if tgt else None

        # ---- legs
        laser = kinds.get("laser")
        keep_out = 30.0 if "lightning" in kinds else 110.0 if laser else 75.0
        if healing:
            keep_out += 25.0
        mode = "B2"
        zg = self.zone_must(me, zone, t) if ZONE_FIRST else None
        goal = None
        fight_dir = None
        if zg is not None:
            goal, mode = zg, "B2ZONE"
        elif healing and mechs:
            if s["cover"] is None or t - s["cover_t"] > 2.0:
                eyes = [(m["pos"][0], m["pos"][1], m["pos"][2] + 8.0) for m in mechs]
                c = self.nav.flank_cell(eyes, None, x, y, 60.0, t_start + 0.012)
                s["cover"] = self.nav.center(c) if c >= 0 else None
                s["cover_t"] = t
            if s["cover"] is not None:
                goal, mode = s["cover"], "B2HIDE"
        if goal is None:
            hd = self.heal_drop_goal(obs, me, t) if (hpf < 0.85 or me.get("cubes", 0) < 10) and \
                (HEAL_DROP_ANY or tgt is not None or hpf < 0.85) else None
            if hd is not None:
                goal, mode = hd[0], "B2DROP"
        if goal is None and LASER_RUN:
            lr = self.laser_run_goal(obs, me, t)
            if lr is not None:
                goal, mode = lr, "B2LASER"
        if goal is None and nearest > 35:
            # another weapon (empty slot) or a cube close by
            from loot import weapon_take
            best, bd = None, 1e9
            for i, it in self.mem.items.items():
                if i in self.mem.banned or abs(it["z"] - z) > 4.0:
                    continue
                if it["kind"] == "pickup" and weapon_take(it.get("wkind"), weapons, False, False) is not None:
                    d = math.hypot(it["x"] - x, it["y"] - y) / (1.8 if it.get("wkind") == "laser" else 1.0)
                    if d < 80.0 and d < bd and self.mem.reachable_cell(it) >= 0:
                        best, bd = (it["x"], it["y"]), d
            if best is None and me.get("cubes", 0) < 10 and nearest > 40:
                rng = 140.0 if nearest > 80 else 80.0
                for i, it in self.mem.items.items():
                    if it["kind"] == "cube" and abs(it["z"] - z) < 4.0:
                        d = math.hypot(it["x"] - x, it["y"] - y)
                        if d < rng and d < bd:
                            best, bd = (it["x"], it["y"]), d
            if best is not None:
                goal, mode = best, "B2LOOT"
        if goal is None and tgt is not None and tgt["kind"] == "mech":
            ex, ey = tgt["pos"][0], tgt["pos"][1]
            d = math.hypot(ex - x, ey - y)
            ux, uy = unit(ex - x, ey - y)
            pref = (35.0 if "lightning" in kinds and me["energy"] > 20 else 90.0 if laser else 60.0) * B2_RANGE_K
            if laser and "lightning" in (tgt.get("weapons") or []):
                pref = 110.0
            if hpf < 0.35 or close3:
                pref += 40.0
            radial = max(-1.3, min(1.2, (d - pref) / 25.0))
            strafe = s["side"] * (1.0 if int(t / B2_FLIP_S) % 2 == 0 else -1.0)
            fight_dir = unit(ux * radial - uy * strafe, uy * radial + ux * strafe)
            mode = "B2FIGHT"
        elif goal is None and tgt is not None:
            goal, mode = (tgt["pos"][0], tgt["pos"][1]), "B2NPC"
        if goal is None and fight_dir is None and nearest > 80 and \
                (len(kinds) < 2 or (t < 45.0 and me.get("cubes", 0) < 3)):
            goal, mode = tuple(zone["next_center"]), "B2EXPLORE"
        if fight_dir is not None:
            want = fight_dir
        elif goal is not None and math.hypot(goal[0] - x, goal[1] - y) > 2.0:
            want = self._b2_goto(goal[0], goal[1], x, y, t_start)
        else:
            want = (0.0, 0.0)
        move = self._b2_steer(obs, me, want, keep_out)

        # stuck: no progress for 3 s -> jump and jet over it
        jump = False
        if s["prog"] is None or want == (0.0, 0.0):
            s["prog"], s["prog_t"] = (x, y), t
        elif t - s["prog_t"] >= 3.0:
            if math.hypot(x - s["prog"][0], y - s["prog"][1]) < 4.0 and me["grounded"] and me["energy"] > 35:
                jump, s["jet_until"], s["goal"] = True, t + 4.0, None
            s["prog"], s["prog_t"] = (x, y), t
        danger = not me["in_zone"] or hpf < 0.55 or (laser is not None and nearest < 65)
        s["boosting"] = danger and me["energy"] > (20 if s["boosting"] else 65)
        boost = s["boosting"] or (t < s["jet_until"] and me["energy"] > 10) or \
            (mode in ("B2LASER", "B2DROP", "B2ZONE") and me["energy"] > 25)

        # ---- aim and triggers
        fire = [False, False]
        if tgt is not None:
            is_mech = tgt["kind"] == "mech"
            scale = 1.0 + self.size_per_cube * tgt.get("cubes", 0) if is_mech else 1.0
            size = 2.5 * scale if is_mech else 1.8
            zoff = self.hit_zoff(me, tgt)
            charging = laser is not None and laser[1].get("charge", 0) > 0
            speed = 0.0 if (charging or "gun" not in kinds) else self._gun_speed(me)
            aim, h = self.aim_at(me, tgt["pos"], tgt["vel"], zoff if zoff is not None else 4.5 * scale, speed, 1.0)
            if zoff is not None:
                fire = self.fire(me, h, aim, size, is_mech, True, True)
                if LASER_KILLSHOT and is_mech:
                    fire = self.laser_killshot(me, tgt, aim, h, size, fire)
                if not me["in_zone"] and laser is not None and laser[1].get("charge", 0) == 0:
                    fire[laser[0]] = False  # don't start a half-speed charge outside the zone
        elif t - self.dmg_bearing[0] < 2.0:
            aim = [self.dmg_bearing[1], 0.0]
        elif mechs:
            w = min(mechs, key=lambda m: math.hypot(m["pos"][0] - x, m["pos"][1] - y))
            aim = [math.atan2(w["pos"][1] - y, w["pos"][0] - x), 0.0]
        else:
            aim = [me["aim"][0] + 0.3, 0.0]
        if healing and tgt is None:
            fire = [False, False]
        # a charging laser with no shot keeps holding (released into a wall otherwise)
        if laser is not None and laser[1].get("charge", 0) > 0 and tgt is None and t - self.dmg_bearing[0] < 1.5:
            fire[laser[0]] = True
        act.update(move=move, boost=boost, jump=jump, aim=aim, fire=fire)
        mf.draw.label(f"B2 {mode}")
        return act


if __name__ == "__main__":
    mf.run(Bot2)
