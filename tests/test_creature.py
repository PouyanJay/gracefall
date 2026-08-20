"""The creature's invariants.

It is a mascot, so it is easy to think of these as cosmetic. They are not.
A frame that is not pure cannot be golden-tested, a frame whose width
wobbles cannot be redrawn in place, and a limb that is not a v1 span type
is a protocol change wearing a costume.
"""

import re

import pytest

from gracefall import MAX_ATTRS, strip_spans
from gracefall.creature import (_BLINK, _BLINK_HOLD, DEFAULTS, MIN_BRAILLE,
                                MIN_SHADED, MOODS, SIZES, WIDTH, WIDTHS,
                                Creature, mood_for)
from gracefall.render import attrs_dict, parse

SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
V1_TYPES = {"spark", "meter", "dist", "flow", "scatter", "heat", "lanes"}

SIGNALS = {"cpu": 0.62, "rate": 1.4, "latency": 0.3, "ci": "pass",
           "dirty": True}


def visible(s):
    """What a terminal with no idea what OSC 4700 is would show."""
    return SGR_RE.sub("", strip_spans(s))


def every_creature():
    for mood in MOODS:
        for size in SIZES:
            yield Creature(mood, dict(SIGNALS), size=size)


def test_frames_are_pure():
    """Same (mood, signals, size, tick), same bytes, every time and from
    every instance. Without this nothing downstream can be tested."""
    for mood in MOODS:
        for size in SIZES:
            a = Creature(mood, dict(SIGNALS), size=size)
            b = Creature(mood, dict(SIGNALS), size=size)
            for tick in (0, 1, 7, 13, 1000):
                assert a.lines(tick) == b.lines(tick)
                assert a.lines(tick) == a.lines(tick)
                assert a.frame(tick) == b.frame(tick)

def test_nothing_outside_the_arguments_moves_a_frame():
    """No clock and no randomness: two calls a moment apart, and a run
    with the module's random seed disturbed, must agree."""
    import random
    import time
    c = Creature("working", {"cpu": 0.4}, size=4)
    first = c.lines(3)
    random.seed(999)
    [random.random() for _ in range(10)]
    time.sleep(0.01)
    assert c.lines(3) == first

def test_a_frame_does_not_mutate_the_creature():
    c = Creature("happy", {"cpu": 0.5}, size=4)
    before = dict(c.signals)
    c.lines(9)
    c.frame(9)
    assert c.signals == before


def test_width_does_not_depend_on_the_signals():
    """A creature whose signals arrive one at a time must not resize
    under the caller."""
    c = Creature("idle", size=2)
    seen = {len(visible(row)) for row in c.lines(0)}
    for key, value in (("cpu", 1.0), ("rate", 400.0), ("latency", 90.0),
                       ("ci", "fail"), ("dirty", True), ("cpu", 0.0)):
        c.update(**{key: value})
        seen |= {len(visible(row)) for row in c.lines(4)}
    assert seen == {WIDTH}


def test_lines_and_frames_carry_no_newline():
    for c in every_creature():
        assert "\n" not in c.frame(2)
        for row in c.lines(2):
            assert "\n" not in row

def test_the_fallback_is_clean_text():
    """Law 4. Strip the envelopes and the SGR and what is left is cells."""
    for c in every_creature():
        for tick in (0, 5):
            for row in c.lines(tick):
                bad = [ch for ch in visible(row) if ord(ch) < 32]
                assert bad == []

def test_envelopes_fit_the_cap():
    for c in every_creature():
        for row in c.lines(6):
            _, spans, _ = parse(row)
            for sp in spans:
                assert len(sp["attrs"]) <= MAX_ATTRS

def test_missing_signals_fall_back_to_the_defaults():
    c = Creature("working", {"cpu": 0.9}, size=2)
    assert c.signals["rate"] == DEFAULTS["rate"]
    assert c.signals["cpu"] == 0.9

def test_update_merges_rather_than_replaces():
    c = Creature("idle", {"cpu": 0.5, "dirty": True})
    c.update(cpu=0.1)
    assert c.signals["cpu"] == 0.1 and c.signals["dirty"] is True
    c.update(ci="fail")
    assert c.signals["cpu"] == 0.1 and c.signals["ci"] == "fail"

