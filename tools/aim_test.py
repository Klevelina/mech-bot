"""Offline aim test: how often do our gun and laser hit a moving mech?

Runs the bot's REAL aiming and firing code (fight.FightMixin.aim_at / fire, aim.AimLearner, the HOLD_DODGY and
LASER_LEAD_S rules from bot.py) against a simulated target, with the game's real numbers from the recorded hello:
20 ticks/s, a decision every 2 ticks, torso slewing at turn_rate / pitch_rate, gun bullets at 252 m/s with a
Gaussian spread, magazine + reload, laser charge / release / cooldown, mechs accelerating at ground_accel.
No buildings: open ground, line of fire always clear. The shooter stands still.

Target behaviours (guesses at how bots move - the real house bots are unknown):
  still      standing
  cross      walking sideways across our view, straight
  strafe     walking sideways, reversing every 1-2 s
  jink       walking, a new random direction every 0.3-1.0 s
  boostjink  the same at boost speed
  approach   walking at us with a slight zigzag (a hunter coming in)

Usage:
  python tools/aim_test.py                     # table for gun and laser
  python tools/aim_test.py --set LEAD=False    # override any config switch the fight code reads
  python tools/aim_test.py --trials 40 --secs 15 --weapons gun
"""
import argparse
import gzip
import json
import math
import os
import random
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fight  # noqa: E402
from aim import AimLearner  # noqa: E402
from util import wrap  # noqa: E402

DT = 0.05
BEHAVIOURS = ("still", "cross", "strafe", "jink", "boostjink", "approach")
DISTS = (30, 60, 100, 150, 200)


def load_cfg():
    with gzip.open(os.path.join(ROOT, "mechfight-kit", "check", "stream.jsonl.gz"), "rt") as f:
        return json.loads(f.readline())["config"]


class Shooter(fight.FightMixin):
    """Just enough of the bot for the fight code's aiming / firing methods."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.nav = None
        self.gravity = cfg["mech"]["gravity"]
        self.size_per_cube = cfg["cubes"]["size_per_cube"]
        self.aim = AimLearner()
        self.lit_near = False


class Target:
    def __init__(self, cfg, beh, dist, rng):
        m = cfg["mech"]
        self.beh, self.rng = beh, rng
        self.walk, self.boost, self.acc = m["walk_speed"], m["boost_speed"], m["ground_accel"]
        a = rng.uniform(-math.pi, math.pi)  # where it stands around us
        self.pos = [dist * math.cos(a), dist * math.sin(a), 0.0]
        self.vel = [0.0, 0.0, 0.0]
        self.dist0 = dist
        self.cmd = (0.0, 0.0)
        self.switch_t = 0.0
        self.side = rng.choice((-1, 1))
        if beh == "cross":
            self.cmd = self._side_dir(self.walk)

    def _side_dir(self, speed):
        x, y = self.pos[0], self.pos[1]
        d = math.hypot(x, y) or 1.0
        # sideways to our line of sight, plus a pull back to its starting distance (stays in the test band)
        px, py = -y / d * self.side, x / d * self.side
        pull = max(-0.5, min(0.5, (d - self.dist0) / 20.0))
        vx, vy = px - x / d * pull, py - y / d * pull
        n = math.hypot(vx, vy)
        return vx / n * speed, vy / n * speed

    def step(self, t):
        b, rng = self.beh, self.rng
        if b == "cross":
            self.cmd = self._side_dir(self.walk)
        elif b == "strafe":
            if t >= self.switch_t:
                self.side = -self.side
                self.switch_t = t + rng.uniform(1.0, 2.0)
            self.cmd = self._side_dir(self.walk)
        elif b in ("jink", "boostjink"):
            if t >= self.switch_t:
                self.switch_t = t + rng.uniform(0.3, 1.0)
                self.jink_a = rng.uniform(-math.pi, math.pi)
            sp = self.boost if b == "boostjink" else self.walk
            x, y = self.pos[0], self.pos[1]
            d = math.hypot(x, y) or 1.0
            pull = max(-0.6, min(0.6, (d - self.dist0) / 20.0))
            vx, vy = math.cos(self.jink_a) - x / d * pull, math.sin(self.jink_a) - y / d * pull
            n = math.hypot(vx, vy) or 1.0
            self.cmd = (vx / n * sp, vy / n * sp)
        elif b == "approach":
            x, y = self.pos[0], self.pos[1]
            d = math.hypot(x, y) or 1.0
            zig = 0.5 * math.sin(t * 2.0)
            ux, uy = -x / d, -y / d
            vx, vy = ux - uy * zig, uy + ux * zig
            n = math.hypot(vx, vy)
            self.cmd = (vx / n * self.walk, vy / n * self.walk)
            if d < 15.0:
                self.pos[0], self.pos[1] = x / d * self.dist0, y / d * self.dist0
        # accelerate toward the commanded velocity
        dvx, dvy = self.cmd[0] - self.vel[0], self.cmd[1] - self.vel[1]
        dv = math.hypot(dvx, dvy)
        mx = self.acc * DT
        if dv > mx:
            dvx, dvy = dvx / dv * mx, dvy / dv * mx
        self.vel[0] += dvx
        self.vel[1] += dvy
        self.pos[0] += self.vel[0] * DT
        self.pos[1] += self.vel[1] * DT


def seg_hits_cyl(p0, p1, c, r, h):
    """Does segment p0->p1 pass through the vertical cylinder at c (feet), radius r, height h?"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    fx, fy = p0[0] - c[0], p0[1] - c[1]
    a = dx * dx + dy * dy
    b = 2 * (fx * dx + fy * dy)
    cc = fx * fx + fy * fy - r * r
    if a < 1e-12:
        return False
    disc = b * b - 4 * a * cc
    if disc < 0:
        return False
    sq = math.sqrt(disc)
    for s in ((-b - sq) / (2 * a), (-b + sq) / (2 * a)):
        if 0.0 <= s <= 1.0:
            z = p0[2] + s * (p1[2] - p0[2])
            if c[2] <= z <= c[2] + h:
                return True
    if cc <= 0:  # started inside the circle
        z = p0[2]
        return c[2] <= z <= c[2] + h
    return False


