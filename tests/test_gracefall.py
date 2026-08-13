import re
import subprocess
import sys

import gracefall as g
from gracefall.cli import build_demo
from gracefall.render import parse

SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


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
