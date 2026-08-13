"""gracefall.view: paint spans as real graphics inside today's terminals.

No terminal implements OSC 4700 yet. This shim stands in for one: it reads a
stream, works out where each span's cells landed, rasterizes the span's
geometry, and places the image over those exact cells using the kitty
graphics protocol, which Ghostty, kitty, and WezTerm already speak.

    gfl demo --force-osc | gfl view

The geometry comes from shapes.py, the same source the SVG renderer uses, so
this cannot drift from the reference rendering. Nothing here changes the wire
format, and a terminal that cannot do graphics still gets the fallback text
untouched, which is the whole point of the protocol.

Everything that decides *what bytes to write* is a pure function so it can be
tested without a terminal. Only the probe and the metric queries touch a tty.

Coordinate spaces
-----------------
shapes.py geometry is authored in abstract pixels where one cell is 12 x 24
(the constants in render.py). Real cells are not that size, so the raster
backend scales abstract units to device pixels rather than asking shapes.py
for a differently sized box: the insets and bar heights in the geometry are
absolute, so re-boxing would distort them.
"""

import base64
import os
import re
import sys

from .raster import build_palette, parse_color, span_png
from .render import attrs_dict, parse
from .shapes import cell_bbox

#: kitty transmits at most this much base64 per escape sequence.
CHUNK = 4096

#: Fallback when the terminal will not say how big a cell is.
DEFAULT_CELL = (10, 20)


# ------------------------------------------------------- pure byte layout


def place_moves(row0, nrows, col0):
    """Cursor moves to reach a span's top-left cell and to come back.

    Called with the cursor at column 0 of the line *after* the block, which
    is where printing the text leaves it.
    """
    up = nrows - row0
    before = (f"\x1b[{up}A" if up > 0 else "") + (f"\x1b[{col0}C"
                                                  if col0 > 0 else "")
    after = "\r" + (f"\x1b[{up}B" if up > 0 else "")
    return before, after


def apc_chunks(payload_b64, control, chunk_size=CHUNK):
    """Split a base64 payload into kitty graphics escape sequences.

    The first sequence carries the control keys; `m=1` means more follows and
    `m=0` ends the transmission. A payload that fits in one sequence carries
    no `m` at all, which is what kitty expects for the unchunked case.
    """
    if not payload_b64:
        return []
    parts = [payload_b64[i:i + chunk_size]
             for i in range(0, len(payload_b64), chunk_size)]
    out = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if i == 0:
            keys = control if last else control + ",m=1"
        else:
            keys = "m=0" if last else "m=1"
        out.append(f"\x1b_G{keys};{part}\x1b\\")
    return out


def image_sequence(png, cols, rows, placement="over"):
    """The full escape sequence run that places one PNG over `cols` x `rows`
    cells. C=1 leaves the cursor alone, q=2 suppresses the terminal's reply
    so it never leaks into the next command's stdin."""
    control = f"a=T,f=100,c={cols},r={rows},C=1,q=2"
    if placement == "under":
        control += ",z=-1"
    return "".join(apc_chunks(base64.b64encode(png).decode("ascii"), control))


def compose_text(grid, nrows, hide=frozenset()):
    """Rebuild the stream's visible text, blanking the cells `hide` covers.

    Cells under a span become plain spaces so nothing shows through the
    image, while every other cell keeps its colors: the surrounding labels
    and numbers are ordinary text and must stay ordinary text.
    """
    rows = {}
    for (r, c), cell in grid.items():
        rows.setdefault(r, {})[c] = cell
    out = []
    for r in range(nrows):
        cells = rows.get(r, {})
        line = []
        fg = bg = None
        for c in range(max(cells) + 1 if cells else 0):
            ch, cfg, cbg = cells.get(c, (" ", None, None))
            if (r, c) in hide:
                ch, cfg, cbg = " ", None, None
            if (cfg, cbg) != (fg, bg):
                line.append("\x1b[0m")
                if cfg:
                    line.append(_truecolor(cfg, 38))
                if cbg:
                    line.append(_truecolor(cbg, 48))
                fg, bg = cfg, cbg
            line.append(ch)
        if fg or bg:
            line.append("\x1b[0m")
        out.append("".join(line).rstrip())
    return "\n".join(out)


