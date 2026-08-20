"""gracefall.replay: play a recorded stream back, with the creature reading it.

A `.gfall` file is a byte stream that a terminal already knows what to do
with, so replaying one is `cat` with a clock on it. That is the whole of
this module's contract: the bytes that go out are the bytes in the file,
in order, unchanged. Everything else it does happens around them.

Three paths, chosen by the isatty rule and nothing else:

- Not a terminal: one write of the whole file. No creature, no scroll
  region, no pacing. `gfl replay f | cat` is `cat f`.
- A terminal, `--pet` off: through `less -rFX` (or `$GFL_PAGER`, the same
  policy `gfl git log` uses) unless `--no-pager`, because a recording is
  usually longer than a screen and a pager is how a person reads one.
- A terminal, `--pet` on: the bottom line is reserved with a scroll
  region, the stream scrolls above it, and the creature sits on it and
  reads what goes past. A pager owns the screen, so the two never both
  run: `--pet` takes the pager's place.

There is no timing data in a `.gfall` file. Recording the timing is the
other half of this feature and is not here yet, so the default is to
write the file out as it is, and `--speed` paces it evenly instead:
`CELLS_PER_SECOND` visible cells a second at `--speed 1`, in chunks of a
twentieth of a second. Cells rather than bytes, because an envelope is
not cells: a stream that is mostly data would otherwise crawl and one
that is mostly text would race. `--pet` implies `--speed 1` when no speed
was given, since a creature reacting to a stream that went past in one
write has nothing to react to.

The acceptance this module lives by: the replayed bytes are identical
whether the creature is there or not. It is drawn between DECSC and
DECRC, on a row outside the scroll region, so it never enters the stream
and never moves it. `stream_only()` is that invariant made testable.
"""

import re
import shutil
import subprocess
import sys
import time

from . import _SPAN_RE
from .creature import Creature
from .recipes import MARGIN, PET_HZ

__all__ = ["chunks", "moods", "read", "run", "stream_only"]

#: The pace of `--speed 1`, in visible cells a second. About twenty five
#: lines of an eighty column terminal, which is a fast build's output and
#: is readable with the creature beside it. `--speed` scales it.
CELLS_PER_SECOND = 2000

#: How often a paced replay writes. Twenty times a second is smooth
#: enough to read as a stream rather than as pages, and it is also the
#: creature's repaint clock, which at PET_HZ moves every tenth chunk.
CHUNK = 0.05

#: `--pet` with no `--speed`: a creature needs something going past.
PET_SPEED = 1.0

#: The rows a pet replay needs: one for the creature and some for the
#: stream. Under this the bottom line is not worth reserving.
MIN_ROWS = 3

#: How long a mood stays up unless a stronger reading arrives, in
#: seconds. Without it a chart that goes past in one chunk would change
#: the face for a twentieth of a second, which reads as a flicker rather
#: than as a reaction.
HOLD = 0.6

#: What the creature is willing to be talked out of. A wince outranks a
#: cheer, a cheer outranks looking up, and reading along is the pose
#: anything else replaces.
RANK = {"working": 0, "idle": 1, "happy": 2, "sad": 3}

#: The opening envelope of a span, and its attributes. Deliberately not
#: `render.parse`: a replay scans every chunk it writes, and the parser
#: builds a character grid, which is the wrong price to pay twenty times
#: a second for two attribute values.
_OPEN = re.compile(r"\x1b\]4700;([^\x07\x1b]+)(?:\x07|\x1b\\)")
_ATTR = re.compile(r"(?:^|;)([a-z]+)=([^;]*)")

#: The words a stream says when something went wrong or came out right.
#: Read over the raw chunk, so `flow`'s `s=...,failed` counts as much as
#: a test runner writing FAILED.
_BAD = re.compile(r"\b(fail|failed|failing|failure|error|errors)\b", re.I)
_GOOD = re.compile(r"\b(pass|passed|ok)\b", re.I)

