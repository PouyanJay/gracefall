"""gracefall CLI. Pipe numbers in, get graphics out.

    seq 1 20 | gracefall spark
    gracefall meter 62%
    gracefall flow build:done test:active deploy:pending
    gracefall demo

OSC policy: envelopes are emitted only when stdout is a terminal, so pipes,
files, and pagers get pure fallback by default. Use --force-osc to save a
stream (for cat, or for a terminal that implements OSC 4700) and --no-osc
to strip unconditionally.
"""

import argparse
import math
import os
import random
import sys

from . import (__version__, dist, flow, heat, meter, scatter, spark,
               strip_spans)


def _floats_from(args_list):
    if args_list:
        toks = args_list
    else:
        if sys.stdin.isatty():
            raise SystemExit("no data: pass numbers as arguments or on stdin")
        toks = sys.stdin.read().replace(",", " ").split()
    if not toks:
        # Real pipelines produce nothing all the time: a grep that misses, a
        # log with no lines yet. That should say so, not raise.
        raise SystemExit("no data: stdin was empty")
    try:
        return [float(t) for t in toks]
    except ValueError:
        bad = next(t for t in toks if not _isfloat(t))
        raise SystemExit(f"not a number: {bad!r}")


def _isfloat(t):
    try:
        float(t)
        return True
    except ValueError:
        return False


def _pairs_from_stdin():
    if sys.stdin.isatty():
        raise SystemExit("scatter reads 'x y' pairs from stdin, one per line")
    pts = []
    for line in sys.stdin:
        toks = line.replace(",", " ").split()
        if len(toks) >= 2:
            pts.append((float(toks[0]), float(toks[1])))
    if len(pts) < 2:
        raise SystemExit("need at least two x/y pairs")
    return pts


def _rows_from_stdin():
    if sys.stdin.isatty():
        raise SystemExit("heat reads rows of numbers from stdin")
    rows = []
    for line in sys.stdin:
        toks = line.replace(",", " ").split()
        if toks:
            rows.append([float(t) for t in toks])
    if not rows:
        raise SystemExit("no rows on stdin")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise SystemExit("all rows must have the same number of values")
    return rows


def _pct(s):
    v = float(s[:-1]) / 100 if s.endswith("%") else float(s)
    if v > 1:
        raise SystemExit("meter takes 0..1 or a percentage like 62%")
    return v