def _truecolor(hexs, base):
    r, g, b = parse_color(hexs) or (0, 0, 0)
    return f"\x1b[{base};2;{r};{g};{b}m"


#: Synchronized output. A repaint between these two is presented as one
#: frame, which is what keeps the watch loop from tearing.
BSU, ESU = "\x1b[?2026h", "\x1b[?2026l"
#: Delete every image this terminal is holding for us.
DELETE_IMAGES = "\x1b_Ga=d,d=A\x1b\\"
HIDE_CURSOR, SHOW_CURSOR = "\x1b[?25l", "\x1b[?25h"


def repaint_sequence(body, prev_rows, graphics=True):
    """One watch frame: rewind over the last one, drop its images, draw.

    Wrapped in synchronized output so the terminal shows the old frame or
    the new one and never a half-drawn mix. The images have to be deleted
    explicitly: overwriting the cells underneath does not remove them, and
    without this they accumulate until the terminal is drowning in them.

    `graphics=False` is the text-only loop for terminals without image
    support, and it must not emit the delete: that is an APC sequence, and a
    terminal that does not understand APC may print it rather than swallow
    it.
    """
    rewind = f"\x1b[{prev_rows}A\x1b[0J" if prev_rows else ""
    delete = DELETE_IMAGES if graphics else ""
    return BSU + rewind + delete + body + ESU


def cleanup_sequence(graphics=True):
    """Leave the terminal exactly as it was found."""
    return (DELETE_IMAGES if graphics else "") + SHOW_CURSOR + "\x1b[0m"


def parse_cell_size_reply(s):
    """CSI 16 t answers with CSI 6 ; height ; width t."""
    m = re.search(r"\x1b\[6;(\d+);(\d+)t", s)
    return (int(m.group(2)), int(m.group(1))) if m else None


# -------------------------------------------------------- capability probe


def backend_from_env(env):
    """Recognize a graphics-capable terminal without talking to it.

    Cheap, and it is the only detection available when stdin is not a tty,
    which is the normal case for `gfl demo | gfl view`.
    """
    if env.get("KITTY_WINDOW_ID") or env.get("GHOSTTY_RESOURCES_DIR"):
        return "env"
    if (env.get("TERM_PROGRAM") or "").lower() in ("ghostty", "wezterm"):
        return "env"
    # TERM is the last resort: Ghostty and kitty both ship their own
    # terminfo, so this still fires when shell integration is disabled.
    if (env.get("TERM") or "") in ("xterm-kitty", "xterm-ghostty"):
        return "env"
    return None


def tmux_passthrough_warning(env):
    """tmux swallows APC sequences unless passthrough is on.

    The shim cannot detect that its images were eaten: it writes them and
    tmux silently drops them, so the cells sit blank with nothing over them.
    That is the one case where the fallback does not save us, because the
    cells were already blanked to make room. Say so instead of painting
    nothing and leaving the user to guess.
    """
    if not env.get("TMUX"):
        return None
    return ("running inside tmux, which drops the graphics sequences unless "
            "passthrough is enabled. Run: tmux set -g allow-passthrough on")


def describe_terminal(env):
    """What we can say about the terminal, for the failure message. Being
    told the name of the terminal that was rejected beats being told that
    some terminal, somewhere, was rejected."""
    name = (env.get("TERM_PROGRAM") or env.get("TERM") or "unknown")
    return name


def probe_kitty(timeout=0.3):
    """Ask the terminal whether it speaks the kitty graphics protocol.

    Sends a 1x1 query plus a device attributes request. Every terminal
    answers DA, so a DA reply with no graphics reply is a definite no rather
    than a timeout. Returns None when there is no tty to ask.
    """
    try:
        import termios
        import tty
    except ImportError:
        return None
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        os.close(fd)
        return None
    try:
        tty.setraw(fd)
        # Not every terminal silently consumes an APC sequence. Terminal.app
        # prints its contents, so an unguarded probe spits
        # "Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA" onto the user's screen. Save the
        # cursor, probe, then restore it and erase forward, which cleans up
        # whatever leaked and is a no-op on a terminal that behaved.
        os.write(fd, b"\x1b7"
                     b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\"
                     b"\x1b[c")
        reply = _read_until(fd, timeout, lambda s: s.endswith("c"))
        os.write(fd, b"\x1b8\x1b[0J")
        return "_Gi=31" in reply
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.close(fd)