#: What a chunk gets for free: an envelope and a colour change are both
#: bytes with no cell behind them, so neither is counted and neither is
#: ever cut in half.
_FREE = re.compile(_SPAN_RE.pattern + r"|\x1b\[[0-9;]*m")

#: The creature paint, between DECSC and DECRC. It contains no other
#: ESC 8, so the first one ends it.
_PAINT = re.compile(r"\x1b7.*?\x1b8", re.S)


# --------------------------------------------------------------------------
# the stream, cut into chunks


def chunks(text, cells):
    """`text` split into pieces of about `cells` visible cells each.

    An envelope and a colour change cost nothing and are never cut in
    half, so a chunk is a fixed amount of what a person sees rather than
    a fixed number of bytes. `"".join(chunks(text, n)) == text` for every
    n, which is what makes a paced replay a byte copy.
    """
    if cells <= 0:
        return [text] if text else []
    pieces, i = [], 0
    for m in _FREE.finditer(text):
        if m.start() > i:
            pieces.append((text[i:m.start()], m.start() - i))
        pieces.append((m.group(0), 0))
        i = m.end()
    if i < len(text):
        pieces.append((text[i:], len(text) - i))
    out, buf, n = [], [], 0
    for s, cost in pieces:
        if not cost:                      # no cells behind it: never split
            buf.append(s)
            continue
        j = 0
        while j < len(s):
            take = min(len(s) - j, cells - n)
            buf.append(s[j:j + take])
            n += take
            j += take
            if n >= cells:
                out.append("".join(buf))
                buf, n = [], 0
    if buf:
        out.append("".join(buf))
    return out


# --------------------------------------------------------------------------
# what the creature makes of it


def moods(chunk):
    """Every mood a chunk of stream suggests, strongest first.

    The reading is the one the issue asked for and nothing cleverer: a
    coral meter or a word about failure is a wince, a teal meter or a
    word about passing is a cheer, a `lanes` graph is worth looking up
    for, and anything else with cells in it is read along.
    """
    found = set()
    for m in _OPEN.finditer(chunk):
        attrs = dict(_ATTR.findall(m.group(1)))
        t, c = attrs.get("t"), attrs.get("c")
        if t == "meter" and c == "coral":
            found.add("sad")
        elif t == "meter" and c == "teal":
            found.add("happy")
        elif t == "lanes":
            found.add("idle")
    if _BAD.search(chunk):
        found.add("sad")
    elif _GOOD.search(chunk):
        found.add("happy")
    if not found and chunk.strip():
        found.add("working")
    return sorted(found, key=lambda m: -RANK[m])


def read(chunk):
    """The one mood a chunk reads as, or None when it is quiet."""
    m = moods(chunk)
    return m[0] if m else None


class Narrator:
    """The creature, and how the stream moves it.

    A mood holds for `hold` seconds unless a stronger one arrives, so a
    chart that goes past in one chunk is still on the face long enough to
    be seen. A quiet chunk falls back to idle once the hold is up, which
    is what the end of a recording looks like.

    The tick comes from the clock rather than a count of chunks, so the
    creature breathes at one speed whatever `--speed` is set to.
    """

    def __init__(self, creature=None, clock=time.monotonic, hold=HOLD):
        self.creature = creature or Creature("idle", size=1)
        self._clock = clock
        self.hold = hold
        self._t0 = clock()
        self._at = None

    def feed(self, chunk, progress=None):
        """Read `chunk` and return the creature's line for now."""
        mood = read(chunk) or "idle"
        now = self._clock()
        held = self._at is not None and now - self._at < self.hold
        if not held or RANK[mood] > RANK[self.creature.mood]:
            self.creature.mood = mood
            self._at = now
        if progress is not None:
            self.creature.update(cpu=progress)
        return self.frame()

    def frame(self):
        """The creature's one line, at the tick the clock says.

        A fractional beat, not a whole one: the creature is continuous in
        its tick, and rounding it down here meant the reader only ever saw
        two of the frames it draws each second.
        """
        tick = (self._clock() - self._t0) * PET_HZ
        return MARGIN + self.creature.frame(tick)


