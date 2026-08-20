"""`gfl pet`: the creature, animated.

The frame itself is the creature's business and is tested next door. What
is tested here is everything around it: that a frame is what the signals
say it is, that the expensive reading is cached, that the loop leaves on a
keypress and restores the terminal, and that a piped `--once` is plain
text with no envelopes in it.
"""

import io
import os
import re
import subprocess
import sys
import time

import pytest

from gracefall import strip_spans
from gracefall.creature import WIDTH, Creature
from gracefall.pet import Signals, ci_env, cpu_load, key_waiter, run
from gracefall.recipes import MARGIN, watch

SGR = re.compile(r"\x1b\[[0-9;]*m")
#: Every CSI, not only colour: a cursor move is not something on screen.
CSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def plain(s):
    return SGR.sub("", strip_spans(s))


class Args:
    """What argparse hands `run()`, without argparse."""

    def __init__(self, **kw):
        self.mood = None
        self.size = 2
        self.every = 0.25
        self.once = True
        self.__dict__.update(kw)


class Out(io.StringIO):
    def __init__(self, tty=False):
        io.StringIO.__init__(self)
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture
def machine(monkeypatch):
    """A machine that reads the same every time, so a frame is a golden."""
    monkeypatch.setattr("gracefall.pet.cpu_load", lambda: 0.62)
    monkeypatch.setattr("gracefall.pet.git_dirty", lambda root=None: False)
    monkeypatch.setenv("GFL_CI", "pass")
    return {"cpu": 0.62, "dirty": False, "ci": "pass"}


# --------------------------------------------------------------------------
# one frame


def test_once_is_the_creature_at_tick_zero(machine):
    out = Out()
    assert run(Args(mood="happy", size=4), out=out) == 0
    want = Creature("happy", machine, size=4).lines(0)
    assert out.getvalue() == "\n" + "".join(MARGIN + l + "\n" for l in want)


def test_once_keeps_the_margin_and_the_width(machine):
    for size in (1, 2, 4):
        out = Out()
        run(Args(size=size), out=out)
        body = [l for l in plain(out.getvalue()).split("\n") if l.strip()]
        assert len(body) == size
        for line in body:
            assert line.startswith(MARGIN)
            assert len(line) == len(MARGIN) + WIDTH


def test_once_is_plain_text_when_envelopes_are_off(machine):
    out = Out()
    run(Args(size=4), emit=False, out=out)
    assert "\x1b]4700" not in out.getvalue()
    # law 4: what is left is ordinary cells, colour and newlines
    assert not re.search(r"[\x00-\x08\x0b-\x1f]", plain(out.getvalue()))


def test_a_frame_is_printed_once_when_stdout_is_not_a_terminal(machine):
    """No animation into a pipe, whatever --once says: repainting in place
    is meaningless there, and a file would fill up with cursor moves."""
    out = Out(tty=False)
    assert run(Args(once=False, every=0.01), out=out) == 0
    assert "\x1b[J" not in out.getvalue()


def test_the_mood_follows_the_signals_unless_one_is_given(monkeypatch):
    monkeypatch.setattr("gracefall.pet.git_dirty", lambda root=None: False)
    monkeypatch.setattr("gracefall.pet.cpu_load", lambda: 0.9)
    monkeypatch.delenv("GFL_CI", raising=False)
    busy = Out()
    run(Args(size=1), out=busy)
    assert plain(busy.getvalue()).strip() == \
        plain(Creature("working", {"cpu": 0.9}, size=1).frame(0)).strip()

    monkeypatch.setenv("GFL_CI", "fail")
    sad = Out()
    run(Args(size=1), out=sad)
    assert plain(sad.getvalue()) != plain(busy.getvalue())

    held = Out()
    run(Args(size=1, mood="sleepy"), out=held)
    assert plain(held.getvalue()).strip() == \
        plain(Creature("sleepy", {"cpu": 0.9, "ci": "fail"},
                       size=1).frame(0)).strip()


# --------------------------------------------------------------------------
# the signals


def test_the_dirty_reading_is_cached_for_its_interval(monkeypatch):
    calls = []
    monkeypatch.setattr("gracefall.pet.git_dirty",
                        lambda root=None: calls.append(root) or True)
    now = [100.0]
    s = Signals(root="/somewhere", every=5.0, ci=lambda: None,
                clock=lambda: now[0])
    for _ in range(20):                   # five seconds of frames at 4 fps
        s.read()
        now[0] += 0.25
    assert len(calls) == 1
    now[0] += 0.01                        # now five seconds have gone
    s.read()
    assert calls == ["/somewhere", "/somewhere"]


