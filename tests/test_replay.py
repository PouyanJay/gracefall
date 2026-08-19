"""`gfl replay`: a recorded stream played back, with the creature reading it.

The acceptance is one line long: the bytes that go out are the bytes that
were in the file, whether the creature is there or not. Most of what is
tested here is that, from both ends. The rest is the reading the creature
makes of what goes past, and the scroll region, which has to come back on
every exit path including the ones nobody plans for.

The terminal here is a plain grid that understands the handful of
sequences a replay uses, which is what lets a test say "the creature is
on the bottom line and the stream scrolled above it".
"""

import io
import os
import re
import subprocess
import sys

import pytest

from gracefall import lanes, meter, spark, strip_spans
from gracefall.creature import Creature
from gracefall.recipes import MARGIN
from gracefall.replay import (CELLS_PER_SECOND, CHUNK, Narrator, chunks,
                              moods, read, run, setup, stream_only, teardown)

SGR = re.compile(r"\x1b\[[0-9;]*m")
ROWS, COLS = 10, 60


def plain(s):
    return SGR.sub("", strip_spans(s))


class Args:
    """What argparse hands `run()`, without argparse."""

    def __init__(self, file, **kw):
        self.file = file
        self.speed = 0.0
        self.pet = False
        self.no_pager = True
        self.__dict__.update(kw)


class Out(io.BytesIO):
    """A binary stdout that says whether it is a terminal."""

    def __init__(self, tty=False):
        io.BytesIO.__init__(self)
        self._tty = tty

    def isatty(self):
        return self._tty

    @property
    def text(self):
        return self.getvalue().decode("utf-8", "surrogateescape")


def nap(seconds):
    """A sleep that does not."""


class Clock:
    """A clock the test advances, so a hold is a number rather than a wait."""

    def __init__(self, step=CHUNK):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


@pytest.fixture
def size(monkeypatch):
    monkeypatch.setenv("LINES", str(ROWS))
    monkeypatch.setenv("COLUMNS", str(COLS))


@pytest.fixture
def no_pager(monkeypatch):
    """No pager at all, so the terminal path writes straight out."""
    monkeypatch.setenv("GFL_PAGER", "cat")


