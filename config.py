"""All switches and tuning numbers in one place. Flip a switch here to compare submissions."""

# ---- switches (flip to compare submissions)
LOOT = True            # go for items at all
LOOT_UNTIL = 150.0     # match time after which we only loot items close by
LOOT_LATE_DIST = 40.0  # ... "close by" after LOOT_UNTIL
LOOT_MAX_DIST = 110.0  # (160 in the 2.94 build) early-phase loot range
SAFE_AFTER = 6.0       # s without seeing an enemy mech / being hit by one before looting again
DANGER_R = 130.0       # skip items this close to where an enemy mech was seen recently
DANGER_S = 25.0        # ... "recently"
ENERGY_RESERVE = 25.0  # lightning won't spend below this (kept for boost/jet escapes)
ENERGY_RESERVE_LATE = 9.0  # late game: spend almost all of it
SWEEP = True           # sweep the torso while walking to spot more loot
WATCH_LAST = True      # the mech we fought went out of sight: keep facing where it should be for WATCH_LAST_S
WATCH_LAST_S = 4.0
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
DROP_DANGER_R = 60.0   # skip a drop if an enemy was seen/heard this close to it ... (100 in the 2.98 build)
DROP_DANGER_S = 10.0   # ... in the last this many seconds (20 in the 2.98 build)
DROP_FIGHT = True      # ... unless it's one enemy we'd outlast: the capture refills our HP, so we only need to live
DROP_WIN_R = 1.1       # fight odds (seconds until we die / seconds to kill it) needed to go for or contest a drop
                       # with an enemy there (contesting needed ~1.25 in the 2.98 build)
