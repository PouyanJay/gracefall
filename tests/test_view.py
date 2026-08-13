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
from gracefall.raster import build_palette, mix, parse_color
from gracefall.view import (CHUNK, apc_chunks, backend_from_env, compose_text,
                            image_sequence, parse_cell_size_reply, place_moves)

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
    {"TERM": "xterm-ghostty"},
])
def test_env_detection_recognizes_capable_terminals(env):
    assert backend_from_env(env) == "env"


@pytest.mark.parametrize("env", [
    {}, {"TERM": "xterm-256color"}, {"TERM_PROGRAM": "Apple_Terminal"},
])
def test_env_detection_rejects_the_rest(env):
    assert backend_from_env(env) is None


def test_ghostty_is_detected_by_everything_it_sets():
    """Measured from a real Ghostty 1.3.1: it exports all three, so any one
    of them alone must be enough."""
    for env in ({"GHOSTTY_RESOURCES_DIR": "/Applications/Ghostty.app/x"},
                {"TERM_PROGRAM": "ghostty"},
                {"TERM": "xterm-ghostty"}):
        assert backend_from_env(env) == "env", env


def test_failure_message_names_the_terminal():
    from gracefall.view import describe_terminal
    assert describe_terminal({"TERM_PROGRAM": "Apple_Terminal"}) == \
        "Apple_Terminal"
    assert describe_terminal({"TERM": "xterm-256color"}) == "xterm-256color"
    assert describe_terminal({}) == "unknown"


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
    from gracefall.raster import span_png as render_span_png
    png, _ = render_span_png({"t": "meter", "v": "0.5", "c": "teal"},
                             24, 1, 10, 20, build_palette())
    img = Image.open(io.BytesIO(png))
    assert img.size == (240, 20)
    assert img.mode == "RGBA"


def test_render_span_png_actually_draws_something():
    from PIL import Image
    from gracefall.raster import span_png as render_span_png
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
    from gracefall.raster import _gradient
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
    from gracefall.raster import span_png as render_span_png

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
    from gracefall.raster import span_png as render_span_png
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


# ------------------------------------------------------------ watch mode


def test_first_repaint_does_not_rewind():
    """Nothing has been drawn yet, so moving up would eat the caller's
    prompt and whatever else is above."""
    from gracefall.view import BSU, ESU, repaint_sequence
    seq = repaint_sequence("body", 0)
    assert seq.startswith(BSU) and seq.endswith(ESU)
    assert "\x1b[0J" not in seq
    assert "A" not in seq.replace("\x1b_Ga=d,d=A\x1b\\", "")


def test_repaint_rewinds_exactly_the_previous_frame():
    from gracefall.view import repaint_sequence
    seq = repaint_sequence("body", 7)
    assert "\x1b[7A\x1b[0J" in seq


def test_every_repaint_deletes_the_previous_images():
    """Overwriting the cells does not remove an image. Without an explicit
    delete they pile up until the terminal is drowning in them."""
    from gracefall.view import DELETE_IMAGES, repaint_sequence
    for prev in (0, 3, 22):
        assert DELETE_IMAGES in repaint_sequence("body", prev)


def test_repaint_is_wrapped_in_synchronized_output():
    """Without this the terminal can present a half-drawn frame, which is
    exactly the flicker the watch loop exists to avoid."""
    from gracefall.view import BSU, ESU, repaint_sequence
    seq = repaint_sequence("body", 4)
    assert seq.index(BSU) == 0
    assert seq.index(ESU) == len(seq) - len(ESU)
    assert seq.count(BSU) == 1 and seq.count(ESU) == 1


def test_cleanup_restores_the_cursor_and_clears_images():
    from gracefall.view import DELETE_IMAGES, SHOW_CURSOR, cleanup_sequence
    seq = cleanup_sequence()
    assert SHOW_CURSOR in seq
    assert DELETE_IMAGES in seq
    assert seq.endswith("\x1b[0m")


# --------------------------------------------------------- frame composer


def test_frame_png_sizes_to_the_grid_plus_padding():
    from PIL import Image
    from gracefall.raster import frame_png
    from gracefall.render import parse
    stream = build_demo()
    grid, _, nrows = parse(stream)
    ncols = max(c for _, c in grid) + 1
    data, _ = frame_png(stream, 10, 20, pad=12)
    assert Image.open(io.BytesIO(data)).size == (ncols * 10 + 24,
                                                 nrows * 20 + 24)


def test_frame_png_enhanced_and_plain_differ():
    """The whole point is that they are two renderings of one stream."""
    from gracefall.raster import frame_png
    enhanced, _ = frame_png(build_demo(), 10, 20)
    plain, _ = frame_png(build_demo(), 10, 20, enhanced=False)
    assert enhanced != plain


def test_frame_png_honours_the_background():
    from PIL import Image
    from gracefall.raster import frame_png
    data, _ = frame_png("hello", 10, 20, build_palette((250, 250, 250)))
    img = Image.open(io.BytesIO(data)).convert("RGB")
    assert img.getpixel((0, 0)) == (250, 250, 250)


