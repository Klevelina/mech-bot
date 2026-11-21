"""Loot: remember items we have seen, pick what to go for, weapon swap rules, upgrade picks."""
import math

from config import LASER_FIRST, LIGHTNING_FIRST, PLAYBOOK, DROP_GUN_FOR_PAIR, MORTAR_USE, UPGRADE_BY_LOADOUT, \
    POWER, POWER_WEAPON_V, POWER_DRONE_V, POWER_R, POWER_MORE, POWER_CRATE_V

# What a found item is worth before distance (rough "cubes" scale).
VALUE = {"cube": 3.0, "crate": 2.5, "npc": 5.0, "pile": 6.0}  # a drone drops 2 cubes
# Upgrade picks, first available wins. Plating/armor/nanites keep us alive; the rest are fillers.
# With LIGHTNING_FIRST, lightning's chain/range come right after plating/armor (only offered if we hold it).
UPGRADE_ORDER = (["plating", "lightning_chain", "armor", "lightning_range", "nanites", "lightning_efficiency", "sensors"]
                 if LIGHTNING_FIRST else ["plating", "armor", "nanites", "sensors", "lightning_efficiency"]) +                 ["laser_charge", "laser_damage", "lightning_range", "lightning_chain",
                 "mortar_reload", "mortar_velocity", "mortar_splash",
                 "gun_damage", "gun_rate", "gun_mag", "gun_velocity",
                 "servos", "capacitor", "reactor", "gyros", "jets"]
SPECIALS = ("laser", "lightning")


# Upgrade order by what we carry (UPGRADE_BY_LOADOUT): survival first (plating, armor), then what makes OUR
# weapons hit harder/sooner; the generic list is the fallback for anything not listed.
UPGRADES_FOR = {
    "gun": ["plating", "gun_damage", "armor", "gun_rate", "nanites", "gun_mag", "sensors", "gun_velocity"],
    "laser": ["plating", "laser_charge", "armor", "laser_damage", "nanites", "sensors", "laser_pierce"],
    "lightning": ["plating", "lightning_chain", "armor", "lightning_range", "nanites", "lightning_efficiency",
                  "capacitor", "reactor"],
    "hybrid": ["plating", "lightning_chain", "laser_charge", "armor", "lightning_range", "laser_damage", "nanites",
               "lightning_efficiency"],
    "mortar": ["plating", "armor", "mortar_reload", "nanites", "mortar_velocity", "mortar_splash", "gun_damage"],
}


def upgrade_order(weapons):
    kinds = {w["kind"] for w in (weapons or []) if w}
    if "laser" in kinds and "lightning" in kinds:
        key = "hybrid"
    elif "lightning" in kinds:
        key = "lightning"
    elif "laser" in kinds:
        key = "laser"
    elif "mortar" in kinds:
        key = "mortar"
    else:
        key = "gun"
    return UPGRADES_FOR[key] + UPGRADE_ORDER


def pick_upgrade(offer, weapons=None):
    ids = [c["id"] for c in offer["choices"]]
    order = upgrade_order(weapons) if (UPGRADE_BY_LOADOUT and weapons is not None) else UPGRADE_ORDER
    for want in order:
        if want in ids:
            return ids.index(want)
    return 0


def weapon_take(kind, weapons, late, fighting):
    """Should we take a `kind` pickup? Returns None (no), or the `drop` slot (None = an empty slot is free).

    Rules: never mortar or a second gun; empty slot -> take; mortar in slot 1 -> swap it;
    swap the other special toward the stage's preference (laser early, lightning late);
    late game also replace the gun with the second special. Never swap during a fight."""
    if kind == "mortar":
        # better than an empty slot; never instead of a laser/lightning (those replace it later)
        kinds = [w["kind"] if w else None for w in weapons]
        if MORTAR_USE and None in kinds and "mortar" not in kinds and                 not any(w and w.get("charge", 0) > 0 for w in weapons):
            return "empty"
        return None
    if kind not in SPECIALS:
        return None
    kinds = [w["kind"] if w else None for w in weapons]
    if kind in kinds:
        return None
    if any(w and w.get("charge", 0) > 0 for w in weapons):
        return None  # can't swap while a laser charges
    if None in kinds:
        return "empty"
    if fighting:
        return None
    s0, s1 = kinds[0], kinds[1]
    if s1 == "mortar":
        return 1
    if s0 == "mortar":
        return 0
    if PLAYBOOK and DROP_GUN_FOR_PAIR and s0 == "gun" and s1 in SPECIALS and kind != s1:
        return 0  # playbook target loadout: laser + lightning, the gun goes
    if LASER_FIRST:
        # gun + laser: swap a lightning for a laser, keep the gun all game
        if kind == "laser" and "lightning" in (s0, s1):
            return 0 if s0 == "lightning" else 1
        return None
    pref = "lightning" if (late or LIGHTNING_FIRST) else "laser"  # lightning chains through groups
    if late and s0 == "gun":
        return 0  # gun out, second special in
    if kind == pref and s1 in SPECIALS and s1 != pref:
        return 1
    return None