# ---- which match are we in? practice = the house squad (hunts only us, never fights itself); the final event =
# a free-for-all against other teams' bots. Deaths we didn't cause (before the zone gets deadly) mean free-for-all.
SITUATION = True
SITUATION_UNTIL = 300.0   # only count deaths before this match time (later the zone kills too)
SITUATION_MIN_ODD = 2      # this many deaths we didn't cause before deciding it's a free-for-all (was 1)
SITUATION_CONFIRM_S = 1.0  # the extra deaths must stay unexplained this long (a kill event may arrive a tick late)
# squad only: heal at cover between hunters (more come after each kill): (start below, heal up to) per phase;
# no hunting while healing. None = use the phase rules (PHASES)
SQUAD_HEAL = {"early": (0.6, 1.0), "mid": (0.75, 1.0), "late": (0.9, 1.0)}
# ---- vanish when surrounded (stealth.py): 2+ known enemies within VANISH_R and under fire -> run to a spot none
# of them can see, go silent (walk < 5 m/s, no boost, hold fire), heal; after VANISH_HOLD_S unseen judge whether they
# lost us. Still tracked twice in a row -> no more vanishing this match. Lost us -> those enemies are ambush targets.
VANISH = True
VANISH_R = 120.0       # "surrounded": this many m
VANISH_SPOT_R = 50.0   # hiding spot within this far
VANISH_HOLD_S = 6.0    # unseen this long (vision memory is 5 s) before judging
VISION_MEM_S = 5.0     # enemies' vision memory (config vision.memory_s)
VANISH_MAX_S = 30.0    # give up after this long in any case (healing while hidden included)
VANISH_LOSE_R = 1.0    # also vanish from a LONE attacker we're losing to (fight odds below this; DISENGAGE is 0.8)
VANISH_AFTER_KILL_S = 1.5  # also vanish right after a kill if another enemy is known within VANISH_R (no damage needed)
VANISH_HEAL_TO = 0.9   # they lost us: stay hidden and silent until this HP fraction (or VANISH_MAX_S)
VANISH_WALK = 0.38     # move length while hidden: 0.38 x 11.7 m/s = 4.4 m/s, under the 5 m/s footstep speed
VANISH_GIVEUP = 2      # tracked through it this many times in a row: stop vanishing
UNAWARE_S = 12.0       # an enemy that lost us stays an ambush target this long ...
UNAWARE_WIN_R = 1.0    # ... hunted at these odds (FIGHT_WIN_R otherwise; its back is x1.5 damage already)
# squad: focus fire - among enemy mechs we can hit, take the one closest to dying: score = distance (m) + FOCUS_HP_W x
# its HP, minus bonuses for being nearly dead, being our current target (stick to it), hitting us, closing in. Lowest wins
FOCUS = True
FOCUS_HP_W = 0.2       # m per HP point (1400 HP = 280 m: a damaged enemy further away beats a healthy one close by)
FOCUS_LOW = 100.0      # ... minus this below 30% HP
FOCUS_STICK = 120.0    # ... minus this for our current target
FOCUS_HITME = 40.0     # ... minus this if it hit us in the last 4 s
FOCUS_CLOSING = 15.0   # ... minus this if it's coming at us faster than 3 m/s
FOCUS_MAX_D = 240.0    # only targets this close
# squad: engage and pursue - go after an enemy that's inside our matchup's distance band (playbook), recently hit us,
# is coming at us, is badly hurt, or is aiming at us with a clear line - no "alone and odds 1.5" requirement.
# Close to the band; if it ducks out of our line of fire, follow it until we see it again
ENGAGE = True
ENGAGE_BAND_MIN = 45.0   # "inside our band": its upper edge, but at least this many m
ENGAGE_HIT_S = 5.0       # ... or it hit us this recently
ENGAGE_CLOSING = (3.0, 150.0)  # ... or it comes at us faster than this (m/s) within this (m)
ENGAGE_WEAK = (0.35, 150.0)    # ... or below this HP fraction within this
ENGAGE_AIMED_R = 170.0   # ... or its torso points at us with a clear line between us, within this
ENGAGE_MIN_HP = 0.4      # not below this HP fraction (our retreat / hide rules take over)
# squad: finish rush - an enemy below FINISH_RUSH_HP of its max: get close (FINISH_RUSH_R by our weapon) and finish it,
# while our HP is above FINISH_RUSH_MIN and at most 2 enemies are in sight within 150 m; boost in from further away
FINISH_RUSH = True
FINISH_RUSH_HP = 0.35
FINISH_RUSH_MIN = 0.45
FINISH_RUSH_R = {"gun": 18.0, "lightning": 22.0, "laser": 35.0}  # laser is hitscan: no need to be on top of it
# squad: all in - our cautious rules off (heal-to-full, vanish, laser hide, fleeing unhittable attackers, odds
# disengage, low-HP hiding, late-zone hold fire), one short retreat rule instead: back off (to cover close by) only when
# 2+ enemies are in sight within ALLIN_NEAR_R and our HP is below ALLIN_TWO_HP, 3+ and below ALLIN_THREE_HP, or ours is
# below ALLIN_LOW_HP while the target has clearly more - never while the target is nearly dead; back in ALLIN_BACK_S
# after none of that holds any more
SQUAD_ALL_IN = True
ALLIN_NEAR_R = 160.0
ALLIN_TWO_HP = 0.55
ALLIN_THREE_HP = 0.8
ALLIN_LOW_HP = 0.35      # ... with the target at least 10 points of HP fraction above us
ALLIN_TARGET_LOW = 0.25  # never retreat from a target below this (and us above 20%, at most 2 near)
ALLIN_BACK_S = 1.5
ALLIN_RECOVER = True      # below RECOVER_LO HP: retreat to cover and stay until RECOVER_HI (a 5.85 bot's last gain)
ALLIN_RECOVER_LO = 0.25
ALLIN_RECOVER_HI = 0.6
ALLIN_COVER_R = 45.0     # retreat to a spot within this far that the enemies in sight can't see (else straight away)
# laser: let go early when what's charged already kills the target (x margin), instead of giving it time to duck away
LASER_KILLSHOT = True
LASER_HOLD_LOST = True     # a charging laser that loses its shot keeps holding (not released into cover) ...
LASER_HOLD_LOST_S = 1.5    # ... for up to this long
LASER_GUN_LEAD = True     # while the laser charges, aim with the gun's lead (the gun keeps firing) ...
LASER_AIM_SWITCH_S = 0.2   # ... until this long before full charge, then onto the beam's straight line
LASER_FULL_TOL = 0.011    # a laser charge within this of full counts as full (the obs rounds to 0.01)
LASER_KILLSHOT_MARGIN = 0.85
# squad all in: walk over loose cubes close by during a fight (each is +4% HP and damage at once)
FIGHT_CUBES = True
FIGHT_CUBES_R = 70.0     # this close (55 until the cubes build)
FIGHT_CUBES_HURT = 0.57  # x this below half HP (40 m)
# squad all in: a laser we'd take is worth a long walk (gun + laser is the leaderboard loadout), any phase
LASER_SEEK = True
LASER_SEEK_R = 240.0
# squad all in: combat movement (combat.py) - with the target in our line of fire, every decision pick the best of
# staying or a COMBAT_STEP m step in 16 directions: our range, line of fire, hidden from others, strafing at random
COMBAT_MOVE = True
COMBAT_STEP = 6.0
COMBAT_OTHERS_R = 170.0  # other enemies this close count (up to 3)
COMBAT_DODGE_S = 0.7     # sidestep bullets that would hit us within this many seconds
# modes that keep their own movement (retreat, finishing, cubes, escapes, drop contests, pursuit out of sight)
COMBAT_MOVE_SKIP = ("RETREAT", "RUSH", "FIGHTCUBE", "FINISH", "ESCAPE", "CONTEST", "SNIPE", "PURSUE", "DISENGAGE",
                    "HIDE", "HIDDEN", "VANISH", "drop", "DROPWATCH")
