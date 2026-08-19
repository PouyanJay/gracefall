"""The creature's invariants.

It is a mascot, so it is easy to think of these as cosmetic. They are not.
A frame that is not pure cannot be golden-tested, a frame whose width
wobbles cannot be redrawn in place, and a limb that is not a v1 span type
is a protocol change wearing a costume.
"""

import re

import pytest

from gracefall import MAX_ATTRS, strip_spans
from gracefall.creature import (DEFAULTS, MOODS, SIZES, WIDTH, Creature,
                                mood_for)
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


@pytest.mark.parametrize("size", SIZES)
def test_width_is_constant_for_every_mood_and_tick(size):
    """Redraw in place is only safe if the width never wobbles: not
    between moods, not between ticks, not between lines of one frame."""
    for mood in MOODS:
        c = Creature(mood, dict(SIGNALS), size=size)
        assert c.width() == WIDTH
        for tick in range(30):
            assert len(visible(c.frame(tick))) == c.width()
            rows = c.lines(tick)
            assert len(rows) == size
            for row in rows:
                assert len(visible(row)) == c.width(), (mood, tick, row)


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


def test_every_span_is_a_v1_type():
    """The creature may not invent a type. If it ever seems to need one,
    that is the signal it is drifting toward pixels, which SPEC.md
    excludes on purpose."""
    seen = set()
    for c in every_creature():
        for tick in range(15):
            for row in c.lines(tick):
                _, spans, _ = parse(row)
                assert spans, "a row of the creature drew nothing"
                for sp in spans:
                    t = attrs_dict(sp["attrs"])["t"]
                    assert t in V1_TYPES, t
                    seen.add(t)
    assert seen == {"lanes", "spark", "meter", "heat", "scatter"}


def test_every_span_is_one_row():
    """Multi-row spans bring the bbox rules with them. Every limb stays
    inside its own row, so the creature is laid out by the caller."""
    for c in every_creature():
        for row in c.lines(3):
            _, spans, _ = parse(row)
            for sp in spans:
                assert {r for r, _, _ in sp["cells"]} == {0}


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


def test_it_works_with_no_signals_at_all():
    """The first thing any caller does."""
    c = Creature()
    assert len(visible(c.frame(0))) == WIDTH
    assert c.signals == DEFAULTS
    assert c.lines(0) == [c.frame(0)]
    for size in SIZES:
        assert len(Creature(size=size).lines(3)) == size


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


def test_every_mood_has_its_own_face():
    """Five moods that all looked the same would be one mood."""
    faces = {m: visible(Creature(m, size=1).frame(3)) for m in MOODS}
    assert len(set(faces.values())) == len(MOODS), faces


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


def test_the_arms_are_pinned_to_a_fixed_scale():
    """A spark left to scale itself turns a calm arm into a wild one,
    because lo and hi come from the data. The creature's arms are pinned,
    so a still creature is visibly still."""
    calm = Creature("sleepy", {"cpu": 0.0}, size=1)
    busy = Creature("working", {"cpu": 1.0, "rate": 8.0}, size=1)
    for c in (calm, busy):
        _, spans, _ = parse(c.frame(4))
        for sp in spans:
            a = attrs_dict(sp["attrs"])
            if a["t"] == "spark":
                assert a["lo"] == "0" and a["hi"] == "1"
    swing = []
    for c in (calm, busy):
        vals = []
        for tick in range(12):
            _, spans, _ = parse(c.frame(tick))
            for sp in spans:
                a = attrs_dict(sp["attrs"])
                if a["t"] == "spark":
                    vals += [float(v) for v in a["d"].split(",")]
        swing.append(max(vals) - min(vals))
    assert swing[0] < swing[1], "a busy creature must swing wider"


def test_a_failing_build_turns_the_body_coral():
    for mood in MOODS:
        c = Creature(mood, {"ci": "fail"}, size=2)
        _, spans, _ = parse(c.lines(1)[1])
        assert attrs_dict(spans[0]["attrs"])["c"] == "coral"


def test_a_dirty_tree_shows_on_the_crown():
    clean = Creature("idle", {"dirty": False}, size=4).lines(3)[0]
    dirty = Creature("idle", {"dirty": True}, size=4).lines(3)[0]
    assert clean != dirty
    assert "amber" in parse(dirty)[1][0]["attrs"]
    assert visible(clean) == visible(dirty), "the fallback keeps its shape"


def test_mood_for_reads_the_signals():
    assert mood_for(None) == "sleepy"
    assert mood_for({}) == "sleepy"
    assert mood_for({"ci": "fail", "cpu": 0.9}) == "sad"
    assert mood_for({"cpu": 0.8}) == "working"
    assert mood_for({"ci": "pass", "cpu": 0.01}) == "happy"
    assert mood_for({"dirty": True}) == "idle"


def test_it_fits_a_narrow_terminal():
    """The acceptance line from the issue: the creature redraws cleanly at
    40 columns, with the recipe margin and a label beside it."""
    from gracefall.recipes import MARGIN
    label = "  building "
    for c in every_creature():
        for row in c.lines(7):
            assert len(MARGIN) + len(label) + len(visible(row)) <= 40


def test_the_demo_carries_the_creature():
    """The golden covers what the demo covers, so the creature has to be
    in it or its geometry drifts unwatched."""
    from gracefall.cli import build_demo
    demo = visible(build_demo())
    face = visible(Creature("happy", {"cpu": 0.31, "rate": 0.4,
                                      "ci": "pass"}, size=4).lines(2)[1])
    assert face in demo