# ---------------------------------------------- block elements as geometry


def test_block_rect_matches_the_unicode_fractions():
    """Block elements are defined as exact fractions of the cell. Drawing
    them as glyphs at a size that only approximates the cell leaves seams,
    which is what turned the heat grid into a smear."""
    from gracefall.raster import block_rect
    assert block_rect("█", 0, 0, 8, 16) == (0, 0, 8, 16)
    assert block_rect("▀", 0, 0, 8, 16) == (0, 0, 8, 8)
    assert block_rect("▄", 0, 0, 8, 16) == (0, 8, 8, 16)      # lower half
    assert block_rect("▁", 0, 0, 8, 16) == (0, 14, 8, 16)     # one eighth
    assert block_rect("▌", 0, 0, 8, 16) == (0, 0, 4, 16)      # left half
    assert block_rect("▏", 0, 0, 8, 16) == (0, 0, 1, 16)      # left eighth
    assert block_rect("A", 0, 0, 8, 16) is None


def test_block_rects_tile_without_gaps():
    """Adjacent full blocks must share an edge exactly, or the heat grid
    shows seams between its cells."""
    from gracefall.raster import block_rect
    a = block_rect("█", 0, 0, 8, 16)
    b = block_rect("█", 8, 0, 8, 16)
    assert a[2] == b[0]


def test_lower_eighths_increase_monotonically():
    from gracefall.raster import block_rect
    heights = [block_rect(chr(o), 0, 0, 8, 16)[1] for o in range(0x2581,
                                                                 0x2589)]
    assert heights == sorted(heights, reverse=True)
    assert heights[-1] == 0, "full block must fill the cell"


def test_braille_dots_land_on_the_two_by_four_grid():
    from gracefall.raster import braille_dots
    assert braille_dots("A", 0, 0, 8, 16) is None
    assert braille_dots("⠀", 0, 0, 8, 16) == []          # blank braille
    assert len(braille_dots("⣿", 0, 0, 8, 16)) == 8      # all eight dots
    one = braille_dots("⠁", 0, 0, 8, 16)                 # top-left only
    assert len(one) == 1
    x0, y0, x1, y1 = one[0]
    assert 0 < (x0 + x1) / 2 < 4 and 0 < (y0 + y1) / 2 < 4


def test_braille_dot_seven_is_bottom_left():
    """Dots 7 and 8 are the bottom row, and they are not in bit order with
    the rest, which is the usual way to get this wrong."""
    from gracefall.raster import braille_dots
    dot7 = braille_dots("⡀", 0, 0, 8, 16)[0]
    cx, cy = (dot7[0] + dot7[2]) / 2, (dot7[1] + dot7[3]) / 2
    assert cx < 4, "dot 7 is in the left column"
    assert cy > 12, "dot 7 is in the bottom row"


def test_fontset_reports_when_a_glyph_is_unavailable():
    """A missing glyph renders as a .notdef box, which has plenty of ink, so
    'did anything get drawn' is not a coverage test."""
    from gracefall.raster import FontSet
    fs = FontSet(20)
    assert fs.for_char("A") is not None
    assert fs.for_char("") is None, "private use must read as missing"


# -------------------------------------------------------------------- tmux


def test_tmux_is_detected_from_the_environment():
    from gracefall.view import tmux_passthrough_warning
    assert tmux_passthrough_warning({}) is None
    warn = tmux_passthrough_warning({"TMUX": "/tmp/tmux-501/default,1,0"})
    assert warn and "allow-passthrough on" in warn


def test_tmux_falls_back_rather_than_blanking_cells():
    """tmux drops the graphics sequences, and the shim cannot tell. Painting
    anyway blanks the span cells and then loses the images, which is
    strictly worse than the fallback that would have been there."""
    import io as _io

    class Args:
        no_probe = True
        stats = False
        cell = None
        placement = "over"
        watch = None

    out, err = _io.StringIO(), _io.StringIO()
    from gracefall.view import run
    stream = build_demo()
    rc = run(stream, Args(), out=out, env={"TERM": "xterm-kitty",
                                           "TMUX": "/tmp/x,1,0"},
             stderr=err)
    assert rc == 0
    assert "\x1b_G" not in out.getvalue(), "must not emit graphics into tmux"
    assert "█" in out.getvalue(), "fallback must survive intact"
    assert "allow-passthrough" in err.getvalue()


def test_tmux_override_lets_you_paint_anyway():
    import io as _io

    class Args:
        no_probe = True
        stats = False
        cell = "10x20"
        placement = "over"
        watch = None

    out, err = _io.StringIO(), _io.StringIO()
    from gracefall.view import run
    run(build_demo(), Args(), out=out,
        env={"TERM": "xterm-kitty", "TMUX": "/tmp/x,1,0",
             "GRACEFALL_TMUX_OK": "1"}, stderr=err)
    assert "\x1b_G" in out.getvalue()
