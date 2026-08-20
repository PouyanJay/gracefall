"""The shaded figure.

A third drawing technique with a different failure mode from the other
two. `lanes` and `scatter` are on or off, so their risk is that a feature
lands between cells and vanishes. Tone's risk is the opposite: a gradient
that runs the whole range takes the silhouette apart, and a picture made
of gradients needs room before it says anything at all. These are the
invariants that hold it to that.
"""

import re

import pytest

from gracefall import MAX_ATTRS, RAMP, strip_spans
from gracefall.render import attrs_dict, parse
from gracefall.shade import (CELL_RATIO, COLS_MIN, MODEL_ASPECT, field,
                             render, rows)

SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
MOODS = ("idle", "working", "happy", "sad", "sleepy")


def plain(s):
    return SGR_RE.sub("", strip_spans(s))


def picture(cols, nrows, tick=0.0, mood="idle"):
    n = len(RAMP) - 1
    return ["".join(RAMP[max(0, min(n, int(v * n + 0.5)))] for v in row)
            for row in render(cols, nrows, tick, mood)]


def test_the_field_is_pure():
    """A frame has to be reproducible or it cannot be baked, and a baked
    frame that is not reproducible cannot be regenerated."""
    for _ in range(3):
        assert field(0.42, 0.51, 1.75, "happy") == field(0.42, 0.51, 1.75,
                                                         "happy")
    assert render(50, 16, 2.0) == render(50, 16, 2.0)


def test_density_stays_in_range():
    """`heat` is handed these directly with lo 0 and hi 1. A value outside
    would clamp silently and put a flat patch in the picture."""
    for mood in MOODS:
        for grid in (render(52, 16, t, mood) for t in (0.0, 1.3, 7.9)):
            flat = [v for row in grid for v in row]
            assert min(flat) >= 0.0 and max(flat) <= 1.0


def test_the_body_never_falls_to_nothing():
    """Shading that ran the whole range would take the silhouette apart:
    the lit half of the head would land on a space. Every cell of the
    figure has to be a character you can see."""
    grid = render(72, 26, 1.0, "idle")
    inked = [v for row in grid for v in row if v > 0.0]
    assert min(inked) >= 0.09, "a cell of the figure is nearly invisible"


def test_it_is_fitted_and_not_stretched():
    """The cell grid has whatever proportions the caller asked for and the
    cat has its own. Stretched to fill, a wide grid would hold a wide cat,
    which is a different animal."""
    def extent(cols, nrows):
        grid = render(cols, nrows, 1.0)
        cs = [c for r in grid for c, v in enumerate(r) if v > 0]
        rs = [i for i, r in enumerate(grid) if any(r)]
        return ((max(cs) - min(cs) + 1) / ((max(rs) - min(rs) + 1)
                                           * CELL_RATIO))
    square = extent(60, 30)
    wide = extent(120, 30)
    assert abs(square - MODEL_ASPECT) < 0.35
    assert abs(wide - square) < 0.35, "the cat stretched with the grid"


def test_every_mood_changes_the_picture():
    base = picture(72, 26, 1.0, "idle")
    for mood in ("happy", "sad", "sleepy"):
        assert picture(72, 26, 1.0, mood) != base, mood


def test_the_eyes_shut_and_the_lid_stays_light():
    """An open eye is a bright disc with a dark pupil, so the lid that
    closes over it has to stay bright. A dark line would give the face two
    slots where its eyes were."""
    grid = render(72, 26, 1.0, "sleepy")
    lids = [v for row in grid for v in row if 0.0 < v < 0.25]
    assert lids, "nothing light enough to read as a shut eye"


def test_it_animates():
    seen = {tuple(picture(64, 22, i * 0.25, "idle")) for i in range(24)}
    assert len(seen) > 6, f"only {len(seen)} distinct frames in six beats"


def test_a_row_is_one_heat_span_inside_the_cap():
    """The grid does not fit in one envelope; a row does, with room. This
    is why `rows()` returns a span per row rather than one for the grid."""
    for cols in (COLS_MIN, 78, 120):
        rs = rows(cols, 20, 1.0, "idle")
        assert len(rs) == 20
        for r in rs:
            _, spans, _ = parse(r)
            assert len(spans) == 1
            a = attrs_dict(spans[0]["attrs"])
            assert a["t"] == "heat" and a["style"] == "ramp"
            assert len(spans[0]["attrs"]) <= MAX_ATTRS


def test_a_whole_grid_would_not_fit_in_one_envelope():
    """The reason for the row split, asserted so nobody undoes it."""
    from gracefall import heat
    with pytest.raises(ValueError):
        heat(render(78, 30, 1.0), lo=0.0, hi=1.0, style="ramp")


def test_the_picture_survives_losing_the_colour():
    """The whole reason for the ramp. `half` carries a heat grid entirely
    in colour, so a pipe, a mono terminal and a screen reader all get a
    solid block of one character."""
    from gracefall import heat
    grid = render(72, 26, 1.0, "idle")
    ramp = plain("\n".join(rows(72, 26, 1.0, "idle")))
    half = plain("\n".join(heat([r], lo=0.0, hi=1.0) for r in grid))
    assert len(set(ramp) - {"\n"}) > 4, "the ramp lost its levels"
    assert len(set(half) - {"\n"}) <= 2, "half is expected to be flat here"
