"""gracefall.shell: run a shell inside gracefall.

    gfl shell

Starts your normal shell on a pseudo-terminal and relays it, watching the
byte stream go past. Whenever a complete OSC 4700 span passes through, its
graphics are painted over the cells the fallback just landed on. Every
program you run is unmodified and unaware; anything that emits gracefall
envelopes is simply rendered.

This is a stand-in for a terminal that implements OSC 4700, in the same
spirit as `gfl view`, except it is always on rather than something you pipe
into. Inside it, `isatty` is true, so emitters produce their envelopes
naturally with no `--force-osc`.

How the position is worked out
------------------------------
The hard part of rendering a span mid-stream is knowing where on screen it
landed. Full terminal emulation is not needed for that, because the answer
is always *relative*: when a span closes, the cursor is at the end of its
own fallback text, and we counted every cell that fallback wrote. So the
span's top-left is a known number of rows up and a known column across.

The cursor is then saved, moved there, the image is placed, and the cursor
restored. If anything happens mid-span that the tracker does not model, the
span is dropped rather than painted in the wrong place: a missing chart is
a fallback, which is fine, and a misplaced one is a corrupted screen.
"""

import os
import re
import select
import signal
import sys

from .render import attrs_dict

#: Sequences that move the cursor in ways worth tracking.
_CSI_MOVE = re.compile(rb"\x1b\[([0-9;]*)([A-HJKSTfgm])")
_OSC = re.compile(rb"\x1b\](\d+);?([^\x07\x1b]*)(\x07|\x1b\\)")
_APC = re.compile(rb"\x1b_[^\x1b]*\x1b\\")
_ESC_OTHER = re.compile(rb"\x1b[()#][0-9A-Za-z]|\x1b[78=>DEHMcZ]")

#: The longest incomplete escape we are willing to hold back waiting for
#: the rest of it. Beyond this we give up and pass the bytes through, so a
#: stray ESC can never wedge the relay.
MAX_PENDING = 8192

#: Terminals that speak the kitty graphics protocol, and how to start one
#: running a command. They disagree about this: Ghostty follows the xterm
#: `-e` convention, kitty takes the program as plain positional arguments,
#: and WezTerm wants a subcommand.
TERMINALS = [
    ("Ghostty",
     ["/Applications/Ghostty.app/Contents/MacOS/ghostty", "ghostty"],
     lambda exe, cmd, cwd: [exe, f"--working-directory={cwd}", "-e"] + cmd),
    ("kitty",
     ["/Applications/kitty.app/Contents/MacOS/kitty", "kitty"],
     lambda exe, cmd, cwd: [exe, "--directory", cwd] + cmd),
    ("WezTerm",
     ["/Applications/WezTerm.app/Contents/MacOS/wezterm", "wezterm"],
     lambda exe, cmd, cwd: [exe, "start", "--cwd", cwd, "--"] + cmd),
]


def available_terminals():
    """Installed terminals that could actually render the graphics."""
    import shutil
    found = []
    for name, candidates, build in TERMINALS:
        for cand in candidates:
            exe = cand if os.path.exists(cand) else shutil.which(cand)
            if exe:
                found.append((name, exe, build))
                break
    return found


def _menu(who, found, color=True):
    """The relaunch menu, drawn with gracefall's own output.

    Showing the fallback here is not decoration: it is the argument. The
    charts in this menu are real gracefall spans rendered as text, which is
    what the user already has, next to an offer of the smooth version.
    """
    from . import R, SGR, meter, spark, strip_spans

    def paint(code, text):
        return f"{code}{text}{R}" if color else text

    def chart(s):
        return strip_spans(s) if color else _plain(strip_spans(s))

    D, F, T = SGR["dim"], SGR["fg"], SGR["teal"]
    logo = chart(spark([1, 2, 4, 3, 6, 8], color="teal"))
    sample = chart(spark([3, 5, 4, 7, 6, 9, 8, 9], color="blue"))
    bar = chart(meter(0.62, 12, "amber"))
    lines = [
        "",
        f"  {logo}  {paint(F, 'gracefall')}",
        "",
        f"  {paint(D, who + ' draws charts as text, which already works:')}",
        f"    {sample}   {bar}",
        "",
        f"  {paint(F, 'Open a terminal that draws them smoothly?')}",
        "",
    ]
    for i, (name, _, _) in enumerate(found, 1):
        lines.append(f"    {paint(T, str(i))}  {paint(F, name)}")
    lines.append(f"    {paint(D, 'q')}  {paint(D, 'stay here')}")
    lines.append("")
    return "\n".join(lines)