def build_demo():
    random.seed(7)
    from . import SGR, R
    D, F = SGR["dim"], SGR["fg"]
    toks = [128 + 26 * math.sin(i / 3.2) + random.uniform(-9, 9) + i * 1.1
            for i in range(26)]
    ttft = [max(80, random.lognormvariate(5.45, 0.34)) for _ in range(240)]
    pts = []
    for _ in range(46):
        b = random.uniform(1, 32)
        pts.append((b, 58 + 6.4 * b + random.gauss(0, 26)))
    grid = []
    for n in range(8):
        row = []
        for hr in range(24):
            v = (0.45 + 0.4 * math.sin((hr - 8 - n) / 3.6)
                 + 0.12 * math.sin(n * 2.1) + random.uniform(-0.08, 0.08))
            row.append(max(0.02, min(1.0, v)))
        grid.append(row)
    p50 = sorted(ttft)[len(ttft) // 2]
    p99 = sorted(ttft)[int(len(ttft) * 0.99)]
    L = []
    L.append(f"{F}gracefall demo{R}  {D}\u00b7 model-serving node \u00b7 "
             f"one stream, two renderings{R}")
    L.append("")
    L.append(f"{D}tok/s    {R}" + spark(toks, "teal", style="area")
             + f"  {F}{toks[-1]:.0f}/s{R} {SGR['teal']}\u25b4 8.3%{R}")
    L.append(f"{D}ttft     {R}" + dist(ttft, bins=26, color="blue")
             + f"  {D}p50{R} {F}{p50:.0f}ms{R} {D}\u00b7 p99{R} "
             f"{F}{p99:.0f}ms{R}")
    L.append("")
    L.append(f"{D}kv cache {R}" + meter(18.4 / 24.0, 24, "violet")
             + f"  {F}18.4 / 24.0 GB{R}")
    L.append(f"{D}memory   {R}" + meter(26.1 / 32.0, 24, "amber")
             + f"  {F}26.1 / 32.0 GB{R}")
    L.append("")
    L.append(f"{D}latency by batch size{R}" + " " * 22
             + f"{D}trend{R} {SGR['coral']}+6.4 ms/req{R}")
    L.append(" " * 9 + scatter(pts, w=30, h=4, color="coral", indent=9))
    L.append("")
    L.append(f"{D}pipeline {R}"
             + flow(["tokenize", "prefill", "decode", "stream"],
                    ["done", "done", "active", "pending"]))
    L.append("")
    L.append(f"{D}node \u00d7 hour throughput, last 24 h{R}")
    L.append(" " * 9 + heat(grid, color="teal", indent=9))
    return "\n".join(L) + "\n"


def main(argv=None):
    # The OSC policy flags live on a shared parent so they are accepted both
    # before and after the subcommand. SUPPRESS keeps the subparser from
    # overwriting a value the top-level parser already set, which is what
    # would otherwise make `gracefall --force-osc demo` silently a no-op.
    osc = argparse.ArgumentParser(add_help=False)
    osc.add_argument("--force-osc", action="store_true",
                     default=argparse.SUPPRESS,
                     help="emit envelopes even when stdout is not a tty")
    osc.add_argument("--no-osc", action="store_true",
                     default=argparse.SUPPRESS,
                     help="never emit envelopes, fallback only")

    p = argparse.ArgumentParser(
        prog="gracefall",
        parents=[osc],
        description="fallback-first graphics for terminals (OSC 4700)")
    p.add_argument("--version", action="version",
                   version=f"gracefall {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    color = dict(default=None, help="fg role: teal blue amber coral violet")

    sp = sub.add_parser("spark", help="inline trend line", parents=[osc])
    sp.add_argument("data", nargs="*")
    sp.add_argument("-c", "--color", **{**color, "default": "blue"})
    sp.add_argument("--style", choices=["line", "area"], default="line")
    sp.add_argument("-w", "--width", type=int, default=None,
                    help="downsample to this many columns")
    sp.add_argument("--lo", type=float)
    sp.add_argument("--hi", type=float)

    # The %% is required: argparse %-expands help text, and a bare % makes
    # the top-level --help raise ValueError while formatting this line.
    me = sub.add_parser("meter", help="horizontal gauge, 0..1 or N%%",
                        parents=[osc])
    me.add_argument("value")
    me.add_argument("-c", "--color", **{**color, "default": "teal"})
    me.add_argument("-w", "--width", type=int, default=24)

    di = sub.add_parser("dist", help="histogram of values", parents=[osc])
    di.add_argument("data", nargs="*")
    di.add_argument("-c", "--color", **{**color, "default": "blue"})
    di.add_argument("--bins", type=int, default=26)
    di.add_argument("--lo", type=float)
    di.add_argument("--hi", type=float)

    fl = sub.add_parser("flow", help="pipeline strip: name:status ...",
                        parents=[osc])
    fl.add_argument("stages", nargs="+",
                    help="name:done|active|pending|failed")

    sc = sub.add_parser("scatter", help="x/y pairs from stdin", parents=[osc])
    sc.add_argument("-c", "--color", **{**color, "default": "coral"})
    sc.add_argument("-w", "--width", type=int, default=30)
    sc.add_argument("--height", type=int, default=4)

    he = sub.add_parser("heat", help="rows of values from stdin",
                        parents=[osc])
    he.add_argument("-c", "--color", **{**color, "default": "teal"})

    sub.add_parser("demo", help="the full showcase", parents=[osc])

    st = sub.add_parser("strip", help="remove envelopes from a stream")
    st.add_argument("file", nargs="?")

    vi = sub.add_parser("view", help="paint spans as graphics in a terminal "
                                     "that speaks the kitty protocol")
    vi.add_argument("file", nargs="?", help="stream file, or stdin")
    vi.add_argument("--stats", action="store_true",
                    help="report backend, cell metrics, and spans found")
    vi.add_argument("--no-probe", action="store_true",
                    help="trust environment detection, never query the tty")
    vi.add_argument("--cell", metavar="WxH",
                    help="override cell size in pixels, for example 10x20")
    vi.add_argument("--watch", metavar="CMD",
                    help="re-run CMD on an interval and repaint in place")
    vi.add_argument("--interval", type=float, default=2.0,
                    help="seconds between --watch repaints, default 2")
    vi.add_argument("--placement", choices=["over", "under"], default="over",
                    help="over blanks the span's cells, under keeps the "
                         "fallback text and draws beneath it (z=-1)")

    sh = sub.add_parser("shell", help="run your shell inside gracefall, "
                                     "rendering every span as it appears")
    sh.add_argument("--shell", help="shell to run, default $SHELL")
    sh.add_argument("--cell", metavar="WxH",
                    help="override cell size in pixels, such as 10x20")
    sh.add_argument("--no-probe", action="store_true",
                    help="trust environment detection, never query the tty")
    sh.add_argument("--no-relaunch", action="store_true",
                    help="do not offer to reopen in a capable terminal")

    fm = sub.add_parser("fmt", help="add a chart to a command you already "
                                     "run (git log, df, du, ping, pytest)",
                        parents=[osc])
    fm.add_argument("recipe", nargs="?",
                    help="which recipe; omit for the list")
    fm.add_argument("args", nargs=argparse.REMAINDER,
                    help="the command's own arguments")

    ini = sub.add_parser("init", help="print the shell functions that turn "
                                      "recipes on: eval \"$(gfl init zsh)\"")
    ini.add_argument("shell", choices=["zsh", "bash"])

    re_ = sub.add_parser("render",
                         help="reference renderer: stream file to SVG")
    re_.add_argument("file")
    re_.add_argument("-o", "--out", default=None)
    re_.add_argument("--plain", action="store_true",
                     help="render the fallback view instead of enhanced")
    re_.add_argument("--png", action="store_true",
                     help="rasterize to PNG instead of SVG (needs the view "
                          "extra)")
    re_.add_argument("--cell", metavar="WxH", default="10x20",
                     help="cell size in pixels for --png, default 10x20")
    re_.add_argument("--bg", metavar="COLOR",
                     help="background for --png, for example '#10131a'")

    a = p.parse_args(argv)

    if a.cmd == "spark":
        out = spark(_floats_from(a.data), a.color, style=a.style,
                    lo=a.lo, hi=a.hi, width=a.width)
    elif a.cmd == "meter":
        out = meter(_pct(a.value), a.width, a.color)
    elif a.cmd == "dist":
        out = dist(_floats_from(a.data), bins=a.bins, color=a.color,
                   lo=a.lo, hi=a.hi)
    elif a.cmd == "flow":
        pairs = [s.split(":", 1) for s in a.stages]
        if any(len(x) != 2 for x in pairs):
            raise SystemExit("each stage must be name:status")
        out = flow([n for n, _ in pairs], [s for _, s in pairs])
    elif a.cmd == "scatter":
        out = scatter(_pairs_from_stdin(), w=a.width, h=a.height,
                      color=a.color)
    elif a.cmd == "heat":
        out = heat(_rows_from_stdin(), color=a.color)
    elif a.cmd == "demo":
        out = build_demo().rstrip("\n")
    elif a.cmd == "strip":
        text = (open(a.file, encoding="utf-8").read() if a.file
                else sys.stdin.read())
        sys.stdout.write(strip_spans(text))
        return 0
    elif a.cmd == "view":
        from .view import run as view_run
        if a.watch:
            text = ""          # --watch produces the stream each cycle
        elif a.file:
            text = open(a.file, encoding="utf-8").read()
        elif sys.stdin.isatty():
            raise SystemExit("view reads a stream from a file or stdin")
        else:
            text = sys.stdin.read()
        return view_run(text, a)
    elif a.cmd == "shell":
        from .shell import run as shell_run
        return shell_run(a)
    elif a.cmd == "init":
        from .recipes import init_script
        sys.stdout.write(init_script(a.shell))
        return 0
    elif a.cmd == "fmt":
        from . import recipes
        if not a.recipe:
            for name in recipes.names():
                r = recipes.get(name)
                print(f"  {name:<8} {r['help']}")
            print("\nturn them on:  eval \"$(gfl init zsh)\"   "
                  "(or bash) in your rc file")
            return 0
        r = recipes.get(a.recipe)
        if r is None:
            raise SystemExit(f"no recipe {a.recipe!r}; `gfl fmt` lists them")
        argv = a.args[1:] if a.args[:1] == ["--"] else a.args
        # Not the case this recipe is for: say nothing, the shell function
        # runs the real command next.
        if not r["matches"](argv):
            return 0
        if r["mode"] == "wrap":
            return r["fn"](argv, _emit_osc(a))
        chart = r["fn"](argv)
        if not chart:
            return 0
        out = chart
    elif a.cmd == "render":
        stream = open(a.file, encoding="utf-8").read()
        stem = a.file.rsplit(".", 1)[0] + (".plain" if a.plain else "")
        if a.png:
            from .raster import build_palette, frame_png
            try:
                cw, chh = (int(v) for v in a.cell.lower().split("x"))
            except ValueError:
                raise SystemExit("--cell wants WIDTHxHEIGHT, such as 10x20")
            data, warn = frame_png(stream, cw, chh, build_palette(a.bg),
                                   enhanced=not a.plain)
            if warn:
                print(f"gracefall render: {warn}", file=sys.stderr)
            out_path = a.out or (stem + ".png")
            open(out_path, "wb").write(data)
        else:
            from .render import render
            svg = render(stream, enhanced=not a.plain, title=a.file)
            out_path = a.out or (stem + ".svg")
            open(out_path, "w", encoding="utf-8").write(svg)
        print(f"wrote {out_path}", file=sys.stderr)
        return 0
    else:  # pragma: no cover
        raise SystemExit(f"unknown command {a.cmd}")

    if not _emit_osc(a):
        out = strip_spans(out)
    sys.stdout.write(out + "\n")
    return 0


def _emit_osc(a):
    """The OSC policy: envelopes go out when stdout is a terminal, unless
    --no-osc; --force-osc overrides the tty check.

    GRACEFALL_FORCE_OSC exists for the nested case: a script that emits
    several spans is itself run with its stdout on a pipe, so the isatty
    rule would strip every envelope before the consumer ever sees one.
    `gfl view --watch` sets it for exactly this reason.
    """
    force = (getattr(a, "force_osc", False)
             or os.environ.get("GRACEFALL_FORCE_OSC") == "1")
    return ((sys.stdout.isatty() or force)
            and not getattr(a, "no_osc", False))


if __name__ == "__main__":
    raise SystemExit(main())
