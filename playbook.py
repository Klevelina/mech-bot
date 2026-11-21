"""Playbook: how to fight, decided by what we carry and what the enemy carries.

Our role comes from our loadout; the enemy's from the most dangerous weapon it holds. Each pair has a preferred
distance band: too close -> back off to the band, too far (and we can take the fight) -> close to the middle,
inside -> stand and fight. The weapon triangle: lightning beats laser (shorts its charge), laser beats mortar
(cook-off +60%, outranges it), mortar beats lightning (knock-back, energy drain); the gun is neutral and
lands ~100% within 40 m but only 30-55% beyond 120 m against dodgers (offline aim range)."""

ROLES = ("gun", "brawler", "sniper", "hybrid")

# (our role, their main weapon) -> (min m, max m)
BANDS = {
    ("gun", "gun"): (40.0, 80.0),
    ("gun", "lightning"): (55.0, 90.0),     # stay out of its 45 m arc
    ("gun", "laser"): (0.0, 45.0),          # rush: it's slow while charging, our gun lands up close
    ("gun", "mortar"): (15.0, 30.0),        # too close for a safe lob

    ("brawler", "gun"): (25.0, 40.0),       # gun + lightning: get inside our arc
    ("brawler", "lightning"): (30.0, 60.0),
    ("brawler", "laser"): (0.0, 40.0),      # our arc cancels its charge
    ("brawler", "mortar"): (60.0, 90.0),    # mortar beats lightning: gun range, outside its comfort

    ("sniper", "gun"): (100.0, 200.0),      # gun + laser: outrange it, our beam can't be dodged
    ("sniper", "lightning"): (80.0, 200.0),  # never let its arc short our charge
    ("sniper", "laser"): (120.0, 200.0),
    ("sniper", "mortar"): (100.0, 200.0),   # cook-off

    ("hybrid", "gun"): (100.0, 180.0),      # laser + lightning: laser at range, arc if it closes
    ("hybrid", "lightning"): (60.0, 180.0),
    ("hybrid", "laser"): (0.0, 40.0),
    ("hybrid", "mortar"): (100.0, 200.0),
}


def our_role(weapons):
    kinds = {w["kind"] for w in weapons if w}
    las, lit = "laser" in kinds, "lightning" in kinds
    if las and lit:
        return "hybrid"
    if lit:
        return "brawler"
    if las:
        return "sniper"
    return "gun"


def their_main(kinds):
    """The weapon that decides how to fight them (most dangerous first)."""
    kinds = set(kinds or ())
    for k in ("lightning", "laser", "mortar"):
        if k in kinds:
            return k
    return "gun"


def band(role, enemy_kinds):
    return BANDS.get((role, their_main(enemy_kinds)), (40.0, 80.0))
