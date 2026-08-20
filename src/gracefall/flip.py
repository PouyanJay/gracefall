"""gracefall.flip: a sequence of frames, baked once and played back.

The technique the Ghostty site uses for its hero animation: render every
frame ahead of time, ship the frames, and swap them at a fixed rate. There
is no renderer at playback and nothing to compute per frame, so the player
is a repaint loop and a clock.

What is different here is what a frame is made of. Theirs are baked
characters, so the animation is a picture of a terminal. A gracefall frame
is spans, so the same flipbook is block art in a plain terminal and vector
graphics in one that implements OSC 4700, and it stays selectable,
greppable text either way. The pitch demonstrates itself.

The file is deliberately dull: a header of `key=value` lines, then frames
separated by a line holding one form feed. Rows are written exactly as
they will be printed, envelopes and all, so a flipbook can be inspected
with `less -r`, diffed, and truncated with `head` without a parser.

    gfl bake -o cat.flip          # render the frames
    gfl play cat.flip             # play them until you press a key
    head -c 400 cat.flip          # it is a text file

Playback keeps the same discipline as every other live view here: one
write per frame inside synchronized output so a terminal never presents a
half-erased screen, a rewind that is exactly as tall as what it drew, and
a deadline rather than a sleep so the rate is the rate that was asked for.
"""

import os
import shutil
import sys
import time

from . import strip_spans

__all__ = ["Flipbook", "bake", "dumps", "loads", "play", "fits", "MAGIC"]

#: First line of a flipbook file, and the version of the format.
MAGIC = "#gfl-flip 1"

#: Frames are separated by a line holding this and nothing else. A form
#: feed cannot appear inside a row: rows are printable text and escape
#: sequences, and neither contains one.
SEP = "\f"

#: Frames a second when the file does not say.
DEFAULT_FPS = 30.0

#: Synchronized output, and the cursor. Same sequences the pet loop uses,
#: and safe to send blind: a terminal that does not know the private mode
#: ignores it, which is why this needs no capability check.
BSU, ESU = "\x1b[?2026h", "\x1b[?2026l"
HIDE, SHOW = "\x1b[?25l", "\x1b[?25h"


class Flipbook:
    """`frames` is a list of frames; a frame is a list of row strings."""

    def __init__(self, frames, fps=DEFAULT_FPS, label=""):
        self.frames = list(frames)
        self.fps = float(fps)
        self.label = label

    def __len__(self):
        return len(self.frames)

    @property
    def rows(self):
        return max((len(f) for f in self.frames), default=0)

    @property
    def cols(self):
        """Cell width of the widest row, envelopes and colour not counted."""
        import re
        sgr = re.compile(r"\x1b\[[0-9;]*m")
        return max((len(sgr.sub("", strip_spans(r)))
                    for f in self.frames for r in f), default=0)

    def frame(self, i):
        return self.frames[i % len(self.frames)] if self.frames else []


def dumps(book):
    """The flipbook as text."""
    head = [MAGIC, f"fps={book.fps:g}", f"frames={len(book.frames)}",
            f"rows={book.rows}", f"cols={book.cols}"]
    if book.label:
        head.append(f"label={book.label}")
    out = ["\n".join(head)]
    for f in book.frames:
        out.append(SEP + "\n" + "\n".join(f))
    return "\n".join(out) + "\n"


def loads(text):
    """Parse what `dumps` wrote. Raises ValueError on anything else."""
    if not text.startswith(MAGIC):
        raise ValueError("not a gracefall flipbook (bad first line)")
    blocks = text.split("\n" + SEP + "\n")
    meta = {}
    for line in blocks[0].split("\n")[1:]:
        if "=" in line:
            k, _, v = line.partition("=")
            meta[k.strip()] = v.strip()
    frames = []
    for b in blocks[1:]:
        rows = b.split("\n")
        # dumps() ends the file with a newline, so the last frame carries a
        # trailing empty row that was never part of it.
        if rows and rows[-1] == "" and b is blocks[-1]:
            rows.pop()
        frames.append(rows)
    try:
        fps = float(meta.get("fps", DEFAULT_FPS))
    except ValueError:
        fps = DEFAULT_FPS
    return Flipbook(frames, fps, meta.get("label", ""))


