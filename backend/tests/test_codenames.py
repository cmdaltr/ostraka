"""Code names: two words from the lists, never a threat group's vocabulary or Ostraka's own."""

from ostraka import codenames

# The words vendors name threat groups from, and words that would read as a fact about the case
KEPT_OUT = {
    # animals (CrowdStrike's Bear, Panda, Kitten, Spider, Chollima, and the rest)
    "bear", "panda", "kitten", "cat", "spider", "tiger", "chollima", "jackal", "buffalo", "leopard",
    "lynx", "wolf", "fox", "falcon", "eagle", "hawk", "owl", "crane", "ant", "bee", "wasp", "hornet",
    "turtle", "snake", "viper", "cobra", "python", "scorpion", "dragon", "horse", "mustang", "lion",
    "shark", "whale", "dolphin", "raven", "crow", "sparrow", "badger", "otter", "wombat", "kangaroo",
    # weather (Microsoft's Typhoon, Blizzard, Sandstorm, Sleet, Tempest and the rest)
    "storm", "stormy", "typhoon", "blizzard", "sandstorm", "sleet", "tempest", "hail", "rain", "snow",
    "frost", "thunder", "lightning", "cloud", "fog", "mist", "wind", "gale", "cyclone", "hurricane",
    "tornado", "flood", "tsunami", "dust",
    # colours (PwC, Recorded Future, TLP), metals and elements (Secureworks, Microsoft's old names), gems
    "red", "blue", "green", "amber", "gold", "golden", "silver", "black", "white", "grey", "crimson",
    "scarlet", "violet", "purple", "pink", "orange", "yellow", "brown", "teal", "indigo", "ivory",
    "jade", "charcoal", "iron", "bronze", "copper", "cobalt", "tin", "lead", "zinc", "nickel",
    "platinum", "mercury", "carbon", "gallium", "hafnium", "nobelium", "strontium", "brass", "granite",
    "ruby", "sapphire", "emerald", "opal", "diamond", "pearl", "onyx", "topaz", "garnet", "moonstone",
    # stars, constellations (Unit 42), Greek letters, and vendors' prefixes
    "star", "sun", "moon", "comet", "ursa", "taurus", "pisces", "scorpius", "libra", "draco", "orion",
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "theta", "lambda", "sigma", "omega",
    "earth", "water", "void", "dark", "shadow", "silent", "fancy", "cozy", "charming", "scattered",
    "night", "midnight", "ghost", "phantom", "ocean", "sea", "desert", "lotus", "salt", "flax", "linen",
    "silk", "velvet", "ember", "wizard", "transparent", "patchwork", "atlas", "equation", "halo",
    # Ostraka's own words: phases, severities, TLP, statuses, palettes
    "detect", "scope", "contain", "eradicate", "recover", "report", "learn", "hypothesis", "analyse",
    "critical", "high", "medium", "low", "clear", "strict", "open", "closed", "ledger", "ostrakon",
    "clock", "late", "early",
    # an analyst's words
    "beacon", "key", "shell", "tunnel", "payload", "implant", "worm", "trojan", "phish", "ransom",
}  # fmt: skip


def test_no_word_is_a_threat_groups_or_ostrakas():
    words = {w.lower() for w in codenames.QUALITIES + codenames.THINGS}
    assert not words & KEPT_OUT


def test_the_lists_are_single_capitalised_words_without_repeats():
    for words in (codenames.QUALITIES, codenames.THINGS):
        assert len(set(words)) == len(words)
        assert all(w.isalpha() and w[0].isupper() and w[1:].islower() for w in words)
    assert not set(codenames.QUALITIES) & set(codenames.THINGS)


def test_a_name_is_read_in_any_case_and_written_one_way():
    assert codenames.canonical("folded LANTERN") == "Folded Lantern"
    assert codenames.canonical("  Quiet   Kiln ") == "Quiet Kiln"
    for name in ("Finance ransomware", "Folded", "Folded Lantern Bridge", "Stormy Tiger", ""):
        assert codenames.canonical(name) is None


def test_a_generated_name_is_valid_and_not_taken():
    picks = iter(["Folded", "Lantern", "Folded", "Lantern", "Quiet", "Kiln"])
    name = codenames.generate({"folded lantern"}, choose=lambda _: next(picks))
    assert name == "Quiet Kiln" and codenames.canonical(name) == name


def test_when_guessing_keeps_missing_it_walks_the_pairs():
    first = f"{codenames.QUALITIES[0]} {codenames.THINGS[0]}"
    taken = {first}
    name = codenames.generate(taken, choose=lambda words: words[0])
    assert name == f"{codenames.QUALITIES[0]} {codenames.THINGS[1]}"
