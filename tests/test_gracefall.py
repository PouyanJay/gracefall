import re
import subprocess
import sys

import gracefall as g
from gracefall.cli import build_demo
from gracefall.render import parse

SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _attrs(stream):
    """The envelope of a single-span stream, as a dict."""
    from gracefall.render import attrs_dict
    _, spans, _ = parse(stream)
    assert len(spans) == 1
    return attrs_dict(spans[0]["attrs"])


def _fallback(stream):
    """What a terminal with no idea what OSC 4700 is would print."""
    return SGR_RE.sub("", g.strip_spans(stream))


def test_fallback_is_clean_text():
    plain = SGR_RE.sub("", g.strip_spans(build_demo()))
    bad = [c for c in plain if ord(c) < 32 and c != "\n"]
    assert bad == [], "fallback must be pure printable text"


def test_spark_roundtrip():
    stream = g.spark([1, 4, 2, 8])
    _, spans, _ = parse(stream)
    assert len(spans) == 1
    from gracefall.render import attrs_dict
    a = attrs_dict(spans[0]["attrs"])
    assert a["t"] == "spark" and a["d"] == "1,4,2,8"


def test_lanes_roundtrip_and_fallback():
    from gracefall.render import attrs_dict
    stream = g.lanes([("b", "teal"), ("r", "blue"), (".", None), ("m", None)])
    _, spans, _ = parse(stream)
    a = attrs_dict(spans[0]["attrs"])
    assert a["t"] == "lanes" and a["d"] == "b:teal,r:blue,.,m:teal"
    fb = re.sub(r"\x1b\[[0-9;]*m", "", g.strip_spans(stream))
    assert fb == "\u2502\u2572 \u25cb"                # one glyph per cell
    try:
        g.lanes([("x", None)])
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown cell kind must raise")


def test_heat_takes_a_shared_scale():
    # Several heats drawn as neighbouring rows must agree on what "hot"
    # means, so lo/hi can be pinned like spark and dist already allow.
    from gracefall.render import attrs_dict
    _, spans, _ = parse(g.heat([[1, 2]], lo=0, hi=9))
    a = attrs_dict(spans[0]["attrs"])
    assert a["lo"] == "0" and a["hi"] == "9"
    _, spans, _ = parse(g.heat([[1, 2]]))
    a = attrs_dict(spans[0]["attrs"])
    assert a["lo"] == "1" and a["hi"] == "2"


def test_envelope_size_cap():
    _, spans, _ = parse(build_demo())
    assert spans, "demo must contain spans"
    for sp in spans:
        assert len(sp["attrs"]) <= g.MAX_ATTRS
    try:
        g.heat([[0.5] * 200 for _ in range(40)])
    except ValueError:
        pass
    else:
        raise AssertionError("oversized envelope must raise")


def test_multirow_bbox_is_rectangular():
    stream = " " * 9 + g.scatter([(1, 1), (2, 4), (3, 2), (4, 8)],
                                 w=10, h=3, indent=9)
    _, spans, _ = parse(stream)
    cells = [(r, c) for r, c, ch in spans[0]["cells"] if ch != " "]
    rows = sorted({r for r, _ in cells})
    assert len(rows) == 3
    for r in rows:
        assert min(c for rr, c in cells if rr == r) == 9


def _run(*args):
    return subprocess.run([sys.executable, "-m", "gracefall", *args],
                          capture_output=True, text=True)


def test_cli_strips_osc_when_piped():
    out = _run("spark", "1", "2", "3")
    assert out.returncode == 0
    assert "\x1b]4700" not in out.stdout
    assert "\u2581" in out.stdout
    forced = _run("--force-osc", "spark", "1", "2", "3")
    assert "\x1b]4700" in forced.stdout


def test_osc_flags_accepted_on_both_sides_of_the_subcommand():
    """The README documents `gracefall demo --force-osc`, so the policy
    flags must work after the subcommand as well as before it."""
    for args in (("--force-osc", "spark", "1", "2", "3"),
                 ("spark", "1", "2", "3", "--force-osc")):
        r = _run(*args)
        assert r.returncode == 0, f"{args} failed: {r.stderr}"
        assert "\x1b]4700" in r.stdout, f"{args} emitted no envelope"
    for args in (("--no-osc", "spark", "1", "2", "3"),
                 ("spark", "1", "2", "3", "--no-osc")):
        r = _run(*args)
        assert r.returncode == 0, f"{args} failed: {r.stderr}"
        assert "\x1b]4700" not in r.stdout, f"{args} leaked an envelope"


def test_help_works_for_every_parser():
    """A bare % in a subcommand's help string breaks argparse's %-expansion
    when the top-level parser renders the subcommand list."""
    top = _run("--help")
    assert top.returncode == 0, top.stderr
    assert "0..1 or N%" in top.stdout
    for cmd in ("spark", "meter", "dist", "flow", "scatter", "heat",
                "demo", "strip", "render"):
        r = _run(cmd, "--help")
        assert r.returncode == 0, f"{cmd} --help failed: {r.stderr}"


