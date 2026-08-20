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

from . import (__version__, dist, flow, heat, lanes, meter, scatter, spark,
               strip_spans)
from .creature import MOODS, SIZES


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
    L.append("")
    L.append(f"{D}rollout history{R}")
    hist = [([("m", "teal"), (".", None), (".", None)], "merge hotfix into main"),
            ([("b", "teal"), ("r", "blue"), (".", None)], ""),
            ([("b", "teal"), (".", None), ("d", "blue")], "hotfix: kv cache eviction"),
            ([("b", "teal"), ("l", "blue"), (".", None)], ""),
            ([("d", "teal"), (".", None), (".", None)], "v2.4 rollout")]
    for cells, subject in hist:
        L.append(" " * 9 + lanes(cells) + (f"  {F}{subject}{R}" if subject else ""))
    L.append("")
    L.append(f"{D}the creature{R}  {D}· a scatter drawing and a meter of "
             f"the load, and nothing else{R}")
    from .creature import Creature
    busy = Creature("working", {"cpu": 0.74, "rate": 2.0, "dirty": True},
                    size=6)
    glad = Creature("happy", {"cpu": 0.31, "rate": 0.4, "ci": "pass"}, size=6)
    for a, b in zip(busy.lines(5), glad.lines(2)):
        L.append(" " * 5 + a + "    " + b)
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

    la = sub.add_parser("lanes", help="one row of a commit graph from cells "
                                       "such as b:teal r:blue . d:amber",
                        parents=[osc])
    la.add_argument("cells", nargs="+",
                    help="kind[:role]; kinds . b d m r l h (see SPEC.md)")

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
    fm.add_argument("--full", action="store_true",
                    help="the detailed view where a recipe has one (git "
                         "log, df); GFL_FULL=1 in the environment does the same")
    fm.add_argument("--no-pet", action="store_true", help="no creature on the live line of ping, pytest and iostat; GFL_PET=0 does the same")
    fm.add_argument("--watch", action="store_true",
                    help="redraw in place until ctrl-c, for recipes that "
                         "query for themselves (df, du, git)")
    fm.add_argument("--every", type=float, default=2.0, metavar="SECONDS",
                    help="seconds between --watch redraws, default 2")
    fm.add_argument("--around", nargs=argparse.REMAINDER, metavar="CMD",
                    help="run a full-screen tool (claude, vim, lazygit) with "
                         "the screen entirely its own, and print what the "
                         "session changed when it exits; takes the rest of "
                         "the line")
    fm.add_argument("recipe", nargs="?",
                    help="which recipe; omit for the list")
    fm.add_argument("args", nargs=argparse.REMAINDER,
                    help="the command's own arguments")

    gt = sub.add_parser("git", help="history as a reading format: gfl git "
                                    "log [git log arguments], gfl git graph",
                        parents=[osc])
    gt.add_argument("args", nargs=argparse.REMAINDER,
                    help="log or graph, then git log's own arguments; "
                         "--no-summary skips the charts on top of log, "
                         "--no-pager writes straight out")

    pe = sub.add_parser("pet", help="the creature, breathing in place until "
                                    "you press a key", parents=[osc])
    pe.add_argument("--mood", choices=list(MOODS),
                    help="hold one mood; the default follows the machine")
    # Big by default. `gfl pet` owns the screen it is on, and the cat has
    # four times the detail at eight rows that it has at four: the small
    # sizes exist for the live line and the prompt, where something else
    # owns the layout.
    pe.add_argument("--size", type=int, choices=list(SIZES), default=8,
                    help="lines to draw on, %s; default 8"
                         % ", ".join(str(s) for s in SIZES))
    # None rather than the number: pet.py owns the default, and importing
    # it here to name it would pull the recipe registry into every `gfl
    # spark`, which costs more than this argument is worth.
    pe.add_argument("--every", type=float, default=None, metavar="SECONDS",
                    help="seconds between frames, default 0.05 (20 a second)")
    pe.add_argument("--graphics", action="store_true",
                    help="draw the creature instead of drawing it in block "
                         "characters, in a terminal that speaks the kitty "
                         "graphics protocol (Ghostty, kitty, WezTerm)")
    pe.add_argument("--once", action="store_true",
                    help="print one frame and exit, for a prompt or a test")

    rp = sub.add_parser("replay", help="play a recorded stream back, with "
                                       "the creature reading it")
    rp.add_argument("file", help="a stream file, such as the one "
                                 "`gfl demo --force-osc > s.gfall` writes")
    rp.add_argument("--speed", type=float, default=0.0, metavar="N",
                    help="pace it at N times about 2000 cells a second; the "
                         "default writes the file out as it is")
    rp.add_argument("--pet", action="store_true",
                    help="the creature on the bottom line, reading the spans "
                         "as they go past; implies --speed 1")
    rp.add_argument("--no-pager", action="store_true",
                    help="write straight out instead of through less")

    bk = sub.add_parser("bake", help="render an animation to a flipbook "
                                     "file, once, ahead of time",
                        parents=[osc])
    bk.add_argument("-o", "--out", default="cat.flip", metavar="FILE",
                    help="where to write it, default cat.flip")
    bk.add_argument("--frames", type=int, default=120,
                    help="how many frames, default 120")
    bk.add_argument("--fps", type=float, default=30.0,
                    help="frames a second to record in the file, default 30")
    bk.add_argument("--cols", type=int, default=78,
                    help="cells wide, default 78")
    bk.add_argument("--rows", type=int, default=30,
                    help="terminal rows tall, default 30")
    bk.add_argument("--mood", choices=list(MOODS), default="idle",
                    help="hold one mood, default idle")
    bk.add_argument("--color", default="teal",
                    help="colour role, default teal")
    bk.add_argument("--beats", type=float, default=12.0,
                    help="animation beats the loop covers, default 12")

    pl = sub.add_parser("play", help="play a flipbook file in place")
    pl.add_argument("file", help="a file written by gfl bake")
    pl.add_argument("--fps", type=float, default=None,
                    help="override the rate recorded in the file")
    pl.add_argument("--once", action="store_true",
                    help="play once and stop instead of looping")

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
    elif a.cmd == "lanes":
        cells = []
        for c in a.cells:
            kind, _, role = c.partition(":")
            cells.append((kind, role or None))
        try:
            out = lanes(cells)
        except (ValueError, KeyError) as e:
            raise SystemExit(f"lanes: {e}")
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
    elif a.cmd == "git":
        from .gitlog import main as git_main
        return git_main(a.args, _emit_osc(a))
    elif a.cmd == "pet":
        from .pet import run as pet_run
        return pet_run(a, _emit_osc(a))
    elif a.cmd == "replay":
        from .replay import run as replay_run
        return replay_run(a)
    elif a.cmd == "bake":
        from . import flip, shade
        if a.cols < shade.COLS_MIN:
            raise SystemExit(
                f"--cols {a.cols} is too narrow to shade: below "
                f"{shade.COLS_MIN} the tone has nowhere to run. Use "
                f"`gfl pet` for the small sizes.")
        # A flipbook is a file, not stdout, so the isatty rule does not
        # decide this: the envelopes are the point of the artefact and they
        # go in unless --no-osc says otherwise.
        keep = not getattr(a, "no_osc", False)

        def draw(t):
            rows_ = shade.rows(a.cols, a.rows, t, a.mood, color=a.color)
            return rows_ if keep else [strip_spans(r) for r in rows_]

        book = flip.bake(draw, frames=a.frames, fps=a.fps,
                         label=f"cat {a.mood}", beats=a.beats)
        flip.write_file(a.out, book)
        print(f"{a.out}: {len(book)} frames, {book.cols}x{book.rows}, "
              f"{book.fps:g} fps, {os.path.getsize(a.out)} bytes",
              file=sys.stderr)
        return 0
    elif a.cmd == "play":
        from . import flip
        try:
            book = flip.read_file(a.file)
        except OSError as e:
            raise SystemExit(f"gfl play: {e}")
        except ValueError as e:
            raise SystemExit(f"gfl play: {a.file}: {e}")
        if a.fps:
            book.fps = a.fps
        if not sys.stdout.isatty():
            # The isatty rule, the same one every other command keeps: a
            # pipe gets the frames and no cursor games.
            try:
                for i in range(len(book)):
                    sys.stdout.write("\n".join(book.frame(i)) + "\n")
            except BrokenPipeError:          # `gfl play f | head`
                try:
                    sys.stdout.close()
                except BrokenPipeError:
                    pass
                os._exit(0)
            return 0
        from .pet import _cbreak, _stdin_fd, key_waiter
        fd = _stdin_fd()
        restore = _cbreak(fd) if fd is not None else None
        try:
            return flip.play(book, loop=not a.once,
                             wait=key_waiter(fd) if restore else None)
        finally:
            if restore:
                restore()
    elif a.cmd == "init":
        from .recipes import init_script
        sys.stdout.write(init_script(a.shell))
        return 0
    elif a.cmd == "fmt":
        from . import recipes
        if a.around is not None:
            from .recipes_tui import around
            return around(a.around, _emit_osc(a))
        if not a.recipe:
            for name in recipes.names():
                r = recipes.get(name)
                subs = recipes.subs(name)
                if not subs:
                    print(f"  {name:<12} {r['help']}")
                    continue
                for sname, e in sorted(subs.items()):
                    print(f"  {name + ' ' + sname:<12} {e['help']}")
            print("\nturn them on:  eval \"$(gfl init zsh)\"   "
                  "(or bash) in your rc file\n"
                  "details:       gfl fmt --full df (or git log), or export GFL_FULL=1\n"
                  "live:          gfl fmt --watch --every 2 --full df")
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
            recipes.set_pet(not a.no_pet)
            return r["fn"](argv, _emit_osc(a))
        full = a.full or os.environ.get("GFL_FULL", "") not in ("", "0")

        def draw():
            return r["fn"](argv, full=True) if full and r["full"] else r["fn"](argv)
        if a.watch and sys.stdout.isatty():
            from .recipes import watch
            return watch(draw, a.every, emit=_emit_osc(a))
        chart = draw()
        if not chart:
            return 0
        from .recipes import frame
        out = frame(chart)
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