def _plain(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def offer_relaunch(command, out=sys.stderr, ask=input, who="this terminal",
                   color=None):
    """Offer to reopen `command` in a terminal that can draw it.

    Returns True if one was launched. Being told "your terminal cannot do
    this" is only useful if the next step is obvious, and here the next step
    is a different window, which we can just open.
    """
    found = available_terminals()
    if not found:
        print("gfl shell: no graphics-capable terminal is installed.\n"
              "  Install one:  brew install --cask ghostty", file=out)
        return False
    if not sys.stdin.isatty():
        print(f"gfl shell: run this inside "
              f"{' or '.join(n for n, _, _ in found)}.", file=out)
        return False

    if color is None:
        color = not os.environ.get("NO_COLOR")
    print(_menu(who, found, color), file=out)
    from . import R, SGR
    arrow = f"  {SGR['teal']}\u25b8{R} " if color else "  > "
    try:
        choice = (ask(arrow) or "1").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=out)
        return False
    if choice.startswith("q"):
        return False
    try:
        name, exe, build = found[int(choice) - 1]
    except (ValueError, IndexError):
        print(f"  not a choice: {choice!r}", file=out)
        return False

    import subprocess
    argv = build(exe, command, os.getcwd())
    try:
        subprocess.Popen(argv, start_new_session=True,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"  could not start {name}: {e}", file=out)
        return False
    print(f"  opened {name}, this window is unchanged\n", file=out)
    return True