def test_readme_force_osc_demo_line_works():
    """The exact command the README tells people to run."""
    r = _run("demo", "--force-osc")
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("\x1b]4700") > 1


def test_empty_and_bad_input_fail_cleanly():
    """Real pipelines produce nothing all the time: a grep that misses, a
    log with no lines yet. That must say so, not raise a traceback."""
    for cmd in ("spark", "dist"):
        r = subprocess.run([sys.executable, "-m", "gracefall", cmd],
                           input="", capture_output=True, text=True)
        assert r.returncode == 1, cmd
        assert "Traceback" not in r.stderr, cmd
        assert "no data" in r.stderr, cmd
    r = subprocess.run([sys.executable, "-m", "gracefall", "spark"],
                       input="hello world", capture_output=True, text=True)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "not a number" in r.stderr


def test_force_osc_env_var_survives_a_pipe():
    """A script that emits several spans is itself run with stdout on a
    pipe, so the isatty rule would strip every envelope before the consumer
    saw one. `gfl view --watch` sets this for exactly that case."""
    import os
    env = dict(os.environ, GRACEFALL_FORCE_OSC="1")
    r = subprocess.run([sys.executable, "-m", "gracefall", "spark", "1", "2"],
                       capture_output=True, text=True, env=env)
    assert "\x1b]4700" in r.stdout
    # --no-osc must still win over the environment
    r = subprocess.run([sys.executable, "-m", "gracefall", "spark", "1", "2",
                        "--no-osc"], capture_output=True, text=True, env=env)
    assert "\x1b]4700" not in r.stdout


# --------------------------------------------------------------------------
# scatter as a drawing surface
#
# The type was built to plot measurements, where the data's own extent is
# the right canvas and a trend line is the point. Drawing a figure on it
# wants neither: a canvas derived from the points rescales the picture
# every time one moves, and a least-squares fit through a face is a line
# across the face.


def test_scatter_bounds_default_to_the_data():
    """The charting case is unchanged: no bounds given, the canvas is the
    extent of the points."""
    a = _attrs(g.scatter([(2, 5), (8, 11)], w=4, h=1))
    assert (a["xlo"], a["xhi"], a["ylo"], a["yhi"]) == ("2", "8", "5", "11")


def test_explicit_bounds_fix_the_canvas():
    """A figure drawn on its own extent breathes in and out with whatever
    its outermost dot is doing. Fixed bounds hold it still."""
    box = dict(w=13, h=4, xlo=0, xhi=25, ylo=0, yhi=15)
    wide = _fallback(g.scatter([(4, 4), (20, 12)], **box))
    narrow = _fallback(g.scatter([(4, 4), (20, 12), (12, 8)], **box))
    assert wide[:4] == narrow[:4], "an added point moved the existing ones"
    a = _attrs(g.scatter([(4, 4), (20, 12)], **box))
    assert (a["xlo"], a["xhi"], a["ylo"], a["yhi"]) == ("0", "25", "0", "15")


def test_a_point_outside_explicit_bounds_clamps_to_the_edge():
    """It must not wrap. A negative grid index is a valid Python index, so
    an out-of-bounds dot would silently appear on the opposite side."""
    box = dict(w=4, h=1, xlo=0, xhi=7, ylo=0, yhi=3)
    left = _fallback(g.scatter([(-40, 1)], **box))
    right = _fallback(g.scatter([(99, 1)], **box))
    assert left[0] != "⠀" and left[-1] == "⠀"
    assert right[-1] != "⠀" and right[0] == "⠀"


def test_trend_can_be_left_out_of_the_envelope():
    """SPEC.md requires a derived value *shipped* in the envelope to be
    honest, not that one is shipped."""
    with_ = _attrs(g.scatter([(1, 1), (2, 4), (3, 2)], w=4, h=1))
    assert "m" in with_ and "tb" in with_
    without = _attrs(g.scatter([(1, 1), (2, 4), (3, 2)], w=4, h=1,
                               trend=False))
    assert "m" not in without and "tb" not in without
    assert without["d"] == with_["d"], "the points are the same either way"


def test_a_scatter_with_no_trend_draws_no_trend_line():
    """The renderer already guards on both keys being present. This is the
    end of that: no keys, no line across the drawing."""
    from gracefall.shapes import shapes_for
    box = (0, 0, 120, 96)
    pts = [(1, 1), (2, 4), (3, 2)]
    lined = shapes_for(_attrs(g.scatter(pts, w=5, h=4)), box)
    plain = shapes_for(_attrs(g.scatter(pts, w=5, h=4, trend=False)), box)
    assert [s[0] for s in lined].count("line") == 1
    assert [s[0] for s in plain].count("line") == 0
    assert [s[0] for s in plain].count("circle") == len(pts)
