"""Mech bot: navigate to cover inside the next zone circle; shoot the nearest visible mech."""
import math
import time
import traceback
from collections import deque

import mechfight as mf
from nav import Nav, Search

SLICE_S = 0.020  # A* time per decision


class Bot:
    name = "mech-nav"

    def __init__(self, hello: mf.Hello):
        cfg = hello["config"]
        self.cfg = cfg
        self.reach = cfg["loot"]["pickup_radius"]
        self.walk = cfg["mech"].get("walk_speed", 11.7)
        try:
            self.nav = Nav(hello["map"]["dir"], cfg["mech"])
        except Exception:
            mf.log("nav build failed, walking straight:\n" + traceback.format_exc())
            self.nav = None
        self.goal = None        # (x, y)
        self.goal_key = None
        self.search = None
        self.path = None        # list of (x, y)
        self.pidx = 0
        self.hist = deque(maxlen=11)  # (t, x, y) over ~1 s
        self.unstick = 0
        self.stuck_n = 0
        self.bad = set()        # goal cells that failed

    # ---------- goal selection
    def choose_goal(self, mx, my, zone):
        nav = self.nav
        nc, nr = zone["next_center"], zone["next_radius"]
        R = nr - max(8.0, 0.25 * nr)
        if nav is None:
            return (nc[0], nc[1]), None
        if R < 15:
            c = nav.nearest_main(nc[0], nc[1])
            return nav.center(c), c
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
        return nav.center(best), best

    def replan(self, mx, my):
        if self.nav is None or self.goal is None:
            return
        start = self.nav.cell_of(mx, my)
        self.search = Search(self.nav, start, self.nav.cell_of(*self.goal))

    # ---------- main
    def act(self, obs: mf.Observation) -> mf.Action:
        t_start = time.perf_counter()
        me = obs["self"]
        x, y, z = me["pos"]
        zone = obs["zone"]
        act: mf.Action = {"tick": obs["tick"]}
        if obs["offer"]:
            act["pick"] = 0

        # new circle -> new goal
        key = (round(zone["next_center"][0], 1), round(zone["next_center"][1], 1), round(zone["next_radius"], 1))
        if key != self.goal_key:
            self.goal_key = key
            self.goal, _ = self.choose_goal(x, y, zone)
            self.path = None
            self.replan(x, y)

        # advance path search
        if self.search is not None:
            if self.search.step(t_start + SLICE_S):
                s = self.search
                self.search = None
                self.path = [self.nav.center(c) for c in s.path]
                self.pidx = 0
                if not s.reached:
                    mf.log(f"t={obs['t']:.1f} goal unreachable, partial path {len(s.path)}")
                    self.bad.add(s.goal)

        # steering
        gx, gy = self.goal if self.goal else zone["next_center"]
        dgoal = math.hypot(gx - x, gy - y)
        arrived = dgoal < 4.0
        if self.path:
            p = self.path
            lo, hi = self.pidx, min(len(p), self.pidx + 20)
            bi, bd = lo, 1e18
            for k in range(lo, hi):
                d = (p[k][0] - x) ** 2 + (p[k][1] - y) ** 2
                if d < bd:
                    bi, bd = k, d
            self.pidx = bi
            if bd > 12.0 ** 2 and self.search is None:  # fell off the path
                self.replan(x, y)
            tx, ty = p[min(len(p) - 1, bi + 3)]
        else:
            tx, ty = gx, gy
        move = [0.0, 0.0]
        if not arrived:
            dx, dy = tx - x, ty - y
            d = math.hypot(dx, dy)
            if d > 0.3:
                move = [dx / d, dy / d]
            else:
                move = [(gx - x) / max(dgoal, 1e-6), (gy - y) / max(dgoal, 1e-6)]
        moving = move != [0.0, 0.0]

        # stuck detection
        t = obs["t"]
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

        yaw = math.atan2(move[1], move[0]) if moving else math.atan2(zone["center"][1] - y, zone["center"][0] - x)
        act.update(move=move, boost=boost, jump=jump, aim=[yaw, 0.0])

        # loot: take a non-gun weapon if standing on it
        near = [p for p in obs["pickups"] if math.dist(p["pos"][:2], (x, y)) < self.reach]
        if me["weapons"][1] is None and near and near[0]["kind"] != "gun":
            act["take"] = near[0]["id"]

        # fight: nearest visible mech (unchanged from the example for now)
        enemies = sorted((m for m in obs["mechs"] if m["kind"] == "mech"), key=lambda m: math.dist(m["pos"], me["pos"]))
        if enemies:
            e = enemies[0]["pos"]
            aim = [math.atan2(e[1] - y, e[0] - x), math.atan2(e[2] - z - 2.5, math.dist(e[:2], (x, y)))]
            act.update(aim=aim, fire=[True, True])
            mf.draw.line([x, y, z + 7], [e[0], e[1], e[2] + 4.5], "#ff5040")

        # debug
        if self.path:
            for k in range(self.pidx, min(len(self.path), self.pidx + 30), 3):
                px, py = self.path[k]
                mf.draw.point([px, py, z + 1], "#40ff80", 3)
        mf.draw.point([gx, gy, z + 1], "#40c0ff", 8)
        mf.draw.label(f"hp {me['hp']:.0f} d {dgoal:.0f}{' S' if self.unstick else ''}{' P' if self.search else ''}")
        return act


if __name__ == "__main__":
    mf.run(Bot)
