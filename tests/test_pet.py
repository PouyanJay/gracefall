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