class Memory:
    """Items seen so far. Crates, cubes and pickups don't move; NPCs wander near home."""

    def __init__(self, nav, cfg):
        self.nav = nav
        self.items = {}   # id -> dict(kind, x, y, z, seen, wkind, hp)
        self.banned = set()
        self.near_range = cfg["vision"].get("near_range", 45)

    def update(self, obs):
        t = obs["t"]
        for c in obs["cubes"]:
            self._see(c, "cube", t)
        for c in obs["crates"]:
            self._see(c, "crate", t, hp=c.get("hp"))
        for p in obs["pickups"]:
            self._see(p, "pickup", t, wkind=p["kind"])
        for m in obs["mechs"]:
            if m["kind"] == "npc":
                self._see(m, "npc", t, hp=m.get("hp"))
        for e in obs["events"]:
            if e["type"] in ("took",):
                self.items.pop(e["id"], None)
            elif e["type"] == "dropped":
                self.banned.add(e["id"])  # don't pick back up what we just dropped
        # forget things that should be visible but aren't (close by), and stale ones
        x, y = obs["self"]["pos"][0], obs["self"]["pos"][1]
        gone = []
        for i, it in self.items.items():
            age = t - it["seen"]
            if age > 1.0 and (it["x"] - x) ** 2 + (it["y"] - y) ** 2 < 20.0 ** 2:
                gone.append(i)
            elif it["kind"] == "npc" and age > 60:  # drones stay within ~30-60 m of home
                gone.append(i)
            elif age > 120:
                gone.append(i)
        for i in gone:
            del self.items[i]

    def _see(self, o, kind, t, **extra):
        x, y, z = o["pos"]
        it = self.items.get(o["id"])
        if it is None:
            it = {"kind": kind, "cell": None, "ok": None}
            self.items[o["id"]] = it
        it.update(x=x, y=y, z=z, seen=t, **extra)

    def reachable_cell(self, it):
        """Main-component cell next to the item, or -1 (roof / unreachable)."""
        nav = self.nav
        if it["ok"] is None or it["kind"] == "npc":
            ok = False
            cell = -1
            if nav is not None:
                gz = nav.ground_z(it["x"], it["y"])
                if it["z"] < gz + 3.0:  # not on a roof
                    c = nav.nearest_main(it["x"], it["y"], max_r=4)
                    if nav.comp[c] == nav.main:
                        cx, cy = nav.center(c)
                        if math.hypot(cx - it["x"], cy - it["y"]) < 6.0:
                            ok, cell = True, c
            it["ok"], it["cell"] = ok, cell
        return it["cell"] if it["ok"] else -1

    def best_target(self, me, zone, weapons, late, max_dist, danger, skip=(), phase=None):
        """(id, item) worth going for, or (None, None). `danger(x, y)` -> True to skip.
        POWER: no laser/lightning yet -> those are the top target (up to POWER_R away); early phase -> drones next."""
        x, y = me["pos"][0], me["pos"][1]
        no_special = not ({w["kind"] for w in weapons if w} & set(SPECIALS))
        nc, nr = zone["next_center"], zone["next_radius"]
        cc, cr = zone["center"], zone["radius"]
        best, bs = None, 0.0
        for i, it in self.items.items():
            if i in self.banned:
                continue
            k = it["kind"]
            if k in skip:
                continue
            if k == "pickup":
                d = weapon_take(it["wkind"], weapons, late, False)
                if d is None:
                    continue
                v = 5.0 if d == "empty" else 3.0
                if it["wkind"] == "mortar":
                    v = 3.0
                if LASER_FIRST and it["wkind"] == "laser":
                    v += 2.0
                if LIGHTNING_FIRST and it["wkind"] == "lightning":
                    v += 2.0
                if PLAYBOOK:
                    held = {w["kind"] for w in weapons if w}
                    if DROP_GUN_FOR_PAIR and held & set(SPECIALS) and it["wkind"] not in held:
                        v = 7.0  # completes laser + lightning
                    elif not held & set(SPECIALS):
                        v = max(v, 6.0)  # first special of any kind: both are a big step up from the gun
            else:
                v = VALUE[k]
                if POWER_MORE and k == "crate":
                    v = POWER_CRATE_V  # a cube plus a 50% chance of a weapon
            reach = max_dist
            if POWER:
                if k == "pickup" and no_special and it["wkind"] in SPECIALS:
                    v, reach = POWER_WEAPON_V, max(max_dist, POWER_R)  # a good weapon first (any phase)
                elif k == "npc" and (phase == "early" or (POWER_MORE and phase == "mid")):
                    v, reach = POWER_DRONE_V, max(max_dist, POWER_R)  # then drones: 2 cubes each, weak shooters
            ix, iy = it["x"], it["y"]
            dist = math.hypot(ix - x, iy - y)
            if dist > reach:
                continue
            if math.hypot(ix - nc[0], iy - nc[1]) > nr - 5 or math.hypot(ix - cc[0], iy - cc[1]) > cr - 5:
                continue
            if danger(ix, iy):
                continue
            if self.reachable_cell(it) < 0:
                continue
            s = v / (dist + 30.0)
            if s > bs:
                best, bs = i, s
        return (best, self.items[best]) if best is not None else (None, None)