def test_a_reading_that_has_not_arrived_yet_is_not_an_error():
    """The creature sits on a prompt line. A caller whose first poll has
    not returned passes None, and that must draw, not raise."""
    c = Creature("idle", {"cpu": None, "rate": None, "latency": None},
                 size=4)
    for row in c.lines(2):
        assert len(visible(row)) == WIDTH

def test_an_unknown_mood_is_refused_on_the_way_in():
    with pytest.raises(ValueError):
        Creature("grumpy")
    c = Creature()
    with pytest.raises(ValueError):
        c.mood = "grumpy"
    assert c.mood == "idle"
    c.mood = "sleepy"
    assert c.mood == "sleepy"

def test_an_unknown_size_is_refused():
    with pytest.raises(ValueError):
        Creature(size=3)

def test_the_eyes_shut_and_open_again():
    """The blink is the animation's proof of life, and it is a pure
    function of the tick like everything else."""
    c = Creature("idle", size=1)
    shut = visible(c.frame(11))
    open_ = visible(c.frame(0))
    assert shut != open_
    assert "●" not in shut and "●" in open_
    assert "●" not in visible(c.frame(23))   # and again twelve ticks on
    assert all("●" in visible(c.frame(t)) for t in range(0, 11))
    asleep = Creature("sleepy", size=1)
    assert all("●" not in visible(asleep.frame(t)) for t in range(13))

def test_the_belly_reads_the_load():
    """The meter is the signal, not decoration."""
    for cpu in (0.0, 0.25, 1.0):
        c = Creature("idle", {"cpu": cpu}, size=2)
        _, spans, _ = parse(c.lines(1)[1])
        a = attrs_dict(spans[0]["attrs"])
        assert a["t"] == "meter" and float(a["v"]) == cpu

def test_a_failing_build_turns_the_body_coral():
    for mood in MOODS:
        c = Creature(mood, {"ci": "fail"}, size=2)
        _, spans, _ = parse(c.lines(1)[1])
        assert attrs_dict(spans[0]["attrs"])["c"] == "coral"

def test_mood_for_reads_the_signals():
    assert mood_for(None) == "sleepy"
    assert mood_for({}) == "sleepy"
    assert mood_for({"ci": "fail", "cpu": 0.9}) == "sad"
    assert mood_for({"cpu": 0.8}) == "working"
    assert mood_for({"ci": "pass", "cpu": 0.01}) == "happy"
    assert mood_for({"dirty": True}) == "idle"

#: The sizes something else may pick for you: the live line, a splash, a
#: prompt. These have to survive a narrow terminal. The larger ones are
#: only ever reached by asking for them by number, and asking for a fifty
#: six column cat in a forty column terminal is answered by the terminal.
FITS_NARROW = tuple(s for s in SIZES if WIDTHS[s] <= 26)


def test_it_fits_a_narrow_terminal():
    """The acceptance line from the issue: the creature redraws cleanly at
    40 columns, with the recipe margin and a label beside it."""
    from gracefall.recipes import MARGIN
    label = "  building "
    for size in FITS_NARROW:
        for mood in MOODS:
            c = Creature(mood, dict(SIGNALS), size=size)
            for row in c.lines(7):
                assert len(MARGIN) + len(label) + len(visible(row)) <= 40


def test_nothing_picks_a_wide_size_on_your_behalf():
    """The sizes a caller does not choose are the small ones. A recipe or
    a prompt that reached for a shaded cat would blow out the line it was
    sharing."""
    import inspect

    from gracefall import recipes, recipes_tui
    for fn in (recipes.companion, recipes_tui.splash):
        got = inspect.signature(fn).parameters.get("size")
        assert got is not None and got.default in FITS_NARROW, fn
    # `gfl replay` builds its reader's creature itself rather than taking
    # a size, so check the object it makes.
    from gracefall.replay import Narrator
    assert Narrator().creature.size in FITS_NARROW


def test_the_demo_carries_the_creature():
    """The golden covers what the demo covers, so the creature has to be
    in it or its geometry drifts unwatched."""
    from gracefall.cli import build_demo
    demo = visible(build_demo())
    rows = Creature("happy", {"cpu": 0.31, "rate": 0.4,
                              "ci": "pass"}, size=6).lines(2)
    for row in (visible(r) for r in rows):
        assert row.strip() in demo, row


# --------------------------------------------------------------------------
# motion
#
# The creature was animated long before it moved. Sampled at the rate it was
# drawn at, half of its frames were identical to the one before and two of
# its four rows never changed at all, so it read as a stutter with specks on
# it. These are the invariants that keep it moving.

