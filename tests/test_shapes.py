"""Tests for the shared geometry core.

The golden snapshot is the drift detector between backends: the SVG
renderer and the terminal viewer both consume these exact shape lists, so
if the geometry moves, this test says so before either backend ships it.

Regenerate deliberately, never reflexively:

    GRACEFALL_UPDATE_GOLDEN=1 pytest tests/test_shapes.py
"""

import os
import pathlib

import pytest

from gracefall import shapes
from gracefall.render import CHH, CW, HDR, PAD, attrs_dict, parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
STREAM = ROOT / "examples" / "inference.gfall"
GOLDEN = pathlib.Path(__file__).parent / "golden" / "inference_shapes.txt"


def _spans():
    _, spans, _ = parse(STREAM.read_text(encoding="utf-8"))
    for sp in spans:
        a = attrs_dict(sp["attrs"])
        box = shapes.box_from_cells(sp["cells"], PAD, PAD + HDR, CW, CHH)
        yield a, box


def _dump():
    out = []
    for i, (a, box) in enumerate(_spans()):
        out.append(f"# span {i} t={a.get('t')} box={box}")
        out.extend(repr(s) for s in shapes.shapes_for(a, box))
    return "\n".join(out) + "\n"


def test_golden_shapes():
    got = _dump()
    if os.environ.get("GRACEFALL_UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(got, encoding="utf-8")
    assert GOLDEN.exists(), "missing golden: GRACEFALL_UPDATE_GOLDEN=1 pytest"
    assert got == GOLDEN.read_text(encoding="utf-8"), (
        "span geometry changed. If that was intentional, regenerate with "
        "GRACEFALL_UPDATE_GOLDEN=1 and eyeball the SVG before committing.")


def test_golden_covers_every_span_type():
    """A golden that silently stopped covering a type would be worse than
    no golden at all."""
    types = {a.get("t") for a, _ in _spans()}
    assert types == {"spark", "meter", "dist", "flow", "scatter", "heat"}


def test_every_shape_kind_has_an_svg_backend():
    """Adding a primitive to shapes.py without teaching the renderer to draw
    it would silently drop it from the output."""
    from gracefall.render import _to_svg
    kinds = {s[0] for a, box in _spans() for s in shapes.shapes_for(a, box)}
    assert kinds, "no shapes produced"
    for kind in kinds:
        example = next(s for a, box in _spans()
                       for s in shapes.shapes_for(a, box) if s[0] == kind)
        assert _to_svg(example, []), f"renderer drew nothing for {kind!r}"


@pytest.mark.parametrize("box", [(0, 0, 312, 24), (10, 5, 100, 20),
                                 (0, 0, 40, 24)])
def test_spark_dot_stays_inside_the_span_box(box):
    """A marker centered on the box edge is half undrawable, because SPEC.md
    confines a span's rendering to its own cells."""
    x, y, w, h = box
    a = {"t": "spark", "d": "1,5,2,9", "lo": "1", "hi": "9", "c": "teal"}
    dot = [s for s in shapes.shapes_for(a, box) if s[0] == "circle"][0]
    _, cx, cy, r, _, _, sw = dot
    outer = r + sw / 2
    assert x <= cx - outer and cx + outer <= x + w, "dot escapes horizontally"
    assert y <= cy - outer and cy + outer <= y + h, "dot escapes vertically"


def test_spark_curve_still_spans_the_full_box():
    """Holding the dot inside must not shrink the line it marks."""
    a = {"t": "spark", "d": "1,5,2,9", "lo": "1", "hi": "9", "c": "teal"}
    curve = [s for s in shapes.shapes_for(a, (0, 0, 312, 24))
             if s[0] == "curve"][0]
    assert curve[1][0][0] == 0
    assert curve[1][-1][0] == 312


def test_overlay_is_clipped_to_the_span_box():
    """SPEC.md confines the enhanced rendering to the span's cells. The
    spark's end dot is centered on the right edge, so an unclipped renderer
    draws 3.4px that no conforming terminal can show."""
    from gracefall.render import _overlay, parse
    stream = STREAM.read_text(encoding="utf-8")
    _, spans, _ = parse(stream)
    defs = []
    for sp in spans:
        out = _overlay(sp, defs)
        assert out[0].startswith('<g clip-path='), out[0]
        assert out[-1] == "</g>"
    assert sum(1 for d in defs if "clipPath" in d) == len(spans)


def test_bbox_ignores_padding_whitespace():
    """SPEC.md: receivers compute the rectangle from non-space cells, so
    indentation inside a multi-line span must not widen the box."""
    cells = [(0, 4, " "), (0, 5, "x"), (1, 4, " "), (1, 9, "y")]
    assert shapes.cell_bbox(cells) == (0, 5, 2, 5)
    assert shapes.cell_bbox([(0, 0, " ")]) is None


def test_box_from_cells_scales_with_cell_metrics():
    cells = [(1, 2, "x"), (1, 3, "x")]
    assert shapes.box_from_cells(cells, 0, 0, 10, 20) == (20, 20, 20, 20)
    assert shapes.box_from_cells(cells, 0, 0, 7, 15) == (14, 15, 14, 15)
    assert shapes.box_from_cells([(0, 0, " ")], 0, 0, 10, 20) is None


def test_unknown_type_draws_nothing():
    """SPEC.md requires an unimplemented type to fall back to its text. A
    backend that gets no shapes leaves the fallback alone."""
    assert shapes.shapes_for({"t": "gauge", "v": "0.5"}, (0, 0, 100, 24)) == []
    assert shapes.shapes_for({}, (0, 0, 100, 24)) == []
    assert shapes.shapes_for({"t": "meter", "v": "0.5"}, None) == []


def test_unknown_role_falls_back_rather_than_raising():
    a = {"t": "meter", "v": "0.5", "c": "chartreuse"}
    out = shapes.shapes_for(a, (0, 0, 100, 24))
    assert out[1][6] == ("lgrad", "teal", 0.55, 1.0, False)


@pytest.mark.parametrize("n", [2, 3, 8])
def test_catmull_rom_keeps_the_data_points(n):
    """Smoothing may bend the line between points, never move a point."""
    pts = [(float(i), float(i * i % 7)) for i in range(n)]
    start, segs = shapes.catmull_rom(pts)
    assert start == pts[0]
    assert len(segs) == n - 1
    assert [(s[4], s[5]) for s in segs] == pts[1:]


def test_flatten_tracks_the_curve_endpoints():
    pts = [(0.0, 0.0), (10.0, 10.0), (20.0, 0.0)]
    poly = shapes.flatten(*shapes.catmull_rom(pts), steps=8)
    assert poly[0] == pts[0]
    assert poly[-1] == pts[-1]
    assert len(poly) == 1 + 8 * 2


def test_meter_clamps_out_of_range_values():
    full = shapes.shapes_for({"t": "meter", "v": "3"}, (0, 0, 100, 24))
    assert full[1][3] == 100  # fill width, not 300
    assert shapes.shapes_for({"t": "meter", "v": "-1"},
                             (0, 0, 100, 24)) == full[:1]
