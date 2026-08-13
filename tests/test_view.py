"""Tests for the kitty-graphics shim.

Everything here runs without a terminal. The cursor math and the chunking are
the parts that are silently wrong rather than loudly broken: an off-by-one
puts a chart on the wrong line, and a bad chunk boundary makes the terminal
eat the rest of the stream as image data.
"""

import base64
import io
import re

import pytest

from gracefall import spark
from gracefall.cli import build_demo
from gracefall.render import parse
from gracefall.view import (CHUNK, apc_chunks, backend_from_env, build_palette,
                            compose_text, image_sequence, mix, parse_color,
                            parse_cell_size_reply, place_moves)

pil = pytest.importorskip("PIL", reason="rasterizing needs Pillow")


# ------------------------------------------------------------ cursor math


def test_place_moves_reaches_the_span_and_returns():
    """Printing the block leaves the cursor at column 0 below it, so a span
    on row 2 of a 5 row block is 3 lines up."""
    before, after = place_moves(2, 5, 9)
    assert before == "\x1b[3A\x1b[9C"
    assert after == "\r\x1b[3B"


def test_place_moves_omits_zero_length_moves():
    """CSI 0 A still moves one line in some terminals, so a zero move must
    not be emitted at all."""
    before, after = place_moves(0, 1, 0)
    assert before == "\x1b[1A"
    assert after == "\r\x1b[1B"
    assert "[0C" not in before and "[0A" not in before


def test_place_moves_round_trips_for_every_row():
    for nrows in (1, 5, 30):
        for r0 in range(nrows):
            before, after = place_moves(r0, nrows, 0)
            up = sum(int(n) for n in re.findall(r"\x1b\[(\d+)A", before))
            down = sum(int(n) for n in re.findall(r"\x1b\[(\d+)B", after))
            assert up == down, "cursor must end where it started"


# --------------------------------------------------------------- chunking


def _payload(seq):
    return re.match(r"\x1b_G[^;]*;(.*)\x1b\\\\?$", seq, re.S).group(1)


def test_single_chunk_carries_control_keys_and_no_m():
    seqs = apc_chunks("AAAA", "a=T,f=100,c=4,r=1")
    assert len(seqs) == 1
    assert seqs[0] == "\x1b_Ga=T,f=100,c=4,r=1;AAAA\x1b\\"
    assert ",m=" not in seqs[0]


def test_chunked_payload_is_reassembled_exactly():
    b64 = base64.b64encode(bytes(range(256)) * 40).decode()
    assert len(b64) > CHUNK * 2, "need a genuinely multi-chunk payload"
    seqs = apc_chunks(b64, "a=T,f=100")
    assert "".join(_payload(s) for s in seqs) == b64
    assert all(len(_payload(s)) <= CHUNK for s in seqs)


def test_chunk_markers_open_and_close_the_transmission():
    b64 = "A" * (CHUNK * 2 + 7)
    seqs = apc_chunks(b64, "a=T,f=100")
    assert seqs[0].startswith("\x1b_Ga=T,f=100,m=1;")
    for mid in seqs[1:-1]:
        assert mid.startswith("\x1b_Gm=1;")
    assert seqs[-1].startswith("\x1b_Gm=0;")


def test_chunks_split_on_base64_quantum():
    """A boundary inside a 4 character group would corrupt the image."""
    b64 = base64.b64encode(b"x" * 9000).decode()
    for seq in apc_chunks(b64, "a=T")[:-1]:
        assert len(_payload(seq)) % 4 == 0


def test_empty_payload_produces_nothing():
    assert apc_chunks("", "a=T") == []


def test_image_sequence_declares_the_cell_box_and_holds_the_cursor():
    seq = image_sequence(b"\x89PNG fake", 12, 3)
    assert "a=T,f=100,c=12,r=3,C=1,q=2" in seq
    assert "z=-1" not in seq
    assert "z=-1" in image_sequence(b"\x89PNG fake", 12, 3, "under")


# ------------------------------------------------------------ text layout


def test_compose_text_blanks_only_the_span_cells():
    stream = "ab" + spark([1, 4, 2, 8]) + "cd"
    grid, spans, nrows = parse(stream)
    hide = {(r, c) for sp in spans for r, c, _ in sp["cells"]}
    text = compose_text(grid, nrows, hide)
    bare = re.sub(r"\x1b\[[0-9;]*m", "", text)
    assert bare.startswith("ab")
    assert bare.endswith("cd")
    assert "▁" not in bare, "span glyphs must be blanked"
    assert len(bare) == 8, "blanked cells keep their width"


def test_compose_text_without_hiding_keeps_the_fallback():
    stream = "x" + spark([1, 4, 2, 8])
    grid, spans, nrows = parse(stream)
    bare = re.sub(r"\x1b\[[0-9;]*m", "", compose_text(grid, nrows))
    assert "▁" in bare and "█" in bare


def test_compose_text_preserves_row_count_of_the_demo():
    grid, spans, nrows = parse(build_demo())
    text = compose_text(grid, nrows)
    assert len(text.split("\n")) == nrows


# ------------------------------------------------------------- detection