def test_sampling_faster_gives_more_frames_not_faster_motion():
    """`--every` decides how finely the motion is sampled and nothing
    else. The frame at beat 3 is the frame at beat 3 however many frames
    were drawn on the way there, which is what lets the frame rate go up
    without the creature speeding up."""
    c = Creature("working", {"cpu": 0.4, "rate": 2.0}, size=4)
    coarse = [c.lines(i * 0.5) for i in range(7)]      # 2 frames a beat
    fine = [c.lines(i * 0.125) for i in range(25)]     # 8 frames a beat
    assert coarse[-1] == fine[-1], "beat 3 is beat 3 at any frame rate"
    assert coarse == fine[::4]

def test_the_belly_is_the_reading_and_never_a_wobble():
    """The obvious way to make a still creature look alive is to breathe
    with its belly. The belly is a meter of cpu, so breathing with it
    would make the number wrong: a fallback that disagrees with its data
    is the one thing the project does not ship."""
    for cpu in (0.0, 0.25, 0.61, 1.0):
        c = Creature("idle", {"cpu": cpu}, size=4)
        for tick in (0, 0.4, 1.7, 9.9, 137.5):
            _, spans, _ = parse(c.lines(tick)[3])
            a = attrs_dict(spans[0]["attrs"])
            assert a["t"] == "meter" and float(a["v"]) == cpu

def test_the_blink_does_not_depend_on_the_frame_rate():
    """The blink was `(tick + 1) % 12 == 0`, which is a test only a caller
    stepping by whole numbers ever passes. Sampled twenty times a second
    it was true for one frame in eighty, so raising the frame rate made
    the creature stop blinking."""
    c = Creature("idle", size=1)
    for step in (1.0, 0.5, 0.25, 0.1, 0.05):
        n = int(round(_BLINK / step))
        shut = [t for t in range(2 * n)
                if "●" not in visible(c.frame(t * step))]
        assert shut, f"no blink at all when sampled every {step} beats"
        # Two blinks in twenty-four beats, each about _BLINK_HOLD long.
        assert abs(len(shut) * step - 2 * _BLINK_HOLD) <= 2 * step


# --------------------------------------------------------------------------
# the body the cat actually has
#
# The creature used to be a lanes head with spark arms, a meter belly and a
# heat or scatter aura, each limb one row of its own. It is a cat now: one
# `scatter` for the drawing wherever there are dots enough to hold a face,
# a `lanes` figure where there are not, and a `meter` under both. The laws
# below are the old ones re-stated against the body that exists.


def joined(c, tick):
    """The creature as it is printed. A multi-row span opens on its first
    row and closes on its last, so a row on its own is not a stream and
    parsing one is meaningless."""
    return "\n".join(c.lines(tick))


def test_every_span_is_a_v1_type():
    """The creature may not invent a type. If it ever seems to need one,
    that is the signal it is drifting toward pixels, which SPEC.md
    excludes on purpose."""
    seen = set()
    for c in every_creature():
        for tick in range(15):
            _, spans, _ = parse(joined(c, tick))
            assert spans, "the creature drew nothing"
            for sp in spans:
                seen.add(attrs_dict(sp["attrs"])["t"])
    assert seen <= V1_TYPES, f"not a v1 type: {seen - V1_TYPES}"
    assert seen == {"scatter", "lanes", "meter", "heat"}


def test_the_drawing_is_one_span_not_one_per_row():
    """The cat is a single figure and its rows are not independent. Split
    across a span per row, each row's canvas would be derived from
    whatever happened to be drawn in that row, and the head would change
    width depending on how much of it was ears."""
    c = Creature("idle", size=8)
    _, spans, _ = parse(joined(c, 1.0))
    kinds = [attrs_dict(sp["attrs"])["t"] for sp in spans]
    assert kinds == ["scatter", "meter"], kinds


def test_width_is_constant_for_every_mood_and_tick():
    """A caller redraws in place. A frame whose width moves would leave
    the tail of the last one on screen."""
    for c in every_creature():
        assert c.width() == WIDTHS[c.size]
        for mood in MOODS:
            c.mood = mood
            for tick in range(12):
                for row in c.lines(tick * 0.37):
                    assert len(visible(row)) == c.width()


