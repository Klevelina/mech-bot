"""Vanish when surrounded: break every known enemy's line of sight, go silent, heal, and find out whether they lost us.

Vision rules (protocol §8): a mech keeps tracking us for vision.memory_s (5 s) after it last saw us; footsteps are
made only above sound.step_speed (5 m/s), boosting / jets / guns / reloads are heard further. So: run (boosting is
fine, they see us anyway) to a spot none of the known enemies can see, then walk slowly, hold fire, don't boost.
After VANISH_HOLD_S unseen we judge: an enemy that walked straight at our new spot still tracks us (shared vision);
two of those in a row and we stop vanishing for the rest of the match. Enemies that turned away from us are
"unaware": ambush targets (hits on their back do x1.5) that we hunt at lower odds.
"""
import math

import mechfight as mf
from config import *  # noqa: F401,F403


class StealthMixin:
    vanish = None          # dict while vanishing
    vanish_off = False     # they tracked us through it twice in a row: don't bother any more this match
    vanish_tracked = 0
    unaware = None         # enemy id -> until t: lost us, back turned - an ambush target
    last_kill_t = -1e9

    def _known_enemies(self, obs_mechs, t):
        """[(id, x, y, eye_z)] of enemy mechs we know about: in the observation, or seen within 8 s."""
        out, ids = [], set()
        for m in obs_mechs:
            if m["kind"] == "mech":
                ids.add(m["id"])
                out.append((m["id"], m["pos"][0], m["pos"][1], self.eye_of(m)))
        for i, (et, pos, _) in self.epos.items():
            if i < 100 and i not in ids and t - et < 8.0:
                out.append((i, pos[0], pos[1], pos[2] + 8.0))
        return out

    def _seen_by(self, me, eyes):
        """Known enemies with a line of sight to our top right now (ids)."""
        x, y, z = me["pos"]
        top = z + me["stats"].get("eye_height", 8.0) + 0.5
        return [i for i, ex, ey, ez in eyes if self.nav.los(ex, ey, ez, x, y, top, skip=1.0)]

    def update_vanish(self, me, obs_mechs, fight_m, t, t_start):
        """Start / run / end a vanish. Returns the goal (x, y) while vanishing, else None."""
        if not VANISH or self.nav is None:
            return None
        if self.unaware is None:
            self.unaware = {}
        x, y = me["pos"][0], me["pos"][1]
        eyes = self._known_enemies(obs_mechs, t)
        near = [e for e in eyes if math.hypot(e[1] - x, e[2] - y) < VANISH_R]
        v = self.vanish
        if v is None:
            if self.vanish_off or self.late_zone or not near:
                return None
            under_fire = t - self.last_dmg_t < 3.0
            surrounded = len(near) >= 2 and under_fire
            # just killed one and another is around: relocate before it gets here (shot at or not)
            after_kill = t - self.last_kill_t < VANISH_AFTER_KILL_S
            # a lone attacker we're losing to: evade instead of trading
            losing = False
            if fight_m is not None and fight_m["kind"] == "mech" and under_fire and len(near) == 1:
                losing = self.fight_odds(me, fight_m, t) < VANISH_LOSE_R
            if not (surrounded or after_kill or losing):
                return None
            if fight_m is not None and fight_m["kind"] == "mech" and self.can_hit(me, fight_m):
                d = math.hypot(fight_m["pos"][0] - x, fight_m["pos"][1] - y)
                if (fight_m.get("hp") or 1400.0) / self.our_dps(me, fight_m, d, True) < 2.0:
                    return None  # it dies in ~2 s: finish it first
            c = self.nav.flank_cell([(ex, ey, ez) for _, ex, ey, ez in near], None, x, y, VANISH_SPOT_R,
                                    t_start + 0.012)
            if c < 0:
                return None  # nowhere to vanish to: fight as usual
            self.vanish = v = {"t0": t, "goal": self.nav.center(c), "hidden_t": None, "from": (x, y),
                               "ids": [e[0] for e in near], "close": set()}
            why = "surrounded" if surrounded else ("after a kill" if after_kill else "losing 1v1")
            mf.log(f"t={t:.1f} vanish ({why}, {len(near)} near) -> {v['goal'][0]:.0f},{v['goal'][1]:.0f}")
        seen = self._seen_by(me, eyes)
        if seen:
            v["hidden_t"] = None
            if t - v.get("repick_t", -1e9) > 1.0 and math.hypot(v["goal"][0] - x, v["goal"][1] - y) < 4.0:
                # we're at the spot and someone sees us anyway: another spot, or stop (and fight)
                v["repick_t"] = t
                c = self.nav.flank_cell([(ex, ey, ez) for _, ex, ey, ez in eyes], None, x, y, VANISH_SPOT_R,
                                        t_start + 0.012)
                if c < 0 or t - v["t0"] > VANISH_MAX_S:
                    self._end_vanish(t, tracked=None)
                    return None
                v["goal"] = self.nav.center(c)
        elif v["hidden_t"] is None:
            v["hidden_t"] = t
        # who is walking at us while we hide (tracking us), and who has turned away (lost us)
        for m in obs_mechs:
            if m["kind"] != "mech":
                continue
            ex, ey = m["pos"][0], m["pos"][1]
            d = math.hypot(ex - x, ey - y) or 1.0
            toward = (m["vel"][0] * (x - ex) + m["vel"][1] * (y - ey)) / d
            if v["hidden_t"] is not None and t - v["hidden_t"] > VISION_MEM_S and toward > 3.0 and d < 80.0:
                v["close"].add(m["id"])  # still coming straight for us after its memory ran out
        if v["hidden_t"] is not None and t - v["hidden_t"] >= VANISH_HOLD_S and not v.get("judged"):
            tracked = bool(v["close"])
            if tracked:
                self._end_vanish(t, True)  # they still come for us: fight from here
                return None
            v["judged"] = True
            self._end_vanish(t, False, keep=True)  # they lost us
        if v.get("judged"):
            if me["hp"] / max(1.0, me["max_hp"]) >= VANISH_HEAL_TO or v["hidden_t"] is None:
                # healed (or someone sees us again): back to normal play - loot, then hunt the ones that lost us
                for m in obs_mechs:
                    if m["kind"] == "mech":
                        self.unaware[m["id"]] = t + UNAWARE_S
                self.vanish = None
                return None
        if t - v["t0"] > VANISH_MAX_S:
            self._end_vanish(t, tracked=None)
            return None
        return v["goal"]

    def _end_vanish(self, t, tracked, keep=False):
        if tracked is True:
            self.vanish_tracked += 1
            if self.vanish_tracked >= VANISH_GIVEUP:
                self.vanish_off = True
                mf.log(f"t={t:.1f} they track us through cover: no more vanishing this match")
        elif tracked is False:
            self.vanish_tracked = 0
            mf.log(f"t={t:.1f} vanished: they lost us")
        if not keep:
            self.vanish = None

    def vanish_hidden(self):
        """True once we're out of every known enemy's sight (go silent, hold fire)."""
        return self.vanish is not None and self.vanish.get("hidden_t") is not None

    def is_unaware(self, m, t):
        """An enemy that lost us (after a vanish) and isn't facing us: an ambush target."""
        if self.unaware is None or t > self.unaware.get(m["id"], -1e9):
            return False
        x, y = self._me_xy
        return abs(((math.atan2(y - m["pos"][1], x - m["pos"][0]) - m["aim"][0] + math.pi) % (2 * math.pi))
                   - math.pi) > math.pi / 2