# --------------------------------------------------------------------------
# the bottom line


def setup(rows):
    """Reserve the bottom row: scroll the screen up by one so the row is
    free, keep the replay inside the rows above it, and put the cursor at
    the bottom of that region so output scrolls the way it always does."""
    return ("\x1b[?25l"                   # a repainted line has no caret
            f"\x1b[{rows};1H\n"           # one row of room, nothing lost
            f"\x1b[1;{rows - 1}r"         # the replay scrolls in here
            f"\x1b[{rows - 1};1H")


def teardown(rows):
    """Give the whole screen back and leave the last frame on it, with
    the cursor on the row under the creature so a prompt lands there."""
    return f"\x1b[r\x1b[{rows};1H\n\x1b[?25h"


def paint(line, rows):
    """The creature on row `rows`, between DECSC and DECRC so the cursor
    and the pen come back exactly as the stream left them."""
    return f"\x1b7\x1b[{rows};1H\x1b[2K{line}\x1b8"


def stream_only(text, rows):
    """`text` with everything the creature added taken back out.

    The acceptance for `--pet` is that this equals a replay without it.
    Every paint is bracketed by DECSC and DECRC and the scroll region is
    set up and given back in one write each, so removing them is exact
    rather than a guess at what the creature might have written.
    """
    out = _PAINT.sub("", text)
    for s in (setup(rows), teardown(rows)):
        out = out.replace(s, "", 1)
    return out


# --------------------------------------------------------------------------
# the command


def _isatty(stream):
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _load(path):
    """The stream as text, losslessly. surrogateescape rather than
    replace: a replay is a byte copy, and a file this cannot decode still
    has to come out the way it went in."""
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "surrogateescape")
    except OSError as e:
        raise SystemExit(f"replay: cannot read {path}: {e.strerror or e}")


def _write(out, text):
    out.write(text.encode("utf-8", "surrogateescape"))


def _page(text):
    """Through the pager, the same one and the same policy `gfl git log`
    uses. False when there is no pager to use."""
    from .gitlog import _pager
    argv = _pager()
    if not argv:
        return False
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE)
    except OSError:
        return False
    try:
        proc.stdin.write(text.encode("utf-8", "surrogateescape"))
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    proc.wait()
    return True


def _rows():
    return shutil.get_terminal_size((80, 24)).lines


def run(a, out=None, sleep=time.sleep, clock=time.monotonic):
    """`gfl replay`: write a recorded stream back out."""
    out = (sys.stdout.buffer if out is None else out)
    text = _load(a.file)
    speed = max(0.0, getattr(a, "speed", 0.0) or 0.0)
    pet = getattr(a, "pet", False) and _isatty(out)
    rows = _rows() if pet else 0
    if pet and rows < MIN_ROWS:
        pet = False
    if pet and not speed:
        speed = PET_SPEED

    if not _isatty(out):
        _write(out, text)                 # the file, and nothing else
        out.flush()
        return 0
    if not pet and not getattr(a, "no_pager", False) and _page(text):
        return 0

    size = int(CELLS_PER_SECOND * speed * CHUNK) if speed else 0
    pieces = chunks(text, size)
    narrator = Narrator(clock=clock) if pet else None
    if pet:
        _write(out, setup(rows))
    try:
        done, total = 0, max(1, len(text))
        last = None
        deadline = clock()
        for piece in pieces:
            _write(out, piece)
            done += len(piece)
            if narrator is not None:
                line = narrator.feed(piece, progress=done / total)
                if line != last:
                    _write(out, paint(line, rows))
                    last = line
            out.flush()
            if size:
                deadline += CHUNK
                sleep(max(0.0, deadline - clock()))
        if narrator is not None:
            line = narrator.feed("", progress=1.0)
            if line != last:
                _write(out, paint(line, rows))
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:               # a pager the reader quit early
        return 0
    finally:
        if pet:
            _write(out, teardown(rows))
        try:
            out.flush()
        except (BrokenPipeError, OSError):
            pass
    return 0