# ---- squad all in, the rest of the plan:
# fight distance by what we hold (not the playbook bands): lightning (with energy) close, laser long, gun mid;
# without lightning stay out of an enemy lightning's / mortar's comfort zone; drones at their own distance
ALLIN_RANGE = {"lightning": 28.0, "laser": 120.0, "gun": 85.0, "npc": 58.0}
ALLIN_RANGE_VS = {"lightning": 64.0, "mortar": 80.0}  # at least this far from these (when we have no lightning)
ALLIN_SAFE_S = 3.0       # loot again this long after the last threat (SAFE_AFTER otherwise)
ALLIN_LOOT_R = 150.0     # loot this far in every phase (crates included)
ALLIN_DRONE_HP = 0.5     # farm drones only above this HP fraction
ALLIN_NO_DROPS = True    # skip supply drops and drop contests (they pull us into the open for 4 s)
ALLIN_NO_EXPLORE = True  # no exploring for loot: hold cover / loot what we've seen
# upgrade picks vs the squad: survival first, then the laser (if we hold one), then the gun
ALLIN_UPGRADES_LASER = ["plating", "armor", "laser_damage", "laser_charge", "nanites", "gun_damage", "gun_rate",
                        "lightning_efficiency", "lightning_range", "servos", "gun_mag", "gun_velocity", "gyros"]
ALLIN_UPGRADES = ["plating", "armor", "gun_damage", "nanites", "gun_rate", "lightning_efficiency", "lightning_range",
                  "laser_damage", "servos", "laser_charge", "gun_mag", "gun_velocity", "gyros"]