@pytest.fixture
def stream(tmp_path):
    """A recording with one of everything the creature reacts to, and
    enough ordinary output between the charts to be paced through."""
    filler = "".join(f"compiling module {i:02d} of 24\n" for i in range(6))
    text = ("build\n" + filler
            + meter(0.9, 12, "coral") + " disk\n"
            + "one test failed\n" + filler
            + meter(0.4, 12, "teal") + " 12 passed\n" + filler
            + lanes([("d", "teal"), (".", None)]) + " v2.4\n" + filler
            + spark([1, 4, 2, 8], "blue") + " tok/s\n")
    p = tmp_path / "s.gfall"
    p.write_text(text, encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# a terminal, in as much detail as a replay needs one


class Grid:
    """The cells a stream lands on: a scroll region, a cursor that can be
    saved and restored, and a screen that scrolls inside the region.

    It knows nothing about OSC 4700 on purpose, so it swallows envelopes
    exactly as an unaware terminal does, which is the view the creature is
    supposed to sit beside.
    """

    def __init__(self, rows=ROWS, cols=COLS):
        self.rows, self.cols = rows, cols
        self.cells = [[" "] * cols for _ in range(rows)]
        self.r = self.c = 0
        self.top, self.bot = 0, rows - 1
        self.saved = (0, 0)

    def row(self, i):
        return "".join(self.cells[i]).rstrip()

    def screen(self):
        return [self.row(i) for i in range(self.rows)]

    def scroll(self):
        del self.cells[self.top]
        self.cells.insert(self.bot, [" "] * self.cols)

    def index(self):
        if self.r == self.bot:
            self.scroll()
        else:
            self.r = min(self.rows - 1, self.r + 1)

    def feed(self, text):
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":
                i = self._escape(text, i)
                continue
            if ch == "\n":
                self.index()
            elif ch == "\r":
                self.c = 0
            elif ch >= " ":
                if self.c >= self.cols:
                    self.c = 0
                    self.index()
                self.cells[self.r][self.c] = ch
                self.c += 1
            i += 1
        return self

    def _escape(self, text, i):
        nxt = text[i + 1:i + 2]
        if nxt == "]":                        # an envelope, swallowed whole
            j = text.find("\x1b\\", i)
            k = text.find("\x07", i)
            if j < 0 or (0 <= k < j):
                j = k
            return len(text) if j < 0 else j + (1 if j == k else 2)
        if nxt == "7":
            self.saved = (self.r, self.c)
            return i + 2
        if nxt == "8":
            self.r, self.c = self.saved
            return i + 2
        if nxt != "[":
            return i + 2
        m = re.compile(r"\x1b\[([?0-9;]*)([A-Za-z])").match(text, i)
        if not m:
            return i + 1
        args, verb = m.group(1), m.group(2)
        if not args.startswith("?"):
            ps = [int(x) if x else 0 for x in args.split(";")] if args else []
            self._csi(ps, verb)
        return m.end()

    def _csi(self, ps, verb):
        if verb == "H":
            self.r = min(self.rows - 1, max(0, (ps[0] if ps else 1) - 1))
            col = (ps[1] if len(ps) > 1 else 1) - 1
            self.c = min(self.cols - 1, max(0, col))
        elif verb == "r":
            self.top = (ps[0] - 1) if ps else 0
            self.bot = (ps[1] - 1) if len(ps) > 1 else self.rows - 1
            self.r = self.c = 0
        elif verb == "K":
            first = ps[0] if ps else 0
            lo = 0 if first == 2 else self.c
            for x in range(lo, self.cols):
                self.cells[self.r][x] = " "
        elif verb == "J":
            for y in range(self.r, self.rows):
                self.cells[y] = [" "] * self.cols


def test_the_grid_scrolls_inside_its_region():
    """The test's terminal, tested: a replay's whole layout rests on the
    bottom line staying put while everything above it moves."""
    g = Grid(rows=4, cols=10).feed("\x1b[4;1Hpet\x1b[1;3r\x1b[3;1H")
    g.feed("a\r\nb\r\nc\r\nd\r\n")
    assert g.screen()[3] == "pet"            # the reserved row never moves
    assert (g.row(0), g.row(1), g.row(2)) == ("c", "d", "")


# --------------------------------------------------------------------------
# the chunks


def test_chunks_join_back_to_the_stream(stream):
    text = stream.read_text(encoding="utf-8")
    for size in (1, 3, 40, 100, 10_000):
        assert "".join(chunks(text, size)) == text
    assert chunks(text, 0) == [text]
    assert chunks("", 10) == []


def test_a_chunk_never_carries_half_an_envelope(stream):
    text = stream.read_text(encoding="utf-8")
    for piece in chunks(text, 7):
        assert piece.count("\x1b]4700;") == piece.count("\x1b\\")
        assert "\x1b" not in strip_spans(SGR.sub("", piece))


def test_chunks_are_counted_in_cells_not_bytes():
    """An envelope is bytes with no cell behind it. Counting them would
    make a stream that is mostly data crawl and one that is mostly text
    race, at the same --speed."""
    text = meter(0.5, 10, "teal") + "abcdefghij"
    assert len(text) > 40                    # the envelope is most of it
    assert len(chunks(text, 10)) == 2
    assert plain(chunks(text, 10)[0]) == "█" * 5 + "▁" * 5


# --------------------------------------------------------------------------
# what the creature makes of it


@pytest.mark.parametrize("chunk,want", [
    (meter(0.8, 10, "coral"), "sad"),
    (meter(0.8, 10, "teal"), "happy"),
    (lanes([("d", "teal"), (".", None)]), "idle"),
    ("2 tests failed", "sad"),
    ("12 passed in 0.4s", "happy"),
    ("Traceback: an error", "sad"),
    (spark([1, 2, 3], "blue"), "working"),
    ("ordinary output", "working"),
    ("   \n", None),
    ("", None),
])
def test_the_reading_of_a_chunk(chunk, want):
    assert read(chunk) == want


def test_the_worst_news_in_a_chunk_wins():
    """A chunk is a twentieth of a second of stream and can hold several
    charts. The creature reacts to the one that matters most."""
    chunk = (meter(0.4, 10, "teal") + " 12 passed  "
             + meter(0.9, 10, "coral") + " 1 failed")
    assert moods(chunk) == ["sad", "happy"]
    assert read(chunk) == "sad"


def test_a_mood_holds_long_enough_to_be_seen():
    """Without the hold a chart that goes past in one chunk would change
    the face for a twentieth of a second, which reads as a flicker."""
    clock = Clock(step=0.1)
    n = Narrator(clock=clock, hold=0.6)
    n.feed(meter(0.9, 10, "coral"))
    assert n.creature.mood == "sad"
    for _ in range(3):
        n.feed("ordinary output")            # 0.3s of stream: still sad
    assert n.creature.mood == "sad"
    for _ in range(5):
        n.feed("ordinary output")
    assert n.creature.mood == "working"


def test_a_stronger_reading_interrupts_the_hold():
    clock = Clock(step=0.01)
    n = Narrator(clock=clock, hold=5.0)
    n.feed("ordinary output")
    assert n.creature.mood == "working"
    n.feed(meter(0.4, 10, "teal"))
    assert n.creature.mood == "happy"
    n.feed(meter(0.9, 10, "coral"))
    assert n.creature.mood == "sad"
    n.feed(meter(0.4, 10, "teal"))           # held: a cheer cannot undo it
    assert n.creature.mood == "sad"


def test_a_quiet_stream_falls_back_to_idle():
    clock = Clock(step=1.0)
    n = Narrator(clock=clock, hold=0.6)
    n.feed("ordinary output")
    assert n.creature.mood == "working"
    n.feed("")
    assert n.creature.mood == "idle"


def test_the_frame_is_one_line_of_the_creature_under_the_margin():
    n = Narrator(clock=Clock(step=0.0), hold=0.6)
    line = n.frame()
    assert line.startswith(MARGIN)
    assert plain(line) == MARGIN + plain(Creature("idle", size=1).frame(0))
    assert "\n" not in line


# --------------------------------------------------------------------------
# the byte copy


def test_piped_replay_is_the_file_and_nothing_else(stream):
    out = Out(tty=False)
    assert run(Args(str(stream)), out=out, sleep=nap) == 0
    assert out.getvalue() == stream.read_bytes()


def test_piped_replay_has_no_creature_and_no_scroll_region(stream):
    """The isatty rule. A pipe, a file and a recording get the stream."""
    out = Out(tty=False)
    run(Args(str(stream), pet=True, speed=4.0), out=out, sleep=nap)
    assert out.getvalue() == stream.read_bytes()
    assert "\x1b7" not in out.text and ";9r" not in out.text


def test_a_stream_that_is_not_utf8_still_comes_out_as_it_went_in(tmp_path):
    """A replay is a byte copy, so a file this cannot decode is not an
    error, and may not be repaired on the way through."""
    p = tmp_path / "raw.gfall"
    p.write_bytes(b"before \xff\xfe after\n" + meter(0.5, 8).encode())
    out = Out(tty=False)
    run(Args(str(p)), out=out, sleep=nap)
    assert out.getvalue() == p.read_bytes()


def test_pacing_does_not_change_a_byte(stream):
    fast, slow = Out(tty=True), Out(tty=True)
    run(Args(str(stream)), out=fast, sleep=nap)
    run(Args(str(stream), speed=0.5), out=slow, sleep=nap)
    assert slow.getvalue() == fast.getvalue() == stream.read_bytes()


def test_pacing_is_a_deadline_rather_than_a_sleep(stream):
    """--speed is the only clock there is: a .gfall file carries no timing
    of its own, so an even pace is what can honestly be offered. It is
    kept against a deadline, so a chunk that took longer than its share to
    write comes out of the next wait instead of adding to it."""
    text = stream.read_text(encoding="utf-8")
    for speed in (0.5, 1.0, 2.0):
        slept = []
        run(Args(str(stream), speed=speed), out=Out(tty=True),
            sleep=slept.append, clock=Clock(step=CHUNK))
        cells = int(CELLS_PER_SECOND * speed * CHUNK)
        assert len(slept) == len(chunks(text, cells)) > 1
        assert max(slept) == 0       # the clock moved one chunk per write


def test_a_missing_file_is_a_clean_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        run(Args(str(tmp_path / "nope.gfall")), out=Out(), sleep=nap)
    assert "nope.gfall" in str(e.value)


# --------------------------------------------------------------------------
# the acceptance: the creature costs the stream nothing


def paints(text):
    """Every creature paint in an output, in order."""
    return re.findall(r"\x1b7.*?\x1b8", text, re.S)


def faces(text):
    """The creature's line out of every paint, as cells alone."""
    moves = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[78]")
    return [moves.sub("", plain(p)) for p in paints(text)]


def test_the_creature_does_not_change_one_byte_of_the_replay(size, no_pager,
                                                             stream):
    """The acceptance from the issue. The creature is drawn between DECSC
    and DECRC on a row outside the scroll region, so the stream above it
    is the stream that would have been there without it."""
    bare, pet = Out(tty=True), Out(tty=True)
    run(Args(str(stream), speed=1.0), out=bare, sleep=nap)
    run(Args(str(stream), pet=True), out=pet, sleep=nap,
        clock=Clock(step=0.4))
    assert paints(pet.text)                  # it really was drawn
    assert stream_only(pet.text, ROWS) == bare.text == \
        stream.read_text(encoding="utf-8")


def test_the_creature_is_drawn_on_the_bottom_line_and_the_stream_above_it(
        size, no_pager, stream):
    out = Out(tty=True)
    run(Args(str(stream), pet=True), out=out, sleep=nap)
    text = out.text
    body = text[:text.rindex(teardown(ROWS))]
    g = Grid().feed(body)
    assert (g.top, g.bot) == (0, ROWS - 2)   # the replay scrolls above it
    assert g.row(ROWS - 1) == faces(text)[-1].rstrip()
    assert g.row(ROWS - 1).startswith(MARGIN)
    above = "\n".join(g.screen()[:ROWS - 1])
    assert "█" in above and "tok/s" in above  # the stream, all of it above
    assert "●" not in above and "○" not in above


def test_the_scroll_region_comes_back_when_the_replay_ends(size, no_pager,
                                                           stream):
    out = Out(tty=True)
    run(Args(str(stream), pet=True), out=out, sleep=nap)
    assert out.text.endswith(teardown(ROWS))
    g = Grid().feed(out.text)
    assert (g.top, g.bot) == (0, ROWS - 1)   # the whole screen scrolls again
    assert g.row(ROWS - 2) == faces(out.text)[-1].rstrip()
    assert g.row(ROWS - 1) == ""             # a prompt lands under it


def test_the_scroll_region_comes_back_on_ctrl_c(size, no_pager, stream):
    def interrupt(seconds):
        raise KeyboardInterrupt

    out = Out(tty=True)
    assert run(Args(str(stream), pet=True), out=out, sleep=interrupt) == 0
    assert out.text.startswith(setup(ROWS))
    assert out.text.endswith(teardown(ROWS))


def test_the_scroll_region_comes_back_when_a_write_raises(size, no_pager,
                                                          stream):
    """The exit path nobody plans for. A terminal left with a scroll
    region on it is a broken terminal, so the region is given back in a
    finally and the exception goes on up."""
    class Breaks(Out):
        def __init__(self):
            Out.__init__(self, tty=True)
            self.left = 4

        def write(self, b):
            self.left -= 1
            if self.left < 0 and b != teardown(ROWS).encode():
                raise RuntimeError("the terminal went away")
            return Out.write(self, b)

    out = Breaks()
    with pytest.raises(RuntimeError):
        run(Args(str(stream), pet=True), out=out, sleep=nap)
    assert out.text.endswith(teardown(ROWS))


def test_the_mood_changes_on_the_spans_that_go_past(size, no_pager, stream):
    """The other half of the issue's acceptance, through the real command:
    the coral meter winces, the teal meter and `passed` cheer, and the
    lanes row is looked up at."""
    out = Out(tty=True)
    run(Args(str(stream), pet=True), out=out, sleep=nap,
        clock=Clock(step=0.4))
    seen = faces(out.text)
    assert any("○" in f for f in seen)       # sad: hollow eyes, a frown
    assert any("╲ ╱" in f for f in seen)     # happy: a smile
    assert any("● ─ ●" in f for f in seen)   # idle: looking up at the graph
    assert any("●───●" in f for f in seen)   # working: reading along
    assert seen[-1].count("─") >= 1 and "○" not in seen[-1]


def test_the_creature_is_dropped_on_a_terminal_with_no_room(monkeypatch,
                                                            no_pager, stream):
    monkeypatch.setenv("LINES", "2")
    monkeypatch.setenv("COLUMNS", str(COLS))
    out = Out(tty=True)
    run(Args(str(stream), pet=True), out=out, sleep=nap)
    assert out.getvalue() == stream.read_bytes()


# --------------------------------------------------------------------------
# the pager


@pytest.fixture
def pager(tmp_path, monkeypatch):
    """A pager that records what it was handed."""
    sink = tmp_path / "paged"
    script = tmp_path / "pager.py"
    script.write_text("import sys\n"
                      "open(sys.argv[1], 'wb')"
                      ".write(sys.stdin.buffer.read())\n")
    monkeypatch.setenv("GFL_PAGER", f"{sys.executable} {script} {sink}")
    return sink


def test_a_terminal_reads_a_recording_through_the_pager(pager, stream):
    out = Out(tty=True)
    assert run(Args(str(stream), no_pager=False), out=out, sleep=nap) == 0
    assert pager.read_bytes() == stream.read_bytes()
    assert out.getvalue() == b""


def test_no_pager_writes_straight_out(pager, stream):
    out = Out(tty=True)
    run(Args(str(stream), no_pager=False), out=out, sleep=nap)
    pager.unlink()
    run(Args(str(stream)), out=out, sleep=nap)
    assert not pager.exists()
    assert out.getvalue() == stream.read_bytes()


def test_the_pet_takes_the_pager_s_place(size, pager, stream):
    """A pager owns the screen and so does a reserved bottom line. Only
    one of them can be right, and the creature was asked for."""
    out = Out(tty=True)
    run(Args(str(stream), pet=True, no_pager=False), out=out, sleep=nap)
    assert not pager.exists()
    assert paints(out.text)


# --------------------------------------------------------------------------
# the command itself


def _cli(*args, **kw):
    return subprocess.run([sys.executable, "-m", "gracefall.cli", "replay"]
                          + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kw)


def test_the_command_piped_is_the_file(stream):
    r = _cli(str(stream))
    assert r.returncode == 0
    assert r.stdout == stream.read_bytes()


def test_the_command_paces_without_changing_anything(stream):
    r = _cli("--speed", "50", str(stream))
    assert r.returncode == 0 and r.stdout == stream.read_bytes()


def test_the_command_says_what_it_wants(tmp_path):
    r = _cli(str(tmp_path / "missing.gfall"))
    assert r.returncode != 0
    assert "Traceback" not in r.stderr.decode()
    assert "missing.gfall" in r.stderr.decode()


@pytest.mark.skipif(sys.platform == "win32", reason="pty")
def test_on_a_real_terminal_the_region_is_reserved_and_given_back(stream):
    """The whole point of the finally, on a terminal that would actually
    keep a scroll region if it were left one."""
    import pty
    pid, fd = pty.fork()
    if pid == 0:                             # pragma: no cover
        os.environ.update(LINES="10", COLUMNS="60", GFL_PAGER="cat")
        os.execvp(sys.executable,
                  [sys.executable, "-m", "gracefall.cli", "replay",
                   "--pet", "--speed", "40", str(stream)])
    seen = b""
    try:
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            seen += b
    except OSError:                          # macOS returns b"", Linux raises
        pass
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    # A terminal translates the newlines it is sent, so the comparison is
    # against what the replay wrote rather than what came back.
    text = seen.decode("utf-8", "surrogateescape").replace("\r\n", "\n")
    assert setup(10) in text and text.endswith(teardown(10))
    assert paints(text)
    g = Grid(rows=10, cols=60).feed(text)
    assert (g.top, g.bot) == (0, 9)          # the terminal got its rows back
    assert "●" in g.row(8) or "○" in g.row(8)