def query_terminal(request, terminator, timeout=0.2):
    """Write a query to the tty and read its reply. Returns "" if there is no
    tty or the terminal stays quiet."""
    try:
        import termios
        import tty
    except ImportError:
        return ""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return ""
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        os.close(fd)
        return ""
    try:
        tty.setraw(fd)
        os.write(fd, request)
        return _read_until(fd, timeout, terminator)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.close(fd)


def _read_until(fd, timeout, done):
    import select
    import time
    buf = ""
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return buf
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            return buf
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            return buf
        if not chunk:
            return buf
        buf += chunk.decode("utf-8", "replace")
        if callable(done) and done(buf):
            return buf


def cell_metrics():
    """Pixel size of one cell, and where the number came from.

    A wrong guess makes the output blurry, never broken, so the fallback is
    a plausible default rather than an error.
    """
    try:
        import fcntl
        import struct
        import termios
        for stream in (sys.stdout, sys.stderr, sys.stdin):
            try:
                packed = fcntl.ioctl(stream.fileno(), termios.TIOCGWINSZ,
                                     b"\0" * 8)
            except (OSError, ValueError, AttributeError):
                continue
            rows, cols, xpix, ypix = struct.unpack("HHHH", packed)
            if xpix and ypix and rows and cols:
                return xpix // cols, ypix // rows, "ioctl"
    except ImportError:
        pass
    got = parse_cell_size_reply(query_terminal(b"\x1b[16t",
                                               lambda s: s.endswith("t")))
    if got:
        return got[0], got[1], "CSI 16 t"
    return DEFAULT_CELL[0], DEFAULT_CELL[1], "default"


def background_color():
    reply = query_terminal(b"\x1b]11;?\x1b\\",
                           lambda s: s.endswith("\x1b\\") or s.endswith("\a"))
    m = re.search(r"rgb:[0-9a-fA-F/]+", reply)
    return parse_color(m.group(0)) if m else None


# ------------------------------------------------------------------ paint


def build_output(stream, cellw, cellh, palette, placement="over"):
    """Return (text_to_write, span_report). Pure: no tty, no Pillow calls
    beyond rasterizing, so it can be exercised in tests."""
    grid, spans, nrows = parse(stream)
    hide = set()
    if placement == "over":
        for sp in spans:
            hide |= {(r, c) for r, c, _ in sp["cells"]}
    out = [compose_text(grid, nrows, hide), "\n"]
    report, warning = [], None
    for sp in spans:
        bb = cell_bbox(sp["cells"])
        a = attrs_dict(sp["attrs"])
        if bb is None:
            report.append((a.get("t"), None, "empty span"))
            continue
        r0, c0, nr, nc = bb
        png, warn = span_png(a, nc, nr, cellw, cellh, palette)
        warning = warning or warn
        if png is None:
            report.append((a.get("t"), bb, "no shapes, fallback kept"))
            continue
        before, after = place_moves(r0, nrows, c0)
        out.append(before)
        out.append(image_sequence(png, nc, nr, placement))
        out.append(after)
        report.append((a.get("t"), bb, f"{len(png)} B"))
    out.append("\x1b[0m")
    return "".join(out), report, warning