# all in: retreat to the hidden spot closest by WALKING distance (out of sight of up to 4 enemies, head and middle)
RETREAT_WALK = False  # (tested offline: finds fewer hidden spots than flank_cell, not shorter walks)
ALLIN_FULL_LEAD = True    # all in: always lead the full speed and take long gun shots (no learned lead / holding)
ALLIN_LOW_RESERVE = True  # all in: lightning fires down to the small late-game energy reserve
ALLIN_RELOAD = True       # all in: reload whenever nobody is in sight and the magazine is at 60% or less
# all in: strip what still interrupts fighting / looting: footsteps nearby don't stop looting, death sounds don't make
# loot targets (walking into that fight), the old fleeing-enemy chase (FINISH) is off (pursue + finish rush cover it)
ALLIN_STRIP = True
# ---- cubes build (squad all in): cubes are +4% HP and damage each up to 10 - the one thing that snowballs
KILL_PILE_FIRST_R = 120.0  # mid-fight: our kill's pile (its cubes + 2) within this far comes before anything but retreat
ALLIN_DRONE_V = 12.0       # a drone (2 cubes, weak shooter) is worth this (POWER_DRONE_V otherwise), any phase
CUBE_UPGRADES_UNTIL = 10   # below this many cubes: plating and our weapon's damage first
# squad all in, gear first: the first GEAR_UNTIL s we don't start fights with "harmless" mechs (dealt us under
# GEAR_HARM_DMG, no laser within GEAR_LASER_R, further than GEAR_CLOSE_R): they don't stop our looting and aren't
# targets; weapons count from GEAR_WEAPON_R, lasers from GEAR_LASER_SEEK_R. Anyone else is fought as usual
GEAR_FIRST = True
GEAR_UNTIL = 120.0
GEAR_HARM_DMG = 150.0
GEAR_LASER_R = 170.0
GEAR_CLOSE_R = 55.0
GEAR_WEAPON_R = 400.0
GEAR_LASER_SEEK_R = 300.0
GEAR_STRICT = True     # never harmless: a laser carrier at any distance, or more than GEAR_MAX_CUBES cubes
GEAR_MAX_CUBES = 3
# squad all in: gear sweeps (scan.py) - calm: full torso turn every SCAN_EVERY_NOLASER s without a laser (SCAN_EVERY
# with one); first sweep / no laser yet and energy >= SCAN_JET_E: jump and jet up during it (down to SCAN_JET_STOP)
SCAN = False            # (gear-sweep scored 3.85 vs 4.25 without: sweeps from cover / jet scans likely hurt)
SCAN_EVERY_NOLASER = 6.0
SCAN_EVERY = 15.0
SCAN_JET_E = 90.0
SCAN_JET_STOP = 35.0
SCAN_MECH_R = 150.0      # calm = no enemy that matters this close (harmless ones don't count while gearing) ...
SCAN_ANY_R = 50.0        # ... and no enemy at all this close
SCAN_MODES = ("cube", "pickup", "crate", "npc", "pile", "cover", "explore")
# zone first: outside the zone, or the shrinking circle would reach us before we could walk in (ZONE_PAD m spare,
# ZONE_SPARE_S s spare): go inside the next circle now, ahead of fights and retreats (only escapes come first)
ZONE_FIRST = True
ZONE_PAD = 8.0
ZONE_SPARE_S = 3.0
# combat movement: favour steps outside the target's front arc (our hits x1.5 there)
REAR_ARC = True
REAR_ARC_W = 20.0        # score for a step behind it (40% of that for its flank, 60-90 degrees off its aim)
# squad all in: a mech that hits our BACK (rear x1.5) and isn't our target becomes the target, so we turn our front to it
DEFEND_REAR = True
DEFEND_REAR_S = 1.0      # ... for a rear hit this recent
# free-for-all endgame (the final): with FFA_END_ALIVE or fewer left, don't start fights - leave the strongest to fight
# each other (a real final was won that way); fight back at anyone who hit us lately, finish anyone nearly dead
FFA_ENDGAME = True
FFA_END_ALIVE = 4
FFA_END_HIT_S = 5.0
FFA_END_WEAK = 0.3
# squad all in, gear details: without a laser, one within GRAB_LASER_R is grabbed even mid-fight (swapping out lightning
# / mortar); cubes stop pulling us once the stat bonus is capped (10), except on the way to the 15-cube upgrade
GRAB_LASER = True
GRAB_LASER_R = 40.0
CUBE_CAP_WANT = True
# squad all in: supply drops after all (full HP + 3 cubes), but only safe ones - within SAFE_DROP_R, nobody seen or heard
# within SAFE_DROP_DANGER_R of it in SAFE_DROP_DANGER_S s; given up as soon as someone shows up there; no contests
SAFE_DROPS = True
SAFE_DROP_R = 150.0
SAFE_DROP_DANGER_R = 100.0
SAFE_DROP_DANGER_S = 10.0
# late in a practice match: back to our cautious rules (heal to 90%, late hold fire, vanish when surrounded) from this
# match time - a team measured that staying in squad attack mode late cost them ~0.35 (265 s: next circle <= 70 m)
ALLIN_UNTIL = 265.0
# squad all in, streamlined fight decisions: retreat > finish rush > engage / pursue > combat movement; the older fight
# rules (lone-enemy hunt, flank / avoid / hide around other enemies, playbook band keep-out) are skipped
ALLIN_STREAMLINE = True
KILL_PILE = True       # (any match) our kill drops its cubes (+2) and weapons where it died: loot target right away

