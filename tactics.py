"""Tactics: what each enemy can do to us (by weapon and range, even when we don't know all its weapons),
unpredictable movement under fire that learns which pattern gets us hit least, and dodging telegraphed
laser shots and mortar shells."""
import math
import random

SPECIALS = ("lightning", "laser", "mortar")


class EnemyModel:
    """Per enemy mech: weapons (seen, or inferred from what hit us), last position, HP."""

    def __init__(self, cfg):
        self.w = cfg["weapons"]
        self.kinds = {}      # id -> set of weapon kinds we know it holds
        self.exact = {}      # id -> True when the server showed us its full loadout
        self.pos = {}        # id -> (t, pos, vel)
        self.hp = {}         # id -> hp

    def update(self, obs):
        t = obs["t"]
        for m in obs["mechs"]:
            if m["kind"] != "mech":
                continue
            i = m["id"]
            if m.get("weapons") is not None:
                self.kinds[i] = set(m["weapons"])  # authoritative: what it holds right now
                self.exact[i] = True
            self.pos[i] = (t, m["pos"], m["vel"])
            if m.get("hp") is not None:
                self.hp[i] = m["hp"]
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None and e["by"] < 100:
                if e.get("weapon") in ("gun",) + SPECIALS:
                    self.kinds.setdefault(e["by"], set()).add(e["weapon"])

    def weapon_dps(self, k, d):
        """Rough damage per second weapon k does to us from d m away (accounting for misses)."""
        w = self.w.get(k, {})
        if k == "gun":
            if d >= w.get("range", 230):
                return 0.0
            hit = max(0.15, 0.9 - d / 300.0)  # dodging/spread make long shots miss more
            return hit * w.get("damage", 45) / max(0.05, w.get("cooldown", 0.24))
        if k == "lightning":
            return w.get("damage", 60) / max(0.05, w.get("cooldown", 0.25)) if d <= w.get("range", 45) + 3 else 0.0
        if k == "laser":
            if d > w.get("range", 280):
                return 0.0
            return 0.7 * w.get("damage", 315) / (w.get("charge", 0.75) + w.get("cooldown", 1.65))
        if k == "mortar":
            if d < 15 or d > 235:
                return 0.0
            return 0.3 * (w.get("damage", 200) + w.get("splash", 160)) / max(0.5, w.get("cooldown", 2.0))
        return 0.0

    def dps_at(self, i, d, t):
        """Expected damage per second enemy i does to us at distance d. Unknown loadout: gun for sure,
        plus a special with a probability that grows over the match (spread evenly over the three)."""
        kinds = self.kinds.get(i, set())
        total = sum(self.weapon_dps(k, d) for k in kinds)
        if not self.exact.get(i):
            if "gun" not in kinds:
                total += self.weapon_dps("gun", d)
            if not (kinds & set(SPECIALS)):
                p = min(0.85, 0.2 + t / 150.0)
                total += p * sum(self.weapon_dps(k, d) for k in SPECIALS) / 3.0
        return total


class Jinker:
    """Sideways movement under fire. Several patterns; keeps score of gun damage taken per second in each
    and mostly uses the best one (sometimes tries another), so it adapts to how these shooters aim."""

    PATTERNS = {  # name: (min, max seconds between reversals, stop-and-go)
        "strafe": (2.0, 3.5, False),
        "short": (0.3, 0.6, False),
        "long": (0.6, 1.2, False),
        "stopgo": (0.4, 0.8, True),
    }

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.stats = {k: [40.0, 1.0] for k in self.PATTERNS}  # [gun damage, seconds]: optimistic start
        self.cur = "short"
        self.switch_t = -1e9
        self.flip_t = -1e9
        self.side = 1
        self.moving = True
        self.last_t = None

    def flip(self):
        self.side = -self.side

    def step(self, t, active, gun_dmg):
        """Returns -1/+1 (which side to step) or 0 (stand: stop-and-go pause)."""
        dt = 0.1 if self.last_t is None else max(0.0, min(0.5, t - self.last_t))
        self.last_t = t
        if not active:
            return 0
        s = self.stats[self.cur]
        s[0] += gun_dmg
        s[1] += dt
        if t >= self.switch_t:
            if self.rng.random() < 0.2:
                self.cur = self.rng.choice(list(self.PATTERNS))
            else:
                self.cur = min(self.PATTERNS, key=lambda k: self.stats[k][0] / self.stats[k][1])
            self.switch_t = t + 4.0
        lo, hi, stopgo = self.PATTERNS[self.cur]
        if t >= self.flip_t:
            if stopgo:
                self.moving = not self.moving
                if self.moving:
                    self.side = self.rng.choice((-1, 1))
            else:
                self.moving = True
                self.side = -self.side if self.rng.random() < 0.85 else self.side
            self.flip_t = t + self.rng.uniform(lo, hi)
        return self.side if self.moving else 0

    def rate(self, k):
        s = self.stats[k]
        return s[0] / s[1]


def mortar_landing(p, v, g, ground_z, max_t=4.0):
    """Where and when a shell at p with velocity v lands (simple ballistic steps). None if not within max_t."""
    x, y, z = p
    vx, vy, vz = v
    dt = 0.05
    tt = 0.0
    while tt < max_t:
        x += vx * dt
        y += vy * dt
        z += vz * dt
        vz -= g * dt
        tt += dt
        if vz < 0 and z <= ground_z(x, y):
            return x, y, tt
    return None