def bake(draw, frames, fps=DEFAULT_FPS, label="", beats=None):
    """Render `frames` frames by calling `draw(tick)` for each.

    `beats` is how many animation beats the whole book covers; the default
    makes one loop of the creature's twelve beat blink cycle. The tick
    handed to `draw` is fractional, because everything that draws here is
    continuous in it, and it is what makes the loop seamless: the last
    frame lands one step before the first repeats.
    """
    n = max(1, int(frames))
    span = float(beats if beats is not None else 12.0)
    return Flipbook([draw(i * span / n) for i in range(n)], fps, label)


def _too_big(book, size, out, err):
    """Say so and draw one frame, rather than animate into a mess.

    The same choice `gfl view` makes when it cannot paint: refusing and
    explaining beats doing it badly, and a still frame is at least the
    picture. Returning 1 so a script can tell.
    """
    err = sys.stderr if err is None else err
    out.write("\n".join(book.frame(0)) + "\n")
    print(f"gfl play: this flipbook needs {book.cols} columns and "
          f"{book.rows + 1} rows; the terminal is {size[0]}x{size[1]}, so "
          f"the frame above is a still. Widen the window, or bake a "
          f"smaller one with --cols/--rows.", file=err)
    return 1


def play(book, out=None, loop=True, wait=None, clock=time.monotonic,
         limit=None, size=None, err=None):
    """Repaint `book`'s frames in place until a key, ctrl-c or `limit`.

    `wait(seconds)` replaces the sleep and returning true from it stops,
    which is how a keypress leaves. `limit` bounds the frame count, for
    tests. The last frame stays on screen, the way every live view here
    leaves its last frame.
    """
    out = sys.stdout if out is None else out
    if not book.frames:
        return 0
    size = size or terminal_size(out)
    if not fits(book, *size):
        return _too_big(book, size, out, err)
    period = 1.0 / book.fps if book.fps > 0 else 0.0
    wait = wait or time.sleep
    prev = 0
    n = 0
    out.write(HIDE)
    try:
        deadline = clock()
        while True:
            if limit is not None and n >= limit:
                return 0
            if not loop and n >= len(book.frames):
                return 0
            # Re-checked every frame: a window resized mid-play is the
            # ordinary case, and carrying on would start the scroll.
            if terminal_size(out) != size:
                size = terminal_size(out)
                if not fits(book, *size):
                    return _too_big(book, size, out, err)
                prev = 0
            body = "\n".join(book.frame(n)) + "\n"
            rewind = f"\x1b[{prev}A\x1b[0J" if prev else ""
            out.write(BSU + rewind + body + ESU)
            out.flush()
            prev = body.count("\n")
            n += 1
            deadline += period
            if wait(max(0.0, deadline - clock())):
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        out.write(SHOW + "\x1b[0m")
        out.flush()


def terminal_size(out=None):
    """(cols, rows) of the terminal, or a sane default."""
    for stream in (out, sys.stdout, sys.stderr):
        try:
            size = os.get_terminal_size(stream.fileno())
            if size.columns > 0:
                return size.columns, size.lines
        except (AttributeError, OSError, ValueError):
            continue
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


def fits(book, cols, rows):
    """Whether a frame can be repainted in place at this terminal size.

    Both axes matter and neither is cosmetic. A row wider than the
    terminal wraps, so it costs two terminal rows while the rewind above
    it counts one, and the difference scrolls every frame: at sixty
    columns a seventy eight column frame walks the screen thirty rows a
    frame, which is the whole screen twice a second. A frame taller than
    the terminal cannot be rewound over at all, because the top of it has
    already scrolled off.

    One row of slack on the height, for the line the frame ends on.
    """
    return book.cols <= cols and book.rows + 1 <= rows


def read_file(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return loads(fh.read())


def write_file(path, book):
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(dumps(book))
    os.replace(tmp, path)