DROP_WAIT_CALM = True  # FREE-FOR-ALL ONLY (vs the squad, 2+ enemies at a drop are hunters coming for us, not a fight):
                       # a fight at the drop (2+ enemies, or one we wouldn't outlast, or someone else capturing):
                       # wait at cover nearby, hold fire unless shot, go in as soon as it's one we'd outlast or nobody
DROP_WATCH_R = (35.0, 60.0)  # ... the waiting spot's distance from the drop (close enough to reach the ring in time)
# ... by our role (playbook.our_role): (min m, max m from the drop, "hidden" from the fighters seen/heard there, or
# "los" = a clear line to the drop for sniping the capturer)
DROP_WATCH_BANDS = {"gun": (35.0, 60.0, "hidden"), "brawler": (25.0, 45.0, "hidden"),
                    "artillery": (40.0, 100.0, "hidden"), "sniper": (80.0, 160.0, "los"),
                    "hybrid": (80.0, 160.0, "los")}
CONTEST = True         # one mech capturing a drop, we reach the ring before it finishes and would win: go in
CONTEST_MIN_HP = 0.25  # ... only above this HP fraction (0.5 in the 2.98 build; the fight odds cover the rest)
CONTEST_MIN_HP_LATE = 0.5  # ... in the late phase (5 or fewer alive, or a small circle): the old floor
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
LIGHTNING_FIRST = False  # prefer lightning pickups/swaps all game (True until the laser-gun build)
LASER_FIRST = True     # gun + laser, like the top 4 in a real teams' free-for-all replay: a lightning gets swapped for
                       # a laser (never the other way), laser pickups worth more, and the gun is never dropped late
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
MORTAR_USE = False     # (True until the bundle build) take a mortar into an empty slot; lob over cover / at slow or bunched targets
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

# ---- power: get stronger faster (cubes = +4% HP and damage each, up to 10), then be careful because we're bigger
POWER = True
POWER_WEAPON_V = 12.0  # no laser/lightning yet: those pickups are worth this (a cube is 3), any phase ...
POWER_DRONE_V = 9.0    # ... early phase: a drone is worth this (2 cubes, weak shooter; was 5) ...
POWER_R = 180.0        # ... and both are gone for up to this far (other loot: the phase's normal range)
POWER_BIG_CUBES = 5    # from this many cubes (25% bigger, easier to hit):
POWER_BIG_EXPO = 2.0   # ... cover spots: open directions count this much more (more enclosed corners)
POWER_BIG_HIDE_HP = 0.45  # ... under fire, hide from everyone below this HP (normally HIDE_LOW_HP 0.30)
POWER_BIG_LOSE_R = 1.0    # ... leave a fight (to cover within DISENGAGE_R) below these odds (normally 0.8)
POWER_MORE = True      # more cubes: drones keep their POWER value/range in the mid game too, crates worth more
POWER_CRATE_V = 4.0    # ... a crate (1 cube + 50% weapon; was 2.5, a cube is 3); supply drops: see DROP_DANGER_R/_S

# ---- faster looting
LOOT_GIVEUP_S = 15.0   # give up on an item after this long (was 40 s)
LOOT_CRATE_SHOOT_R = 90.0  # start shooting a visible crate this far away, while still walking (was 60 m)
LOOT_BOOST = True      # boost-run on loot trips and exploring longer than 15 m ...
LOOT_BOOST_E = 55.0    # ... while energy is above this (keeps the rest for fights and escapes)