@pytest.mark.parametrize("env", [
    {"KITTY_WINDOW_ID": "1"},
    {"GHOSTTY_RESOURCES_DIR": "/x"},
    {"TERM_PROGRAM": "ghostty"},
    {"TERM_PROGRAM": "WezTerm"},
    {"TERM": "xterm-kitty"},
])
def test_env_detection_recognizes_capable_terminals(env):
    assert backend_from_env(env) == "env"


@pytest.mark.parametrize("env", [
    {}, {"TERM": "xterm-256color"}, {"TERM_PROGRAM": "Apple_Terminal"},
])
def test_env_detection_rejects_the_rest(env):
    assert backend_from_env(env) is None


def test_cell_size_reply_parsing():
    assert parse_cell_size_reply("\x1b[6;20;10t") == (10, 20)
    assert parse_cell_size_reply("\x1b[?1;2c") is None
    assert parse_cell_size_reply("") is None


@pytest.mark.parametrize("s,want", [
    ("#102030", (16, 32, 48)),
    ("#abc", (170, 187, 204)),
    ("rgb:1010/2020/3030", (16, 32, 48)),
    ("nonsense", None),
    ("", None),
])
def test_color_parsing(s, want):
    assert parse_color(s) == want


def test_palette_covers_every_role_shapes_can_emit():
    from gracefall.shapes import PSEUDO_ROLES, ROLES
    pal = build_palette()
    for role in ROLES + PSEUDO_ROLES:
        assert role in pal, f"palette is missing {role}"


def test_track_is_derived_from_the_background():
    """Hardcoding the groove would make the meter wrong on a light theme."""
    dark = build_palette((16, 19, 26))["track"]
    light = build_palette((250, 250, 250))["track"]
    assert dark != light
    assert sum(light) > sum(dark)


def test_mix_endpoints():
    assert mix((0, 0, 0), (100, 100, 100), 0) == (0, 0, 0)
    assert mix((0, 0, 0), (100, 100, 100), 1) == (100, 100, 100)


# ----------------------------------------------------------- rasterizing


def test_render_span_png_is_sized_to_its_cells():
    from PIL import Image
    from gracefall.view import render_span_png
    png, _ = render_span_png({"t": "meter", "v": "0.5", "c": "teal"},
                             24, 1, 10, 20, build_palette())
    img = Image.open(io.BytesIO(png))
    assert img.size == (240, 20)
    assert img.mode == "RGBA"


def test_render_span_png_actually_draws_something():
    from PIL import Image
    from gracefall.view import render_span_png
    png, _ = render_span_png({"t": "meter", "v": "0.62", "c": "teal"},
                             24, 1, 10, 20, build_palette())
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    data = getattr(img, "get_flattened_data", None) or img.getdata
    opaque = sum(1 for p in data() if p[3] > 8)
    assert opaque > 100, "meter rendered blank"


def test_gradient_paints_are_actually_opaque():
    """Regression: Image.getchannel("A") returns a copy, so writing alpha
    through it produced a fully transparent gradient. Every meter fill and
    every spark area silently vanished while the tests stayed green."""
    from gracefall.view import _gradient
    grad = _gradient((40, 10), (0, 0, 40, 10), build_palette(),
                     ("lgrad", "teal", 0.55, 1.0, False))
    rgba = grad.convert("RGBA")
    data = getattr(rgba, "get_flattened_data", None) or rgba.getdata
    alphas = [p[3] for p in data()]
    assert max(alphas) > 200, "gradient is transparent"
    assert min(alphas) > 100, "gradient lost its low stop"


def test_meter_fill_scales_with_its_value():
    """The bug above left both meters as empty grooves, and a test that only
    counted opaque pixels passed anyway because the track is opaque."""
    from PIL import Image
    from gracefall.view import render_span_png

    def colored(v):
        png, _ = render_span_png({"t": "meter", "v": v, "c": "teal"},
                                 24, 1, 10, 20, build_palette())
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        data = getattr(img, "get_flattened_data", None) or img.getdata
        # count pixels that are meaningfully teal, not the grey track
        return sum(1 for r, g, b, a in data() if a > 128 and g > r + 40)

    empty, half, full = colored("0"), colored("0.5"), colored("1")
    assert empty == 0, "an empty meter must show no fill"
    assert half > 500, "a half meter drew no fill"
    assert full > half * 1.6, "fill does not track the value"


def test_unknown_type_rasterizes_to_nothing():
    """SPEC.md: an unimplemented type must fall back to its text, so the
    viewer must not paint an empty image over it."""
    from gracefall.view import render_span_png
    png, _ = render_span_png({"t": "gauge", "v": "0.5"}, 10, 1, 10, 20,
                             build_palette())
    assert png is None


def test_build_output_covers_every_demo_span():
    from gracefall.view import build_output
    text, report, _ = build_output(build_demo(), 10, 20, build_palette())
    assert len(report) == 7
    assert all(note.endswith("B") for _, _, note in report), report
    assert text.count("\x1b_G") >= 7
    assert text.endswith("\x1b[0m")


def test_build_output_under_placement_keeps_the_text():
    from gracefall.view import build_output
    text, _, _ = build_output(build_demo(), 10, 20, build_palette(),
                              placement="under")
    assert "█" in text, "under mode must leave the fallback visible"
    assert "z=-1" in text