def _watch(args, cellw, cellh, palette, out, err, source, backend,
           graphics=True):
    """Re-run a command on an interval and repaint in place.

    Snapshot mode per cycle: the command is run to completion, then the whole
    block is redrawn. Incremental streaming is deliberately out of scope.

    Runs in every terminal. Without graphics support it repaints the
    fallback text, which is still a live dashboard and is the whole promise
    of the protocol: the degraded view is a first-class view.
    """
    import subprocess
    import time

    from . import strip_spans

    if args.stats:
        print(f"backend:  {backend or 'text only'}", file=err)
        if graphics:
            print(f"cell:     {cellw}x{cellh} px (from {source})", file=err)
        print(f"watching: {args.watch} every {args.interval}s", file=err)
    out.write(HIDE_CURSOR)
    prev_rows = 0
    try:
        while True:
            # The watched command's stdout is a pipe, so its own isatty
            # check would strip the envelopes it is being asked to produce.
            proc = subprocess.run(
                args.watch, shell=True, capture_output=True,
                env=dict(os.environ, GRACEFALL_FORCE_OSC="1"))
            if proc.returncode:
                out.write(cleanup_sequence(graphics))
                out.flush()
                msg = proc.stderr.decode("utf-8", "replace").strip()
                raise SystemExit(f"gfl view --watch: command failed: {msg}")
            stream = proc.stdout.decode("utf-8", "replace")
            if graphics:
                body, _, _ = build_output(stream, cellw, cellh, palette,
                                          args.placement)
            else:
                # Strip rather than trusting the terminal to swallow them:
                # the fallback is what we want on screen either way.
                body = strip_spans(stream).rstrip("\n")
            out.write(repaint_sequence(body + "\n", prev_rows, graphics))
            out.flush()
            prev_rows = body.count("\n") + 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        # Ctrl-C must not leave a hidden cursor or a screenful of images.
        out.write(cleanup_sequence(graphics))
        out.flush()


def run(stream, args, out=None, env=None, stderr=None):
    """The `gfl view` entry point."""
    out = out if out is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    env = env if env is not None else os.environ

    backend = backend_from_env(env)
    if backend is None and not args.no_probe:
        backend = "probe" if probe_kitty() else None

    if backend is None and getattr(args, "watch", None):
        # A live text dashboard is still worth having, and refusing to run
        # would make --watch the one feature that needs a special terminal.
        print(f"gfl view: {describe_terminal(env)} has no graphics support, "
              f"watching in text mode. Ctrl-C to stop.", file=err)
        return _watch(args, 0, 0, None, out, err, "n/a", None, graphics=False)

    if backend is None:
        out.write(stream if stream.endswith("\n") else stream + "\n")
        who = describe_terminal(env)
        probed = "" if args.no_probe else ", and it did not answer the probe"
        print(f"gfl view: {who} does not speak the kitty graphics protocol"
              f"{probed}, so the fallback text above is what you get. That "
              f"fallback is the point of the protocol, but for the smooth "
              f"rendering run this inside Ghostty, kitty, or WezTerm.",
              file=err)
        return 0

    tmux_warning = tmux_passthrough_warning(env)
    if tmux_warning and not env.get("GRACEFALL_TMUX_OK"):
        # Painting here blanks the cells and then loses the images, which is
        # strictly worse than the fallback. Print the fallback instead.
        out.write(stream if stream.endswith("\n") else stream + "\n")
        print(f"gfl view: {tmux_warning}", file=err)
        print("gfl view: showing the fallback instead. Set "
              "GRACEFALL_TMUX_OK=1 to paint anyway.", file=err)
        return 0

    # Fail before drawing anything, not halfway through a repaint.
    from .raster import require_pillow
    require_pillow()

    cellw, cellh, source = cell_metrics()
    if args.cell:
        try:
            cw, chh = (int(v) for v in args.cell.lower().split("x"))
            cellw, cellh, source = cw, chh, "--cell"
        except ValueError:
            raise SystemExit("--cell wants WIDTHxHEIGHT, for example 10x20")
    palette = build_palette(background_color())

    if getattr(args, "watch", None):
        return _watch(args, cellw, cellh, palette, out, err, source, backend)

    text, report, warning = build_output(stream, cellw, cellh, palette,
                                         args.placement)
    if args.stats:
        print(f"backend:  {backend}", file=err)
        print(f"cell:     {cellw}x{cellh} px (from {source})", file=err)
        print(f"palette:  bg {palette['bg']}", file=err)
        print(f"spans:    {len(report)}", file=err)
        for t, bb, note in report:
            print(f"  {t:8s} {str(bb):22s} {note}", file=err)
    if warning:
        print(f"gfl view: {warning}", file=err)
    out.write(text + "\n")
    return 0