def run_trial(cfg, weapon, beh, dist, secs, rng):
    mech = cfg["mech"]
    wst = dict(cfg["weapons"][weapon])
    sh = Shooter(cfg)
    tg = Target(cfg, beh, dist, rng)
    r, hgt = mech["radius"], mech["height"]
    aim = [rng.uniform(-math.pi, math.pi), 0.0]  # actual torso aim
    cmd_aim = list(aim)
    trig = False
    cooldown, ammo, reload_left = 0.0, wst.get("mag", 12), 0.0
    charge, held_full = 0.0, 0.0
    bullets = []  # [pos, vel, travelled]
    shots = hits = 0
    dmg = 0.0
    spread = math.radians(wst.get("spread_deg", 0.0))
    stats = {"muzzle_height": mech["muzzle_height"], "min_pitch": mech["min_pitch"], "max_pitch": mech["max_pitch"]}
    n_ticks = int(secs / DT)
    for tick in range(n_ticks):
        t = tick * DT
        if tick % 2 == 0:  # ---- decision (the bot's code path from bot.py's fight block)
            w = {"kind": weapon, "stats": wst, "cooldown": max(0.0, cooldown)}
            if weapon == "gun":
                w.update(ammo=ammo, reload=reload_left)
            else:
                w.update(charge=charge)
            me = {"pos": [0.0, 0.0, 0.0], "aim": list(aim), "stats": stats, "weapons": [w], "energy": 100.0}
            m = {"id": 5, "kind": "mech", "pos": list(tg.pos), "vel": list(tg.vel), "aim": [0.0, 0.0],
                 "hp": 1400, "cubes": 0}
            sh.aim.observe(t, [m])
            laser_charging = weapon == "laser" and charge > 0
            speed = 0.0 if (laser_charging or not fight.LEAD or weapon != "gun") else wst["speed"]
            k = sh.aim.lead(5) if fight.LEARN_AIM else 1.0
            tpos = m["pos"]
            if fight.LASER_LEAD_S and speed == 0.0 and laser_charging:
                tpos = [m["pos"][0] + m["vel"][0] * fight.LASER_LEAD_S,
                        m["pos"][1] + m["vel"][1] * fight.LASER_LEAD_S, m["pos"][2]]
            cmd_aim, h = sh.aim_at(me, tpos, m["vel"], 4.5, speed, k)
            tt = h / speed if speed else 0.0
            gun_ok = True
            if fight.HOLD_DODGY and speed and h > 60:
                gun_ok = sh.aim.surprise(5, tt) < 2.5 * 1.5
            trig = sh.fire(me, h, cmd_aim, 2.5, True, False, gun_ok)[0]
            if weapon == "gun" and trig and speed:
                sh.aim.record(t, m, tt)
        # ---- server tick: torso slews, weapons fire, things move
        dyaw = wrap(cmd_aim[0] - aim[0])
        my = mech["turn_rate"] * DT
        aim[0] = wrap(aim[0] + max(-my, min(my, dyaw)))
        mp = mech["pitch_rate"] * DT
        aim[1] += max(-mp, min(mp, cmd_aim[1] - aim[1]))
        cooldown -= DT
        if weapon == "gun":
            if reload_left > 0:
                reload_left -= DT
                if reload_left <= 0:
                    ammo, reload_left = wst["mag"], 0.0
            elif trig and cooldown <= 0 and ammo > 0:
                yaw = aim[0] + rng.gauss(0, spread)
                pit = aim[1] + rng.gauss(0, spread)
                d = (math.cos(pit) * math.cos(yaw), math.cos(pit) * math.sin(yaw), math.sin(pit))
                o = [3.0 * d[0], 3.0 * d[1], mech["muzzle_height"] + 3.0 * d[2]]
                bullets.append([o, [d[0] * wst["speed"], d[1] * wst["speed"], d[2] * wst["speed"]], 0.0])
                shots += 1
                ammo -= 1
                cooldown = wst["cooldown"]
                if ammo == 0:
                    reload_left = wst["reload"]
        else:  # laser
            full = wst["charge"]
            fire_now = False
            if cooldown <= 0:
                if trig:
                    charge = min(full, charge + DT)
                    if charge >= full:
                        held_full += DT
                        if held_full >= wst["hold_s"]:
                            fire_now = True
                elif charge > 0:
                    if charge >= wst["min_charge"]:
                        fire_now = True  # released: fires along the aim right now
                    else:
                        charge = min(full, charge + DT)  # released too early: fires once min_charge is in
            if fire_now:
                frac = wst["min_frac"] + (1 - wst["min_frac"]) * min(1.0, charge / full)
                d = (math.cos(aim[1]) * math.cos(aim[0]), math.cos(aim[1]) * math.sin(aim[0]), math.sin(aim[1]))
                o = [3.0 * d[0], 3.0 * d[1], mech["muzzle_height"] + 3.0 * d[2]]
                e = [o[i] + d[i] * wst["range"] for i in range(3)]
                shots += 1
                if seg_hits_cyl(o, e, tg.pos, r, hgt):
                    hits += 1
                    dmg += wst["damage"] * frac
                charge, held_full, cooldown = 0.0, 0.0, wst["cooldown"]
        tg.step(t)
        keep = []
        for b in bullets:
            p0 = b[0]
            p1 = [p0[i] + b[1][i] * DT for i in range(3)]
            if seg_hits_cyl(p0, p1, tg.pos, r, hgt):
                hits += 1
                dmg += wst["damage"]
                continue
            b[2] += wst["speed"] * DT
            if b[2] < wst["range"]:
                b[0] = p1
                keep.append(b)
        bullets = keep
    return shots, hits, dmg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[], help="NAME=python_value: override a fight switch")
    ap.add_argument("--trials", type=int, default=25)
    ap.add_argument("--secs", type=float, default=12.0)
    ap.add_argument("--weapons", default="gun,laser")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    for s in args.set:
        k, v = s.split("=", 1)
        setattr(fight, k, eval(v))
    cfg = load_cfg()
    for weapon in args.weapons.split(","):
        print(f"\n{weapon}: hit % (damage per second)   [{args.trials} trials x {args.secs:.0f} s per cell]")
        print("            " + "".join(f"{d:>14} m" for d in DISTS))
        tot_s = tot_h = 0
        for beh in BEHAVIOURS:
            row = f"{beh:<11}"
            for dist in DISTS:
                rng = random.Random(zlib.crc32(f"{args.seed}/{weapon}/{beh}/{dist}".encode()))
                S = H = 0
                D = 0.0
                for _ in range(args.trials):
                    s, h, d = run_trial(cfg, weapon, beh, dist, args.secs, rng)
                    S, H, D = S + s, H + h, D + d
                tot_s, tot_h = tot_s + S, tot_h + H
                pct = 100.0 * H / S if S else 0.0
                row += f"{pct:>8.0f}% ({D / (args.trials * args.secs):>4.0f})"
            print(row)
        print(f"overall hit rate {100.0 * tot_h / max(1, tot_s):.1f}%")


if __name__ == "__main__":
    main()
