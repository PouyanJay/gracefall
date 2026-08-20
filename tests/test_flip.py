"""The flipbook: frames baked once, played back in place.

The file format is dull on purpose and the player is the same repaint
discipline as every other live view here. What is tested is that a
flipbook survives a round trip unchanged, that playing one does not walk
down the screen, and that the rate asked for is the rate delivered.
"""

import io
import re

import pytest

from gracefall import flip, shade

COLS, ROWS = 48, 14


def book(frames=6, fps=30.0, mood="idle"):
    return flip.bake(lambda t: shade.rows(COLS, ROWS, t, mood),
                     frames=frames, fps=fps, label="test")


class Out(io.StringIO):
    def __init__(self, tty=True):
        io.StringIO.__init__(self)
        self._tty = tty

    def isatty(self):
        return self._tty


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, s):
        self.now += s


class Screen:
    """Enough terminal to tell whether a repaint stayed where it was put:
    it wraps at the right edge and scrolls at the bottom, which are the two
    ways a frame can cost more rows than it claims."""

    CSI = re.compile(r"\x1b\[([?0-9;]*)([A-Za-z])")

    def __init__(self, rows=40, cols=80):
        self.rows, self.cols = rows, cols
        self.r = self.c = self.scrolled = 0
        self.touched = {}

    def _index(self):
        if self.r >= self.rows - 1:
            self.scrolled += 1
        else:
            self.r += 1

    def feed(self, text):
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                nxt = text[i + 1:i + 2]
                if nxt == "]":                       # an envelope, swallowed
                    j = text.find("\x1b\\", i)
                    i = len(text) if j < 0 else j + 2
                    continue
                if nxt != "[":
                    i += 2
                    continue
                m = self.CSI.match(text, i)
                if not m:
                    i += 1
                    continue
                args, verb = m.group(1), m.group(2)
                if not args.startswith("?"):
                    ps = [int(x) if x else 0
                          for x in args.split(";")] if args else []
                    n = ps[0] if ps else 1
                    if verb == "A":
                        self.r = max(0, self.r - n)
                    elif verb == "B":
                        self.r = min(self.rows - 1, self.r + n)
                    elif verb == "J":
                        pass
                i = m.end()
                continue
            if ch == "\n":
                self._index()
                self.c = 0
            elif ch == "\r":
                self.c = 0
            elif ch >= " ":
                if self.c >= self.cols:
                    self.c = 0
                    self._index()
                self.c += 1
                self.touched[self.r] = max(self.touched.get(self.r, 0), self.c)
            i += 1
        return self


# --------------------------------------------------------------------------
# the file


def test_a_flipbook_survives_a_round_trip():
    b = book()
    got = flip.loads(flip.dumps(b))
    assert got.frames == b.frames
    assert got.fps == b.fps and got.label == b.label


def test_the_header_says_what_is_in_it():
    text = flip.dumps(book(frames=4, fps=24.0))
    head = text.split(flip.SEP)[0]
    assert head.startswith(flip.MAGIC)
    for line in ("fps=24", "frames=4", f"rows={ROWS}", f"cols={COLS}"):
        assert line in head, line


def test_anything_else_is_refused_rather_than_half_parsed():
    for junk in ("", "hello", "#gfl-flop 1\nfps=30\n"):
        with pytest.raises(ValueError):
            flip.loads(junk)


def test_a_frame_keeps_its_rows_exactly():
    """Rows are written as they will be printed, envelopes and all, so a
    flipbook can be read with less and cut with head."""
    b = book(frames=3)
    got = flip.loads(flip.dumps(b))
    for a, c in zip(b.frames, got.frames):
        assert a == c
        assert all("\n" not in r for r in c)


def test_the_file_is_text_a_person_can_look_at():
    text = flip.dumps(book(frames=2))
    assert "\x00" not in text
    assert text.count("\n" + flip.SEP + "\n") == 2


# --------------------------------------------------------------------------
# playing it


def test_playing_does_not_walk_down_the_screen():
    """The bug this shape of loop shipped with once already."""
    b = book(frames=8)
    one, many = Out(), Out()
    flip.play(b, out=one, wait=lambda s: False, limit=1)
    flip.play(b, out=many, wait=lambda s: False, limit=40)
    assert Screen().feed(many.getvalue()).scrolled == 0
    assert Screen().feed(one.getvalue()).r == Screen().feed(
        many.getvalue()).r, "the cursor drifted"


def test_no_row_is_wider_than_the_frame():
    """A row wider than the terminal wraps, and a wrapped row costs more
    rows than the rewind takes back."""
    out = Out()
    flip.play(book(frames=4), out=out, wait=lambda s: False, limit=4)
    s = Screen(cols=80).feed(out.getvalue())
    assert max(s.touched.values()) <= COLS


def test_a_repaint_is_one_synchronized_frame():
    out = Out()
    flip.play(book(frames=3), out=out, wait=lambda s: False, limit=3)
    data = out.getvalue()
    assert data.count(flip.BSU) == data.count(flip.ESU) == 3


def test_the_rate_is_a_period_not_a_pause():
    """Rendering time comes out of the interval, so 30 fps is 30 fps until
    the machine genuinely cannot keep up."""
    clock, asked = Clock(), []

    def wait(s):
        asked.append(s)
        clock.advance(s + 0.005)          # the frame itself costs 5ms
        return len(asked) >= 4

    flip.play(book(frames=8, fps=20.0), out=Out(), wait=wait, clock=clock)
    assert asked[0] == pytest.approx(0.05)
    for s in asked[1:]:
        assert s == pytest.approx(0.045), "the 5ms was not taken off"


def test_it_gives_the_terminal_back():
    out = Out()
    flip.play(book(frames=2), out=out, wait=lambda s: True)
    assert out.getvalue().startswith(flip.HIDE)
    assert out.getvalue().endswith(flip.SHOW + "\x1b[0m")


def test_ctrl_c_gives_the_terminal_back_too():
    def wait(s):
        raise KeyboardInterrupt

    out = Out()
    assert flip.play(book(frames=2), out=out, wait=wait) == 0
    assert out.getvalue().endswith(flip.SHOW + "\x1b[0m")


def test_once_stops_at_the_end_and_looping_does_not():
    once, round_ = Out(), Out()
    flip.play(book(frames=3), out=once, loop=False, wait=lambda s: False)
    flip.play(book(frames=3), out=round_, loop=True, wait=lambda s: False,
              limit=9)
    assert once.getvalue().count(flip.BSU) == 3
    assert round_.getvalue().count(flip.BSU) == 9


def test_an_empty_flipbook_is_not_a_crash():
    assert flip.play(flip.Flipbook([]), out=Out()) == 0