class SpanTracker:
    """Follows the cursor through a byte stream and reports finished spans.

    Pure: feed it bytes, get back the bytes to write plus any spans that
    completed. No terminal, no I/O, so the placement maths is testable.
    """

    def __init__(self, width=80):
        self.width = max(1, width)
        self.col = 0
        self.row = 0            # relative, only meaningful within a span
        self.open = None        # {"attrs": str, "cells": [(row, col)]}
        self.aborted = False    # open span whose position we lost
        self._buf = b""

    # -- cursor -------------------------------------------------------
    def _putc(self, ch):
        if self.open is not None and not self.aborted and ch != " ":
            self.open["cells"].append((self.row, self.col))
        self.col += 1
        if self.col >= self.width:      # autowrap
            self.col = 0
            self.row += 1

    def _move(self, params, final):
        n = 0
        try:
            n = int(params.split(b";")[0] or b"1")
        except ValueError:
            n = 1
        if final == b"A":
            self.row -= n
        elif final == b"B":
            self.row += n
        elif final == b"C":
            self.col += n
        elif final == b"D":
            self.col = max(0, self.col - n)
        elif final == b"G":
            self.col = max(0, n - 1)
        elif final in (b"H", b"f"):
            # Absolute positioning: our row is relative, so we can no
            # longer say where an open span started.
            self.col = 0
            if self.open is not None:
                self.aborted = True
        # J, K, S, T, m do not move the cursor.

    # -- feeding ------------------------------------------------------
    def feed(self, data):
        """Return a list of segments, in stream order.

        Each segment is either raw bytes to pass through, or a span dict
        {"attrs", "up", "col", "rows", "cols"} saying how far up and across
        its top-left cell is *from the cursor at that point in the stream*.

        Order matters and the split is the whole point: a span must be
        painted where it closed, not at the end of the read. One read
        usually carries the trailing newline after a chart, and emitting the
        image after that newline puts it a row too low.
        """
        self._buf += data
        segments = []
        out = bytearray()
        spans = []

        def flush():
            if out:
                segments.append(bytes(out))
                del out[:]

        i = 0
        buf = self._buf
        n = len(buf)
        while i < n:
            b = buf[i:i + 1]
            if b == b"\x1b":
                m = _OSC.match(buf, i)
                if m:
                    if m.group(1) == b"4700":
                        out += buf[i:m.end()]
                        self._envelope(m.group(2), spans)
                        if spans:
                            flush()
                            segments.extend(spans)
                            del spans[:]
                        i = m.end()
                        continue
                    out += buf[i:m.end()]
                    i = m.end()
                    continue
                m = _CSI_MOVE.match(buf, i)
                if m:
                    self._move(m.group(1), m.group(2))
                    out += buf[i:m.end()]
                    i = m.end()
                    continue
                m = _APC.match(buf, i) or _ESC_OTHER.match(buf, i)
                if m:
                    out += buf[i:m.end()]
                    i = m.end()
                    continue
                # Possibly the start of a sequence split across reads.
                if n - i < MAX_PENDING and self._could_complete(buf[i:]):
                    break
                out += b
                i += 1
                continue
            if b == b"\n":
                self.row += 1
            elif b == b"\r":
                self.col = 0
            elif b == b"\b":
                self.col = max(0, self.col - 1)
            elif b == b"\t":
                self.col = min(self.width - 1, (self.col // 8 + 1) * 8)
            elif b >= b" ":
                # Only count the first byte of a UTF-8 sequence as a cell.
                if buf[i] < 0x80 or buf[i] >= 0xC0:
                    self._putc(chr(buf[i]) if buf[i] < 0x80 else "x")
            out += b
            i += 1
        self._buf = buf[i:]
        flush()
        return segments

    @staticmethod
    def _could_complete(tail):
        """True if `tail` looks like the beginning of an escape sequence
        whose remainder has not arrived yet."""
        if len(tail) == 1:
            return True
        second = tail[1:2]
        if second == b"[":
            return not re.search(rb"[@-~]", tail[2:])
        if second == b"]":
            return not re.search(rb"\x07|\x1b\\", tail[2:])
        if second == b"_":
            return not re.search(rb"\x1b\\", tail[2:])
        return False

    def _envelope(self, attrs, spans):
        if not attrs:
            self._close(spans)
            return
        self.open = {"attrs": attrs.decode("utf-8", "replace"),
                     "cells": []}
        self.aborted = False
        self.row = 0

    def _close(self, spans):
        span, self.open = self.open, None
        aborted, self.aborted = self.aborted, False
        if span is None or aborted or not span["cells"]:
            return
        rows = [r for r, _ in span["cells"]]
        cols = [c for _, c in span["cells"]]
        r0, c0 = min(rows), min(cols)
        spans.append({
            "attrs": span["attrs"],
            "up": self.row - r0,
            "col": c0,
            "end_col": self.col,      # where the cursor must end up again
            "rows": max(rows) - r0 + 1,
            "cols": max(cols) - c0 + 1,
        })


def placement_bytes(png, span):
    """Paint the span where its fallback landed, then walk the cursor back.

    Deliberately not DECSC/DECRC: there is exactly one saved-cursor slot,
    and a program inside the shell may be using it. Clobbering vim's saved
    position to draw a chart would be a bad trade. The return path is
    known anyway, since `C=1` means the image never moves the cursor.
    """
    from .view import image_sequence
    up = f"\x1b[{span['up']}A" if span["up"] > 0 else ""
    down = f"\x1b[{span['up']}B" if span["up"] > 0 else ""
    img = image_sequence(png, span["cols"], span["rows"])
    return (f"{up}\x1b[{span['col'] + 1}G{img}"
            f"{down}\x1b[{span['end_col'] + 1}G").encode("utf-8")


def run(args, argv=None):
    """Start the shell and relay it. Returns the shell's exit status."""
    import pty
    import termios
    import tty

    from .raster import build_palette, require_pillow, span_png
    from .view import (backend_from_env, background_color, cell_metrics,
                       describe_terminal, probe_kitty,
                       tmux_passthrough_warning)

    env = os.environ
    if not sys.stdout.isatty():
        raise SystemExit("gfl shell needs a terminal, not a pipe")

    backend = backend_from_env(env)
    if backend is None and not args.no_probe:
        backend = "probe" if probe_kitty() else None
    if backend is None:
        who = describe_terminal(env)
        if args.no_relaunch:
            print(f"gfl shell: {who} cannot draw graphics. Your charts "
                  f"already work here as fallback text.", file=sys.stderr)
            return 1
        return 0 if offer_relaunch(_self_command(args), who=who) else 1
    warn = tmux_passthrough_warning(env)
    if warn:
        raise SystemExit(f"gfl shell: {warn}")
    require_pillow()

    cellw, cellh, _ = cell_metrics()
    if args.cell:
        try:
            cellw, cellh = (int(v) for v in args.cell.lower().split("x"))
        except ValueError:
            raise SystemExit("--cell wants WIDTHxHEIGHT, such as 10x20")
    # Opaque, because the fallback text is already on screen underneath and
    # would otherwise show through the transparent parts of the chart.
    palette = build_palette(background_color())

    shell = args.shell or env.get("SHELL") or "/bin/sh"
    pid, master = pty.fork()
    if pid == 0:                                  # child
        os.environ["GRACEFALL_SHELL"] = "1"
        os.execvp(shell, [shell] + list(argv or []))

    tracker = SpanTracker(_columns())
    _set_winsize(master)

    def on_winch(_sig, _frm):
        _set_winsize(master)
        tracker.width = _columns()

    signal.signal(signal.SIGWINCH, on_winch)
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        _relay(master, tracker, cellw, cellh, palette, span_png)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) if hasattr(
        os, "waitstatus_to_exitcode") else (status >> 8)


def _self_command(args):
    """The command to run in the newly opened terminal: this one again."""
    cmd = [sys.argv[0] if sys.argv[0].endswith(("gfl", "gracefall"))
           else "gfl", "shell"]
    if args.shell:
        cmd += ["--shell", args.shell]
    if args.cell:
        cmd += ["--cell", args.cell]
    return cmd


def _columns():
    from .view import terminal_size
    return terminal_size()[0]


def _set_winsize(fd):
    import fcntl
    import struct
    import termios
    try:
        packed = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ,
                             b"\0" * 8)
    except OSError:
        return
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def _relay(master, tracker, cellw, cellh, palette, span_png):
    """Pump bytes both ways, painting spans as they complete."""
    out = sys.stdout.buffer
    stdin_fd = sys.stdin.fileno()
    while True:
        try:
            ready, _, _ = select.select([master, stdin_fd], [], [])
        except InterruptedError:          # SIGWINCH
            continue
        if stdin_fd in ready:
            data = os.read(stdin_fd, 4096)
            if not data:
                break
            os.write(master, data)
        if master in ready:
            try:
                data = os.read(master, 8192)
            except OSError:
                break
            if not data:
                break
            for segment in tracker.feed(data):
                if isinstance(segment, bytes):
                    out.write(segment)
                else:
                    out.write(_paint(segment, cellw, cellh, palette,
                                     span_png))
            out.flush()


def _paint(span, cellw, cellh, palette, span_png):
    try:
        png, _ = span_png(attrs_dict(span["attrs"]), span["cols"],
                          span["rows"], cellw, cellh, palette,
                          background=palette["bg"])
    except Exception:
        # A malformed envelope must never take the shell down. The fallback
        # is already on screen, so the worst case is no enhancement.
        return b""
    if png is None:
        return b""
    return placement_bytes(png, span)
