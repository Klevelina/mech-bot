"""Aim that learns how much to lead each enemy.

Every gun shot at a mech is remembered with the target's position p0 and velocity v at the moment we fired,
and the predicted flight time tt. When that time has passed we look where the target really is (A) and
solve for the lead factor that would have hit it: k = ((A - p0) . v) / (|v|^2 tt).
k = 1 means it kept going straight, k ~ 0 means it stopped or jinks back and forth.
Each enemy gets its own running average, blended with a global one until we have a few samples.

We also keep a running average of each enemy's acceleration: 0.5 * a * tt^2 is how far it can surprise us
during the flight, which tells us when a long shot isn't worth the ammo.
"""
import math


class AimLearner:
    def __init__(self):
        self.shots = []      # (t_fire, id, p0x, p0y, vx, vy, tt)
        self.k = {}          # id -> (k, n)
        self.gk, self.gn = 1.0, 0
        self.acc = {}        # id -> (ema accel m/s^2, last vel (vx, vy), last t)
        self.last_shot_t = -1e9
        self.samples = 0

    # ---- per decision
    def observe(self, t, mechs):
        by_id = {m["id"]: m for m in mechs if m["kind"] == "mech"}
        for i, m in by_id.items():
            vx, vy = m["vel"][0], m["vel"][1]
            a0, lv, lt = self.acc.get(i, (8.0, None, None))
            if lv is not None and 0.05 < t - lt < 0.5:
                a = math.hypot(vx - lv[0], vy - lv[1]) / (t - lt)
                a0 = 0.8 * a0 + 0.2 * min(a, 80.0)
            self.acc[i] = (a0, (vx, vy), t)
        keep = []
        for s in self.shots:
            tf, i, px, py, vx, vy, tt = s
            if t < tf + tt:
                keep.append(s)
                continue
            if t > tf + tt + 0.3:
                continue  # missed our chance to look
            m = by_id.get(i)
            if m is None:
                continue
            ax, ay = m["pos"][0], m["pos"][1]
            v2 = vx * vx + vy * vy
            if v2 * tt * tt < 1.5 ** 2:
                continue  # it was nearly standing still: nothing to learn
            # where it went vs. where straight-line motion over the time actually elapsed would put it
            el = t - tf
            k_obs = ((ax - px) * vx + (ay - py) * vy) / (v2 * el)
            k_obs = max(-0.5, min(2.0, k_obs))
            k0, n = self.k.get(i, (self.gk, 0))
            self.k[i] = (0.75 * k0 + 0.25 * k_obs, n + 1)
            self.gk = 0.9 * self.gk + 0.1 * k_obs
            self.gn += 1
            self.samples += 1
        self.shots = keep

    def lead(self, i):
        """Lead factor for enemy i."""
        g = max(0.2, min(1.3, self.gk))
        if i not in self.k:
            return g
        k, n = self.k[i]
        w = n / (n + 3.0)
        return max(0.2, min(1.3, w * k + (1 - w) * g))

    def surprise(self, i, tt):
        """How far (m) enemy i can stray from our prediction during a flight of tt seconds."""
        a = self.acc.get(i, (8.0, None, None))[0]
        return 0.5 * a * tt * tt

    def record(self, t, m, tt):
        if t - self.last_shot_t < 0.2:
            return
        self.last_shot_t = t
        self.shots.append((t, m["id"], m["pos"][0], m["pos"][1], m["vel"][0], m["vel"][1], tt))
        if len(self.shots) > 40:
            self.shots.pop(0)
