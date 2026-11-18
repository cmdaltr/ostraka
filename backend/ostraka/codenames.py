"""Code names: two plain English words, a quality and a thing ("Folded Lantern"), one per record.

A code name says nothing about the incident, so it may leave Ostraka where the title may not: a TLP:RED
notification carries the reference and the code name. That holds only while every code name comes from
these lists, so a name is checked against them, never taken as free text.

The words stay clear of the vocabularies vendors name threat groups from (animals, weather, colours,
metals and elements, gems, constellations, Greek letters, and prefixes such as Earth, Void or Silent),
so a code name is never mistaken for an actor, and of Ostraka's own words (phases, severities, TLP,
statuses) and an analyst's (beacon, key, shell, tunnel), so it never reads as a fact about the case.
`tests/test_codenames.py` holds the words kept out.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Collection

QUALITIES = (
    "Able", "Ample", "Brief", "Brisk", "Calm", "Candid", "Careful", "Civil", "Clever", "Constant",
    "Crisp", "Curious", "Daring", "Deft", "Distant", "Earnest", "Easy", "Even", "Exact", "Fair",
    "Faithful", "Fine", "Firm", "Fluent", "Folded", "Formal", "Frank", "Fresh", "Frugal", "Gentle",
    "Glad", "Grand", "Grateful", "Hardy", "Hollow", "Honest", "Humble", "Inner", "Jolly", "Just",
    "Keen", "Kind", "Lasting", "Lawful", "Level", "Little", "Lively", "Loyal", "Lucky", "Mellow",
    "Mild", "Modest", "Narrow", "Neat", "Nimble", "Noble", "Odd", "Orderly", "Outer", "Patient",
    "Plain", "Polite", "Proper", "Proud", "Prudent", "Quick", "Quiet", "Rapid", "Rare", "Ready",
    "Regular", "Remote", "Restful", "Rich", "Round", "Royal", "Rustic", "Settled", "Sharp", "Simple",
    "Sincere", "Slender", "Slow", "Smooth", "Sober", "Solid", "Sound", "Spare", "Square", "Steady",
    "Still", "Sturdy", "Subtle", "Swift", "Tall", "Tame", "Tender", "Thrifty", "Tidy", "Tranquil",
    "True", "Upright", "Useful", "Valiant", "Vast", "Vivid", "Wary", "Wide", "Willing", "Wise",
    "Witty", "Worthy", "Young", "Zealous",
)  # fmt: skip

THINGS = (
    "Anchor", "Archway", "Ballot", "Banner", "Barrow", "Bell", "Bench", "Boathouse", "Bookcase",
    "Bottle", "Bridge", "Buckle", "Button", "Cabin", "Canal", "Candle", "Canopy", "Caravan", "Carpet",
    "Cart", "Castle", "Cellar", "Chapel", "Chimney", "Cistern", "Cloister", "Compass", "Corridor",
    "Cottage", "Cradle", "Crossing", "Cupboard", "Curtain", "Dial", "Doorway", "Drawer", "Fence",
    "Ferry", "Fiddle", "Flask", "Fountain", "Furrow", "Gable", "Garden", "Gazette", "Glasshouse",
    "Granary", "Harbour", "Hearth", "Hedge", "Hinge", "Inkwell", "Jetty", "Jug", "Kettle", "Kiln",
    "Ladder", "Lantern", "Lattice", "Lever", "Library", "Lighthouse", "Locket", "Loom", "Mantel",
    "Market", "Meadow", "Mill", "Mirror", "Mosaic", "Needle", "Notebook", "Oar", "Orchard", "Paddle",
    "Pantry", "Parcel", "Pavilion", "Pier", "Pillar", "Pitcher", "Plough", "Porch", "Postcard",
    "Pulley", "Quarry", "Quill", "Railway", "Rampart", "Ribbon", "Rudder", "Saddle", "Satchel",
    "Scaffold", "Scroll", "Shelf", "Shutter", "Signpost", "Spindle", "Spire", "Staircase", "Statue",
    "Steeple", "Stile", "Teapot", "Terrace", "Thimble", "Threshold", "Timber", "Tower", "Trellis",
    "Trowel", "Turret", "Umbrella", "Valley", "Vineyard", "Wagon", "Wardrobe", "Wellspring", "Wharf",
    "Wheel", "Windmill", "Workshop",
)  # fmt: skip

_QUALITIES = {w.lower(): w for w in QUALITIES}
_THINGS = {w.lower(): w for w in THINGS}


def canonical(name: str) -> str | None:
    """The code name as Ostraka writes it, or None if it isn't a quality and a thing from the lists."""
    words = name.split()
    if len(words) != 2:
        return None
    quality, thing = _QUALITIES.get(words[0].lower()), _THINGS.get(words[1].lower())
    return f"{quality} {thing}" if quality and thing else None


def generate(taken: Collection[str], choose: Callable[[tuple[str, ...]], str] = secrets.choice) -> str:
    """A code name not in `taken` (compared without case). Some 14,000 pairs, so a free one turns up
    at once until thousands are in use; past that it walks the pairs in order rather than guess."""
    used = {t.lower() for t in taken}
    for _ in range(50):
        name = f"{choose(QUALITIES)} {choose(THINGS)}"
        if name.lower() not in used:
            return name
    for quality in QUALITIES:
        for thing in THINGS:
            if f"{quality} {thing}".lower() not in used:
                return f"{quality} {thing}"
    raise RuntimeError("Every code name is in use.")
