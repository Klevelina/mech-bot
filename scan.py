"""Gear sweeps (squad all in): when calm, turn the torso a full circle every few seconds - we only see a 120 degree
wedge (plus 45 m all round), so weapons, crates and cubes behind us stay unknown otherwise. Without a laser yet (or on
the first sweep), with plenty of energy, jump and jet up during the sweep to see over the buildings around us."""
import math

from config import *  # noqa: F401,F403
from util import wrap


class ScanMixin:
    scan_on = False
    scan_acc = 0.0
    scan_yaw = 0.0
    scan_last = -1e9
    scan_jet = False
    scan_first = True

    def scan_calm(self, me, obs_mechs, t):
        """No damage for 3 s and no enemy mech that matters close by."""
        if t - self.last_dmg_t < 3.0:
            return False
        x, y = me["pos"][0], me["pos"][1]
        for m in obs_mechs:
            if m["kind"] != "mech":
                continue
            d = math.hypot(m["pos"][0] - x, m["pos"][1] - y)
            if d < SCAN_ANY_R:
                return False
            if d < SCAN_MECH_R and not (getattr(self, "gearing", False) and self.gear_harmless(me, m)):
                return False
        return True

    def scan_step(self, me, obs_mechs, mode, t):
        """(yaw or None, jump, boost): the sweep's torso command and jet scan, or None when not sweeping."""
        if not (SCAN and getattr(self, "all_in", False)):
            return None, False, False
        yaw_now = me["aim"][0]
        calm = mode in SCAN_MODES and self.scan_calm(me, obs_mechs, t)
        if self.scan_on:
            if not calm:
                self.scan_on = self.scan_jet = False
                return None, False, False
            self.scan_acc += abs(wrap(yaw_now - self.scan_yaw))
            self.scan_yaw = yaw_now
            if self.scan_acc >= 2 * math.pi - 0.4:
                self.scan_on = self.scan_jet = False
                self.scan_last = t
                return None, False, False
        else:
            has_laser = any(w and w["kind"] == "laser" for w in me["weapons"])
            every = SCAN_EVERY_NOLASER if not has_laser else SCAN_EVERY
            if not calm or t - self.scan_last < every:
                return None, False, False
            self.scan_on, self.scan_acc, self.scan_yaw, self.scan_last = True, 0.0, yaw_now, t
            self.scan_jet = me["energy"] >= SCAN_JET_E and (self.scan_first or not has_laser)
            self.scan_first = False
        jump = boost = False
        if self.scan_jet:
            if me["energy"] < SCAN_JET_STOP:
                self.scan_jet = False
            elif me.get("grounded", True):
                jump = True
            else:
                boost = True  # in the air: jets up
        return wrap(yaw_now + 1.5), jump, boost
