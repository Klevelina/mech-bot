"""Brain 3: practice-mode pilot built around picking fights and fighting them at long range.

Like brain 2 it runs on top of the old brain's world model (memory, events, upgrades, pickups, reloads, situation);
against the squad it decides movement, boost, aim and triggers:

  who we fight: a mech that hit us lately, charges a laser at us, walks at us or aims at us (within B3_COMING_R),
    is nearly dead close by, or is simply too close - the rest are bystanders: never shot first, kept at B3_AVOID_R
  how: hold B3_BAND (long range) to the target, strafing across its line; inside B3_KITE_R back off on boost
  legs, in order: zone run > supply drop (hurt or short of cubes) > a known laser > a weapon / cube close by while
    nobody is near > fight > early with nothing to do: toward the next circle's centre
  every step: best of 12 headings around the wanted one - walkable, in the zone, away from bystanders and lightning
    carriers, out of the way of bullets in flight
"""
import math
import time

from brain2 import Bot2, unit
from config import *  # noqa: F401,F403
from loot import weapon_take
import mechfight as mf


class Bot3(Bot2):
    def _b3_steer(self, obs, me, want, repel):
        """Best of 12 headings around `want`; repel = [(x, y, radius, weight)]."""
        x, y, z = me["pos"]
        if want == (0.0, 0.0):
            return [0.0, 0.0]
        yaw = math.atan2(want[1], want[0])
        zone = obs["zone"]
        walk = me["stats"].get("walk_speed", 11.7)
        v = me.get("vel") or (0.0, 0.0, 0.0)
        rad = me["stats"].get("radius", 2.5)
        best, bmove = -1e18, [want[0], want[1]]
        for off in (0.0, 0.35, -0.35, 0.7, -0.7, 1.2, -1.2, 1.7, -1.7, 2.3, -2.3, math.pi):
            dx, dy = math.cos(yaw + off), math.sin(yaw + off)
            if not self._cm_walk(x, y, dx, dy, 6.0):
                continue
            fx, fy = x + dx * 10.0, y + dy * 10.0
            s = math.cos(off)
            s -= 0.3 * max(0.0, math.hypot(fx - zone["center"][0], fy - zone["center"][1]) - max(0.0, zone["radius"] - 5.0))
            s += 0.15 * (dx * v[0] + dy * v[1]) / walk
            for qx, qy, r, w in repel:
                dd = math.hypot(fx - qx, fy - qy)
                s -= w * max(0.0, 1.0 - dd / r) ** 2
            for p in obs["projectiles"][:30]:
                rx, ry = p["pos"][0] - x, p["pos"][1] - y
                vx, vy = p["vel"][0] - dx * walk, p["vel"][1] - dy * walk
                tt = -(rx * vx + ry * vy) / max(1.0, vx * vx + vy * vy)
                if 0.0 < tt < 0.8:
                    gap = math.hypot(rx + vx * tt, ry + vy * tt)
                    pz = p["pos"][2] + p["vel"][2] * tt
                    if gap < rad + 2.0 and z <= pz <= z + me["stats"].get("height", 9.0):
                        s -= (rad + 2.0 - gap) * 0.35
            if s > best:
                best, bmove = s, [dx, dy]
        return bmove

    def _coming(self, me, m, t):
        x, y = me["pos"][0], me["pos"][1]
        dx, dy = x - m["pos"][0], y - m["pos"][1]
        d = math.hypot(dx, dy) or 1.0
        if t - self.attackers.get(m["id"], -1e9) < 6.0 or d < B3_CLOSE_R:
            return True
        if (m.get("hp") or 1400.0) < 300.0 and d < 120.0:
            return True
        if d < B3_COMING_R:
            if (m["vel"][0] * dx + m["vel"][1] * dy) / d > 3.0:
                return True
            if abs(math.remainder(m["aim"][0] - math.atan2(dy, dx), 2 * math.pi)) < 0.22:
                return True
        return False

    def act(self, obs):
        t0 = time.perf_counter()
        act = Bot2.__mro__[1].act(self, obs)  # the old brain's bookkeeping (skip brain 2's decisions)
        if not (B3 and getattr(self, "all_in", False) and self.nav is not None):
            return act
        t_start = min(time.perf_counter(), t0 + 0.016)
        me, zone, t = obs["self"], obs["zone"], obs["t"]
        x, y, z = me["pos"]
        s = self.b2
        hpf = me["hp"] / max(1.0, me["max_hp"])
        weapons = me["weapons"]
        kinds = {w["kind"]: (i, w) for i, w in enumerate(weapons) if w}
        laser = kinds.get("laser")
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        nearest = min((math.hypot(m["pos"][0] - x, m["pos"][1] - y) for m in mechs), default=1e9)
        coming = [m for m in mechs if self._coming(me, m, t)]
        bystanders = [m for m in mechs if m not in coming]

        # ---- target: nearest coming mech in our line of fire; drones only with no mech near
        cands = []
        for m in coming:
            if not self.can_hit(me, m):
                continue
            d = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
            v = d + 0.045 * (m.get("hp") or 1400.0) - (18.0 if m["id"] == s["tgt"] else 0.0)
            cands.append((v, m))
        if not cands and nearest > 90:
            for m in obs["mechs"]:
                if m["kind"] == "npc" and math.hypot(m["pos"][0] - x, m["pos"][1] - y) < 65 and self.can_hit(me, m):
                    cands.append((math.hypot(m["pos"][0] - x, m["pos"][1] - y), m))
        tgt = min(cands, key=lambda c: c[0])[1] if cands else None
        s["tgt"] = tgt["id"] if tgt else None

        # ---- repulsors: bystanders at B3_AVOID_R, lightning carriers at 53 m
        repel = [(m["pos"][0], m["pos"][1], B3_AVOID_R, B3_AVOID_W) for m in bystanders]
        sparks = [m for m in mechs if "lightning" in (m.get("weapons") or [])]
        repel += [(m["pos"][0], m["pos"][1], 53.0, 12.0) for m in sparks]

        # ---- legs
        mode = "B3"
        goal, fight_dir = None, None
        zg = self.zone_must(me, zone, t) if ZONE_FIRST else None
        if zg is not None:
            goal, mode = zg, "B3ZONE"
        if goal is None and (hpf < 0.85 or me.get("cubes", 0) < 10):
            hd = self.heal_drop_goal(obs, me, t)
            if hd is not None:
                goal, mode = hd[0], "B3DROP"
        if goal is None and LASER_RUN:
            lr = self.laser_run_goal(obs, me, t)
            if lr is not None:
                goal, mode = lr, "B3LASER"
        if goal is None and (not coming or nearest > 60):
            best, bd = None, 1e9
            for i, it in self.mem.items.items():
                if i in self.mem.banned or abs(it["z"] - z) > 4.0:
                    continue
                if it["kind"] == "pickup" and weapon_take(it.get("wkind"), weapons, False, False) is not None:
                    d = math.hypot(it["x"] - x, it["y"] - y) / (1.8 if it.get("wkind") == "laser" else 1.0)
                    if d < 80.0 and d < bd and self.mem.reachable_cell(it) >= 0:
                        best, bd = (it["x"], it["y"]), d
                elif it["kind"] == "pile" and nearest > 40:
                    d = math.hypot(it["x"] - x, it["y"] - y) * 0.5
                    if d < 70.0 and d < bd:
                        best, bd = (it["x"], it["y"]), d
                elif it["kind"] == "cube" and me.get("cubes", 0) < 10 and nearest > 40:
                    d = math.hypot(it["x"] - x, it["y"] - y)
                    if d < (140.0 if nearest > 80 else 80.0) and d < bd:
                        best, bd = (it["x"], it["y"]), d
            if best is not None:
                goal, mode = best, "B3LOOT"
        if goal is None and tgt is not None and tgt["kind"] == "mech":
            ex, ey = tgt["pos"][0], tgt["pos"][1]
            d = math.hypot(ex - x, ey - y)
            ux, uy = unit(ex - x, ey - y)
            lo, hi = (25.0, 40.0) if ("lightning" in kinds and not laser and me["energy"] > 20) else B3_BAND
            if hpf < 0.35:
                lo, hi = lo + 30.0, hi + 30.0
            radial = 1.2 if d > hi else (-1.3 if d < lo else 0.0)
            if lo <= d <= hi:
                radial = (d - (lo + hi) / 2.0) / 60.0
            strafe = s["side"] * (1.0 if int(t / B2_FLIP_S) % 2 == 0 else -1.0)
            fight_dir = unit(ux * radial - uy * strafe, uy * radial + ux * strafe)
            mode = "B3FIGHT"
        elif goal is None and tgt is not None:
            goal, mode = (tgt["pos"][0], tgt["pos"][1]), "B3NPC"
        if goal is None and fight_dir is None and (nearest > 80 or not laser) and \
                (len(kinds) < 2 or (t < 45.0 and me.get("cubes", 0) < 3)):
            goal, mode = tuple(zone["next_center"]), "B3EXPLORE"
        if fight_dir is not None:
            want = fight_dir
        elif goal is not None and math.hypot(goal[0] - x, goal[1] - y) > 2.0:
            want = self._b2_goto(goal[0], goal[1], x, y, t_start)
        else:
            want = (0.0, 0.0)
        move = self._b3_steer(obs, me, want, repel)

        jump = False
        if s["prog"] is None or want == (0.0, 0.0):
            s["prog"], s["prog_t"] = (x, y), t
        elif t - s["prog_t"] >= 3.0:
            if math.hypot(x - s["prog"][0], y - s["prog"][1]) < 4.0 and me["grounded"] and me["energy"] > 35:
                jump, s["jet_until"], s["goal"] = True, t + 4.0, None
            s["prog"], s["prog_t"] = (x, y), t
        kite = tgt is not None and tgt["kind"] == "mech" and fight_dir is not None and \
            math.hypot(tgt["pos"][0] - x, tgt["pos"][1] - y) < B3_KITE_R
        spark_close = any(math.hypot(m["pos"][0] - x, m["pos"][1] - y) < 53.0 for m in sparks)
        danger = not me["in_zone"] or hpf < 0.55 or kite or spark_close
        s["boosting"] = danger and me["energy"] > (20 if s["boosting"] else 50)
        boost = s["boosting"] or (t < s["jet_until"] and me["energy"] > 10) or \
            (mode in ("B3LASER", "B3DROP", "B3ZONE") and me["energy"] > 25)

        # ---- aim and triggers (same as brain 2)
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
                    fire[laser[0]] = False
        elif t - self.dmg_bearing[0] < 2.0:
            aim = [self.dmg_bearing[1], 0.0]
        elif coming:
            w = min(coming, key=lambda m: math.hypot(m["pos"][0] - x, m["pos"][1] - y))
            aim = [math.atan2(w["pos"][1] - y, w["pos"][0] - x), 0.0]
        elif mechs:
            w = min(mechs, key=lambda m: math.hypot(m["pos"][0] - x, m["pos"][1] - y))
            aim = [math.atan2(w["pos"][1] - y, w["pos"][0] - x), 0.0]
        else:
            aim = [me["aim"][0] + 0.3, 0.0]
        if laser is not None and laser[1].get("charge", 0) > 0 and tgt is None and t - self.dmg_bearing[0] < 1.5:
            fire[laser[0]] = True
        act.update(move=move, boost=boost, jump=jump, aim=aim, fire=fire)
        mf.draw.label(f"B3 {mode}")
        return act


if __name__ == "__main__":
    mf.run(Bot3)
