"""All switches and tuning numbers in one place. Flip a switch here to compare submissions."""

# ---- switches (flip to compare submissions)
LOOT = True            # go for items at all
LOOT_UNTIL = 150.0     # match time after which we only loot items close by
LOOT_LATE_DIST = 40.0  # ... "close by" after LOOT_UNTIL
LOOT_MAX_DIST = 110.0  # (160 in the 2.94 build) early-phase loot range
SAFE_AFTER = 3.0       # s without seeing an enemy mech / being hit by one before looting again (6 in the 2.90 build)
FIGHTING_S = 3.0       # "in a fight" for this long after the last threat (no sweeping the torso, no weapon swaps)
HEAL_AFTER_S = 3.0     # start healing (phases) after this long without a threat
DROP_AFTER_S = 3.0     # go for a supply drop only after this long without a threat
ESCAPE_END_S = 4.0     # an escape ends this long after the attacker's last hit
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
EXPLORE_UNTIL = 70.0   # match time (100 in the 2.94 build)
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

# ---- map & items
MORTAR_USE = True      # take a mortar into an empty slot; lob over cover / at slow or bunched targets
MORTAR_MIN_DIST = 25.0  # never shell anything closer (our own splash and knock-back)
MORTAR_MAX_DIST = 230.0
DODGE_MORTAR = True    # leave the predicted landing spot of incoming shells (laser sidestep stays with DODGE)
DEATH_PILES = True     # a death sound marks a loot target (its cubes + 2 and weapons)
OPENING_SCAN = True    # first 3 s: spin the torso once to spot loot and the first hunter
PILE_FINISH = True     # an enemy within 30 m of a known death pile below PILE_FINISH_HP: finish it (and take the pile)
PILE_FINISH_HP = 0.45

# ---- recovery after fights
RECOVER = False        # below RECOVER_HP with no fight for 3 s: heal up before looting far / exploring / hunting
RECOVER_HP = 0.7
RECOVER_UNTIL = 0.95
RECOVER_LOOT_DIST = 60.0  # while healing: only cubes / weapons / piles this close (no crates or drones: shooting resets regen)

# ---- game phases (replaces RECOVER): loot hard early, heal only with a group weapon mid, conserve HP late
PHASES = True
PHASE_EARLY_ALIVE = 11  # early: at least this many alive ...
PHASE_EARLY_RADIUS = 140.0  # ... and the next circle at least this big (late = <= 5 alive or circle <= 70)
PHASE_MID_LOOT = 80.0  # mid: loot this far (early LOOT_MAX_DIST, late LOOT_LATE_DIST)
PHASE_MID_HEAL = 0.5   # mid, only holding lightning or mortar: heal at a corner below this (up to 90%)
PHASE_LATE_HEAL = 0.9  # late: heal whenever below this (HP decides the final collapse)

# ---- aim per weapon / watching
LASER_LEAD_S = 0.1     # laser fires on our next decision: aim where the target will be then
LIGHTNING_CLUSTER = True  # with lightning: prefer targets with other enemies within chain range
OPEN_WATCH = True      # parked in the open (8+ of 12 directions) with enemies around: sweep the torso wider
UPGRADE_BY_LOADOUT = True  # upgrade order depends on what we carry (loot.UPGRADES_FOR)

# ---- early picks, then hide and heal: hunt weaker enemies only in the early phase, never after; heal more mid game
EARLY_AGGRO = True
AGGRO_HP = 0.5         # early: hunt while our HP is above this (instead of HUNT_HP)
AGGRO_CLEAR_R = 80.0   # early: hunt only if no other enemy was seen/heard this close to us in 10 s (normally 150 m)
AGGRO_WEAKER = 0.9     # "weaker" target: its HP at most this x ours, or its back is turned to us
AGGRO_FINISH_HP = 0.4  # early: chase a fleeing enemy below this HP fraction (normally FINISH_HP)
AGGRO_MID_HEAL = (0.7, 1.0)  # mid game, any loadout: heal below 70% up to full (late game keeps 90% -> full)

# ---- fight odds: (seconds until we die to everyone shooting us) / (seconds for us to kill the target); >1 = we win
FIGHT_ODDS = True
FIGHT_WIN_R = 1.5      # start a hunt only with odds at least this good (judged at the hunt distance)
FIGHT_LOSE_R = 0.8     # being shot with odds below this: back off out of sight (unless the target dies within 2 s)
DISENGAGE_R = 35.0     # ... only to a spot hidden from every visible enemy within this many metres (boost-run there);
                       #     none that close: stay and fight (running in the open only gets us shot longer)

# ---- hide from lasers we can't answer: no laser of our own, lasered (or a laser charging on us) from beyond our reach
LASER_HIDE = True
LASER_HIDE_MIN = 60.0  # closer than this our gun / lightning can fight it: don't hide
LASER_HIDE_R = 50.0    # hide only to a spot it can't see within this many metres (none: fight as usual)
LASER_HOLD_S = 4.0     # stay hidden until this long without a laser hit; on leaving, a cover spot the laser could
                       # see from where it was is swapped for another one. While hidden: watch its way in, top up
                       # the gun; if it follows us within LASER_HIDE_MIN with a clear shot, stop hiding and shoot first
LASER_RELOAD_FRAC = 0.75  # hidden from a laser: reload below this fraction of a magazine

# ---- faster looting
LOOT_GIVEUP_S = 15.0   # give up on an item after this long (was 40 s)
LOOT_CRATE_SHOOT_R = 90.0  # start shooting a visible crate this far away, while still walking (was 60 m)
LOOT_BOOST = True      # boost-run on loot trips and exploring longer than 15 m ...
LOOT_BOOST_E = 55.0    # ... while energy is above this (keeps the rest for fights and escapes)

# ---- flying when several enemies are close: hop and jet at random heights so we're not a still target between them
FLY = True
FLY_MIN_ENEMIES = 2    # enemy mechs in sight ...
FLY_NEAR_R = 100.0     # ... within this many metres
FLY_JET_S = (0.15, 0.6)  # random jet time per hop (s): ~3-8 m high, ~2-9 energy
FLY_HOP_P = 0.5        # chance per grounded decision to hop again (irregular rhythm)
FLY_MAX_H = 10.0       # stop jetting this high above the terrain (don't climb onto roofs)
FLY_RESERVE = 20.0     # never jet below this energy (+25 more when we hold lightning: it fires on energy)