def test_cpu_load_is_the_load_over_the_cores_and_is_clamped(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.setattr(os, "getloadavg", lambda: (4.0, 0, 0))
    assert cpu_load() == 0.5
    monkeypatch.setattr(os, "getloadavg", lambda: (99.0, 0, 0))
    assert cpu_load() == 1.0


def test_no_load_average_is_not_an_error(monkeypatch):
    def boom():
        raise OSError("no such thing here")
    monkeypatch.setattr(os, "getloadavg", boom)
    assert cpu_load() == 0.0


def test_a_missing_git_is_not_an_error(monkeypatch, tmp_path):
    from gracefall import pet
    monkeypatch.setattr(pet.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert pet.git_dirty(str(tmp_path)) is False


def test_the_ci_hook_reads_the_environment_and_nothing_else(monkeypatch):
    monkeypatch.delenv("GFL_CI", raising=False)
    assert ci_env() is None
    monkeypatch.setenv("GFL_CI", "PASS")
    assert ci_env() == "pass"
    monkeypatch.setenv("GFL_CI", "whatever")
    assert ci_env() is None


# --------------------------------------------------------------------------
# the loop


def test_watch_stops_when_the_wait_says_a_key_arrived():
    """The one addition to watch(): a wait that can end the loop. Without
    it there is no bound on the frames, so this failing is a hang."""
    frames = []
    out = io.StringIO()
    waits = iter([False, False, True])
    watch(lambda: frames.append(1) or "x", every=0, out=out,
          wait=lambda s: next(waits))
    assert len(frames) == 3
    assert out.getvalue().rstrip().endswith("\x1b[0m")   # the last frame stays


def test_watch_without_a_wait_is_unchanged(monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    out = io.StringIO()
    watch(lambda: "x", every=0.5, out=out, ticks=3)
    assert slept == [0.5, 0.5]            # not after the last frame


def test_a_keypress_ends_the_wait_and_a_quiet_one_times_out():
    r, w = os.pipe()
    try:
        wait = key_waiter(r)
        t = time.monotonic()
        assert wait(0.05) is False
        assert time.monotonic() - t >= 0.04
        os.write(w, b"q")
        t = time.monotonic()
        assert wait(5.0) is True
        assert time.monotonic() - t < 1.0
    finally:
        os.close(r)
        os.close(w)


# --------------------------------------------------------------------------
# end to end


def run_cli(*args, **kw):
    return subprocess.run([sys.executable, "-m", "gracefall.cli", *args],
                          capture_output=True, text=True, **kw)


def test_cli_once_piped_is_one_plain_frame():
    r = run_cli("pet", "--once", "--size", "2")
    assert r.returncode == 0 and "\x1b]4700" not in r.stdout
    body = [l for l in plain(r.stdout).split("\n") if l.strip()]
    assert len(body) == 2
    assert all(len(l) == len(MARGIN) + WIDTH for l in body)


def test_cli_once_forced_carries_the_envelopes():
    r = run_cli("--force-osc", "pet", "--once", "--size", "4")
    assert r.returncode == 0
    for t in ("t=lanes", "t=spark", "t=meter"):
        assert t in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="pty")
def test_on_a_terminal_it_animates_and_a_key_leaves_the_last_frame():
    import pty
    pid, fd = pty.fork()
    if pid == 0:                          # pragma: no cover, the child
        os.execvp(sys.executable, [sys.executable, "-m", "gracefall.cli",
                                   "pet", "--every", "0.05", "--size", "2"])
    seen = b""
    try:
        deadline = time.monotonic() + 20
        while b"A\r\x1b[J" not in seen:   # a second frame, over the first
            seen += _read(fd, deadline)
        assert b"\x1b[?25l" in seen       # the caret is hidden while it runs
        os.write(fd, b"q")
        while b"\x1b[?25h" not in seen:   # and given back on the way out
            seen += _read(fd, deadline)
        status = _reap(fd, pid, deadline)
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        os.close(fd)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    tail = seen.split(b"\x1b[?25l")[-1]
    assert b"\x1b]4700;t=lanes" in tail   # drawn as spans on a terminal
    assert not tail.rstrip().endswith(b"\x1b[J")   # last frame still there


def _read(fd, deadline):
    import select
    if time.monotonic() > deadline:
        raise AssertionError("gfl pet did not answer in time")
    r, _, _ = select.select([fd], [], [], 1.0)
    try:
        return os.read(fd, 65536) if r else b""
    except OSError:                       # linux raises at end of file
        return b""


def _reap(fd, pid, deadline):
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return status
        _read(fd, deadline)
    raise AssertionError("gfl pet did not leave on a keypress")


# --------------------------------------------------------------------------
# the frame rate, and the speed, which are not the same thing


class Clock:
    """A clock the test moves by hand, so a loop is a sequence of instants
    rather than something that has to be waited for."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_the_creature_moves_at_its_own_speed_not_the_frame_rate(machine):
    """The tick used to be a frame counter, so `--every 0.05` moved the
    creature five times faster than `--every 0.25` instead of drawing it
    five times more often. Two loops covering the same second of wall
    clock must end on the same frame."""
    from gracefall.pet import PET_HZ

    def frames_over_a_second(every):
        clock = Clock()
        seen = []
        c = Creature("working", {"cpu": 0.62}, size=2)
        for _ in range(int(round(1.0 / every))):
            seen.append(c.lines((clock.now - 1000.0) * PET_HZ))
            clock.advance(every)
        return seen

    slow, fast = frames_over_a_second(0.25), frames_over_a_second(0.05)
    assert len(fast) == 5 * len(slow), "the faster loop draws more frames"
    for i, frame_ in enumerate(slow):
        assert frame_ == fast[i * 5], \
            f"beat {i * 0.5} differs between the two frame rates"


def test_a_faster_loop_draws_more_distinct_frames(machine):
    """The point of the whole change. At the old default the creature was
    sampled at exactly the rate it moved, so half the repaints were the
    frame that was already on screen."""
    c = Creature("working", {"cpu": 0.62, "rate": 1.0}, size=4)

    def distinct(every, seconds=3.0):
        n = int(seconds / every)
        return len({tuple(c.lines(i * every * 2.0)) for i in range(n)})

    assert distinct(0.05) > distinct(0.25), \
        "sampling four times as often must show more of the motion"


def test_the_signals_are_not_read_every_frame(monkeypatch):
    """Twenty frames a second must not mean twenty load averages a
    second. The creature is carried between readings by its tick."""
    from gracefall.pet import SIGNALS_EVERY
    assert SIGNALS_EVERY >= 0.25, \
        "a reading per frame at 20 fps is 20 syscalls a second to watch a " \
        "number that changes every five"


# --------------------------------------------------------------------------
# the drawn creature


class Env(dict):
    pass


GHOSTTY = Env(GHOSTTY_RESOURCES_DIR="/x", TERM_PROGRAM="ghostty")


def _painter(size=4, env=None, err=None):
    from gracefall.pet import _graphics_painter
    return _graphics_painter(size, GHOSTTY if env is None else env,
                             err or io.StringIO())


def test_graphics_declines_where_there_is_nothing_to_draw_on():
    """A terminal with no image support gets the text creature and a line
    saying why, not a screenful of escape sequences."""
    from gracefall.pet import _graphics_painter
    err = io.StringIO()
    got = _graphics_painter(4, Env(TERM="dumb"), err)
    assert got is None
    assert "kitty graphics protocol" in err.getvalue()
    assert "text" in err.getvalue()


def test_graphics_declines_under_tmux_without_passthrough():
    """tmux eats the images and leaves the blanked cells, which is worse
    than the text the blanking replaced."""
    from gracefall.pet import _graphics_painter
    err = io.StringIO()
    env = Env(GHOSTTY, TMUX="/tmp/x,1,0")
    assert _graphics_painter(4, env, err) is None
    assert "passthrough" in err.getvalue()
    env["GRACEFALL_TMUX_OK"] = "1"
    assert _graphics_painter(4, env, io.StringIO()) is not None


def test_the_image_covers_exactly_the_creatures_cells():
    """SPEC.md's rule, and the reason the creature has one width: the
    image is placed over the creature's own cells and no others."""
    pytest.importorskip("PIL")
    paint = _painter()
    c = Creature("working", {"cpu": 0.5}, size=4)
    body = paint(c.lines(1.0))
    m = re.search(r"\x1b_Ga=T,f=100,c=(\d+),r=(\d+)", body)
    assert m, "no image was placed"
    assert (int(m.group(1)), int(m.group(2))) == (WIDTH, 4)


def test_the_image_lands_under_the_margin_every_other_chart_uses():
    """The drawn creature must sit exactly where the text one sat, or
    turning graphics on nudges it two cells left."""
    pytest.importorskip("PIL")
    paint = _painter()
    body = paint(Creature("idle", size=2).lines(0))
    before = body.split("\x1b_G")[0]
    assert f"\x1b[{len(MARGIN)}C" in before, "not indented to the margin"
    assert "\x1b[3A" in before, "wrong row: two rows plus frame()'s blanks"


def test_the_cells_under_the_image_are_blanked():
    """The fallback text would otherwise show through the transparent
    parts of the image, which is a creature with block art inside it."""
    pytest.importorskip("PIL")
    paint = _painter()
    body = paint(Creature("working", {"cpu": 0.5}, size=4).lines(1.0))
    text = body.split("\x1b_G")[0]
    # Everything before the image is the blanked block and the cursor moves
    # onto it. Nothing printable may be left in it.
    assert not CSI.sub("", text).strip(), "something is drawn under the image"


def test_every_graphics_repaint_deletes_the_previous_image():
    """Cells drawn over an image do not remove it. Without the delete they
    accumulate until the terminal is holding one per frame, which at
    twenty a second is a thousand images a minute."""
    pytest.importorskip("PIL")
    from gracefall.pet import _graphics_watch
    paint = _painter()
    c = Creature("working", {"cpu": 0.5}, size=4)
    out, n = Out(tty=True), [0]

    def draw():
        n[0] += 1
        return c.lines(n[0] * 0.1)

    def wait(_):
        return n[0] >= 4

    _graphics_watch(paint, draw, 0.05, out, wait)
    data = out.getvalue()
    placed = data.count("\x1b_Ga=T")
    assert placed == 4
    assert data.count("a=d,d=A") >= placed, "an image was left behind"


def test_a_graphics_repaint_is_one_synchronized_frame():
    """The erase and the redraw are milliseconds apart at this rate. A
    terminal that presents the gap between them is showing flicker."""
    pytest.importorskip("PIL")
    from gracefall.pet import _graphics_watch
    out = Out(tty=True)
    c = Creature("idle", size=2)
    n = [0]

    def draw():
        n[0] += 1
        return c.lines(n[0] * 0.1)

    _graphics_watch(_painter(2), draw, 0.05, out, lambda _: n[0] >= 3)
    data = out.getvalue()
    assert data.count("\x1b[?2026h") == data.count("\x1b[?2026l") == 3
    for chunk in data.split("\x1b[?2026h")[1:]:
        frame_ = chunk.split("\x1b[?2026l")[0]
        assert "a=d,d=A" in frame_ and "\x1b_Ga=T" in frame_, \
            "the delete and the draw must be inside the same frame"


def test_graphics_gives_the_terminal_back_on_the_way_out():
    """Ctrl-c must not leave a hidden cursor or the last frame's image
    sitting over whatever the shell prints next."""
    pytest.importorskip("PIL")
    from gracefall.pet import _graphics_watch
    out = Out(tty=True)

    def draw():
        raise KeyboardInterrupt

    _graphics_watch(_painter(1), draw, 0.05, out, lambda _: True)
    tail = out.getvalue()
    assert tail.endswith("\x1b[?25h\x1b[0m")
    assert "a=d,d=A" in tail, "images survive the exit"


def test_the_frame_rate_is_a_period_not_a_pause():
    """Sleeping the whole interval on top of the render made the real rate
    whatever was left over: 12 frames a second when 20 were asked for, and
    fewer on a retina cell where there are four times the pixels. The wait
    is against a deadline, so rendering time comes out of it."""
    pytest.importorskip("PIL")
    from gracefall.pet import _graphics_watch
    clock = Clock()
    asked, n = [], [0]

    def paint(lines):
        clock.advance(0.03)          # a frame that takes 30ms to draw
        return "body\n"

    def draw():
        n[0] += 1
        return Creature("idle", size=1).lines(n[0] * 0.1)

    def wait(seconds):
        asked.append(seconds)
        clock.advance(seconds)
        return n[0] >= 4

    _graphics_watch(paint, draw, 0.05, Out(tty=True), wait, clock=clock)
    assert asked == pytest.approx([0.02] * 4), \
        "the render must come out of the interval, not sit on top of it"


def test_a_frame_slower_than_the_interval_does_not_go_backwards():
    """When the machine cannot keep up the loop drops the wait rather than
    asking for a negative one and drifting further behind every frame."""
    pytest.importorskip("PIL")
    from gracefall.pet import _graphics_watch
    clock = Clock()
    asked, n = [], [0]

    def paint(lines):
        clock.advance(0.2)           # five times the interval
        return "body\n"

    def draw():
        n[0] += 1
        return Creature("idle", size=1).lines(0)

    def wait(seconds):
        asked.append(seconds)
        return n[0] >= 3

    _graphics_watch(paint, draw, 0.05, Out(tty=True), wait, clock=clock)
    assert asked == [0.0, 0.0, 0.0]
