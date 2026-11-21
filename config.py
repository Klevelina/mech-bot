"""All switches and tuning numbers in one place. Flip a switch here to compare submissions."""

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
FACE_ATTACKER = True   # turn toward where damage comes from when we can't see the shooter
LEAD = True            # lead gun shots at mechs
SMART_TARGET = True    # attacker > low HP > back turned > close; else nearest
ESCAPE_UNHITTABLE = True  # break line of sight from attackers we can't hit back
NO_HIT_S = 2.5         # ... "can't hit" also means: shooting it this long with no hit landing
LEARN_AIM = True       # learn per-enemy lead factor from where targets really went
HOLD_DODGY = True      # don't spend gun ammo on long shots at targets that change direction a lot
PLAYSTYLE = True       # move by loadout: gun keeps mid range, laser keeps long range, lightning ambushes
DRONE_FARM = True      # shoot back at drones that shoot us, treat them as loot
WEIGH_THREATS = True   # several attackers: fight the best trade, stand where the others can't see us
HIDE_LOW_HP = 0.30     # below this HP fraction, under fire: hide from every attacker (0 = off)
EXPLORE = True         # early, with nothing to loot: walk to unexplored areas to find loot
EXPLORE_UNTIL = 100.0  # match time
EXPLORE_BIN = 25.0     # m, size of the "have we looked here" bins
FIELD = False          # in fights, stand where (expected damage taken) - (expected damage dealt) is lowest
FIELD_H = 3.0          # ... over this many seconds
FIELD_KILL_W = 1.0     # ... weight of "fraction of an enemy we kill" vs "fraction of our HP we lose"
JINK = False           # step side to side (learned pattern) while a gun is shooting at us
DODGE = False          # sidestep lasers aimed at us while charging; leave mortar landing spots
SOUNDS = True          # face suspicious sounds, treat nearby movement as a threat, mark fights on the danger map
SOUND_FACE_S = 2.5     # how long to keep facing a sound
DROPS = True           # go for supply drops when it looks safe
DROP_MAX_DIST = 220.0  # m (x1.3 when HP is below half)
DROP_DANGER_R = 100.0  # skip a drop if an enemy was seen/heard this close to it in the last 20 s
CONTEST = True         # one mech capturing a drop, we reach the ring before it finishes and would win: go in
CONTEST_MIN_HP = 0.5   # ... only above this HP fraction
CONTEST_MAX_DIST = 80.0  # ... and only this close (less open ground to cross); sniping has no distance limit
PARK_EXPOSURE = True   # park in corners/alcoves (few open directions) and face the open side
EXPO_W = 6.0           # m of extra walking we accept per open direction avoided (of 12)
HUNT = True            # one enemy in sight, nobody else on us, healthy: close to our weapon's range and fight
HUNT_HP = 0.6          # ... "healthy"
HUNT_RANGE = {"lightning": 30.0, "laser": 110.0, "gun": 70.0}
SLICE_S = 0.020        # A* time per decision

# sound kind -> how much it says "an enemy mech is there" (x band multiplier). npc_gun/crate/drop/death: ignored
SOUND_PRIO = {"step": 3.0, "boost": 3.0, "jet": 3.0, "reload": 3.0, "laser_charge": 3.0,
              "lightning": 2.5, "gun": 2.0, "laser": 2.0, "mortar": 1.5, "explosion": 1.0}
BAND_MULT = {"near": 1.0, "mid": 0.6, "far": 0.25}
BAND_DIST = {"near": 35.0, "mid": 110.0, "far": 230.0}  # rough distance for each band
MOVE_SOUNDS = ("step", "boost", "jet", "reload", "laser_charge")

# ---- late game, lightning, finishing kills
LIGHTNING_FIRST = True  # prefer lightning pickups/swaps all game (chains through groups); its upgrades early
LATE_RADIUS = 70.0     # "late game": next circle this small or smaller
LATE_GAME = True       # late: no hunting / drop contests; hold fire unless it's a quick kill or they shoot us
LATE_QUICK_KILL_S = 3.0  # ... "quick kill"
FINISH = True          # chase a fleeing low-HP enemy for a few seconds (a kill is +1; if it gets away it heals)
FINISH_HP = 0.25       # ... its HP fraction below this
FINISH_MIN_HP = 0.4    # ... and ours above this
FINISH_S = 4.0         # ... keep going to where we last saw it this long
FINISH_RANGE = {"lightning": 25.0, "laser": 100.0, "gun": 45.0}

# ---- playbook: fight by loadout vs enemy loadout (playbook.py)
PLAYBOOK = True        # distance bands per matchup, laser + lightning target loadout, no laser charge near lightning
DROP_GUN_FOR_PAIR = False  # True: drop the gun as soon as we hold laser AND lightning (False: only late game)
AWAY_HIDE_ALL = True   # backing off picks a spot hidden from every visible enemy, not just the one we back off from

# ---- reloading (the magazine also reloads by itself when empty)
RELOAD_IN_PAUSE = True   # target behind cover for the moment: reload if the magazine is below this fraction
RELOAD_PAUSE_FRAC = 0.6
RELOAD_BEFORE_HUNT = True  # gun-only: don't go hunting with less than this fraction of a magazine; reload first
RELOAD_HUNT_FRAC = 0.5
