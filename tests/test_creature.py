"""The creature's invariants.

It is a mascot, so it is easy to think of these as cosmetic. They are not.
A frame that is not pure cannot be golden-tested, a frame whose width
wobbles cannot be redrawn in place, and a limb that is not a v1 span type
is a protocol change wearing a costume.
"""

import re

import pytest

from gracefall import MAX_ATTRS, strip_spans
from gracefall.creature import (_BLINK, _BLINK_HOLD, DEFAULTS, MOODS, SIZES,
                                WIDTH, Creature, mood_for)
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


# --------------------------------------------------------------------------
# motion
#
# The creature was animated long before it moved. Sampled at the rate it was
# drawn at, half of its frames were identical to the one before and two of
# its four rows never changed at all, so it read as a stutter with specks on
# it. These are the invariants that keep it moving.


def test_a_tick_may_be_fractional():
    """The tick is a beat, not a frame number. A caller sampling between
    two beats gets the motion between them, not the earlier one twice."""
    c = Creature("working", {"cpu": 0.5, "rate": 1.0}, size=4)
    assert c.lines(1.0) != c.lines(1.5) != c.lines(2.0)
    assert c.frame(0.25) != c.frame(0.75)


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


def test_every_row_but_the_belly_moves():
    """Two of the four rows used to be built by functions that took no
    tick at all, so half the creature was structurally incapable of
    moving. The belly is the exception on purpose: it is a meter of a
    real number and may not wobble for decoration."""
    c = Creature("working", {"cpu": 0.4, "rate": 1.0}, size=4)
    frames = [c.lines(i * 0.1) for i in range(60)]
    moved = {r for a, b in zip(frames, frames[1:])
             for r, (x, y) in enumerate(zip(a, b)) if x != y}
    assert moved == {0, 1, 2}, "crown, body and mouth rows all animate"
    assert len({f[3] for f in frames}) == 1, "the belly holds still"


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


def test_the_arms_move_even_on_an_idle_machine():
    """A calm arm swung 0.046 across a spark quantized to eighths, which
    is the same block every frame: animated in the numbers, still on the
    screen. There is a floor under the swing now."""
    for mood in MOODS:
        c = Creature(mood, {"cpu": 0.0}, size=1)
        seen = {visible(c.frame(t * 0.25)) for t in range(40)}
        assert len(seen) > 1, f"a {mood} creature's arms never move"


def test_the_air_has_no_seam_in_it():
    """The specks used to drift on `(x % 7) / 7`, a sawtooth: continuous
    everywhere except the wrap, where a speck jumped from the top of its
    cell to the bottom. In block characters that was one more
    indistinguishable step. Drawn, it is the only motion the eye follows,
    and it went the wrong way once a cycle."""
    for mood in ("working", "sleepy"):
        c = Creature(mood, size=4)
        ys = []
        for i in range(400):
            _, spans, _ = parse(c.lines(i * 0.05)[0])
            a = attrs_dict(spans[0]["attrs"])
            ys.append(float(a["d"].split(",")[1].split(":")[-1])
                      if ":" in a["d"] else float(a["d"].split(",")[1]))
        steps = [abs(b - a) for a, b in zip(ys, ys[1:])]
        assert max(steps) < 0.1, f"{mood}: a speck jumps {max(steps):.2f}"