def test_the_compact_row_is_the_same_width_at_every_size():
    """`frame()` is what goes beside a chart on a live line. A caller
    laying out that line cannot ask the creature how wide it is going to
    be this time."""
    for size in SIZES:
        c = Creature("working", dict(SIGNALS), size=size)
        for tick in range(10):
            assert len(visible(c.frame(tick * 0.4))) == WIDTH


def test_it_works_with_no_signals_at_all():
    """The first thing any caller does."""
    c = Creature()
    assert len(visible(c.frame(0))) == WIDTH
    assert visible(c.frame(0)).strip(), "an unmeasured creature drew nothing"


def test_each_size_uses_the_technique_that_wins_at_that_size():
    """Three ways to draw and three ranges. A lane cell gets a whole glyph
    but no resolution, braille gets eight dots a cell but no tone, and
    tone gets ten levels a cell but needs room before a gradient says
    anything. The thresholds are the rule, not an accident of the
    drawing."""
    want = {}
    for size in SIZES:
        _, spans, _ = parse(joined(Creature("idle", size=size), 1.0))
        kinds = {attrs_dict(sp["attrs"])["t"] for sp in spans}
        if size < MIN_BRAILLE:
            want[size] = "lanes"
        elif size < MIN_SHADED:
            want[size] = "scatter"
        else:
            want[size] = "heat"
        assert want[size] in kinds, f"size {size} did not use {want[size]}"
        others = {"lanes", "scatter", "heat"} - {want[size]}
        assert not (kinds & others), f"size {size} mixed techniques: {kinds}"
    assert set(want.values()) == {"lanes", "scatter", "heat"}


def test_a_shaded_size_is_always_wide_enough_to_shade():
    """Tone below `shade.COLS_MIN` columns is mush, so no size may pick it
    without the columns to carry it."""
    from gracefall.shade import COLS_MIN
    for size in SIZES:
        if size >= MIN_SHADED:
            assert WIDTHS[size] >= COLS_MIN, size


def test_every_mood_has_its_own_face():
    """A mood a viewer cannot tell from another mood is not a mood."""
    seen = {m: visible(Creature(m, size=1).frame(0)) for m in MOODS}
    assert len(set(seen.values())) == len(MOODS), seen
    assert seen["happy"] != seen["sad"], "the mouth must turn"
    # In cells, not in colour: a mono terminal, a pipe and a screen reader
    # all take the colour away and the mood has to survive that.


def test_the_cat_moves_on_an_idle_machine():
    """Motion below one dot is not smaller motion, it is none: it rounds
    away and the frame is identical to the last. The bob is quantized to
    whole dots so a still machine still breathes."""
    for size in (s for s in SIZES if s >= MIN_BRAILLE):
        c = Creature("idle", {"cpu": 0.0}, size=size)
        frames = {joined(c, i * 0.1) for i in range(40)}
        assert len(frames) > 3, f"size {size} drew {len(frames)} frames in 4s"


def test_sampling_between_beats_shows_the_motion_between_them():
    """The tick is continuous, so a caller sampling faster sees more of
    the movement rather than each frame twice."""
    c = Creature("working", {"cpu": 0.5, "rate": 1.0}, size=8)
    coarse = {joined(c, i * 0.5) for i in range(24)}
    fine = {joined(c, i * 0.125) for i in range(96)}
    assert len(fine) > len(coarse)


def test_a_dirty_tree_shows_on_the_meter():
    """The crown is gone; the reading it coloured is not."""
    for size in (s for s in SIZES if s > 1):
        clean = Creature("idle", {"dirty": False}, size=size).lines(3)[-1]
        dirty = Creature("idle", {"dirty": True}, size=size).lines(3)[-1]
        assert "amber" in parse(dirty)[1][0]["attrs"]
        assert "amber" not in parse(clean)[1][0]["attrs"]
        assert visible(clean) == visible(dirty), "the fallback keeps its shape"


def test_the_meter_is_the_reading_and_never_a_wobble():
    """The one row that is a measurement rather than a drawing. Breathing
    with it would put a number on screen that is not the number."""
    for cpu in (0.0, 0.25, 0.61, 1.0):
        for size in (s for s in SIZES if s > 1):
            c = Creature("idle", {"cpu": cpu}, size=size)
            for tick in (0, 0.4, 1.7, 9.9, 137.5):
                a = attrs_dict(parse(c.lines(tick)[-1])[1][0]["attrs"])
                assert a["t"] == "meter" and float(a["v"]) == cpu
