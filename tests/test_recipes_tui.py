"""Wrapping a full-screen tool.

The law this file exists to defend: the tool's screen is exactly the screen
it would be without gracefall. The greeting is cleared before the child
starts, nothing is printed while it runs, its bytes reach the terminal
unchanged and the keyboard reaches it, and the summary appears only after
it has exited. The summary itself is a pure function of the numbers, so it
is tested on made-up ones; the numbers come from a real repository.
"""

import os
import re
import subprocess
import sys

import pytest

from gracefall import recipes, strip_spans
from gracefall.creature import WIDTH
from gracefall.recipes_tui import (TuiSummary, human_time, since, snapshot,
                                   splash, summary)

SGR = re.compile(r"\x1b\[[0-9;]*m")

needs_git = pytest.mark.skipif(not __import__("shutil").which("git"),
                               reason="git not installed")
needs_pty = pytest.mark.skipif(sys.platform == "win32", reason="pty")


def plain(s):
    return SGR.sub("", strip_spans(s))


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=T"]
                   + list(args), cwd=str(cwd), check=True, capture_output=True)


def repo(path):
    """A repository with one commit, to snapshot and then change."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "f.txt").write_text("a\nb\nc\n")
    git(path, "init", "-q", ".")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    return path


# --------------------------------------------------------------------------
# the summary, on made-up numbers


def test_human_time_never_shows_more_than_two_units():
    assert human_time(0) == "0 s"
    assert human_time(9.6) == "9 s"
    assert human_time(59) == "59 s"
    assert human_time(60) == "1 min"
    assert human_time(42 * 60 + 30) == "42 min"
    assert human_time(3600) == "1 h"
    assert human_time(3600 + 12 * 60) == "1 h 12 min"
    assert human_time(-5) == "0 s"


def test_summary_outside_a_repository_is_the_time_alone():
    p = plain(summary(75, repo=False))
    assert p == "session  1 min"


def test_summary_says_nothing_changed_in_one_line():
    p = plain(summary(30, commits=0, rows=[]))
    assert p == "session  30 s  ·  nothing changed"
    assert "\n" not in p                        # no chart, not an empty one


def test_summary_counts_commits_and_draws_the_diff():
    rows = [(181, 19, "src/gracefall/recipes.py"), (71, 2, "tests/test_recipes.py")]
    p = plain(summary(42 * 60, commits=3, rows=rows, cols=100))
    lines = p.split("\n")
    assert lines[0] == "session  42 min  ·  3 commits  ·  tree changed"
    assert "gracefall/recipes.py" in p and "+181" in p and "-19" in p
    assert "2 files, +252 -21" in p and "92% added" in p
    # one commit, and the tree left alone, still reads
    assert plain(summary(60, commits=1, rows=[])) == "session  1 min  ·  1 commit"
    for cols in (60, 80, 100, 140):
        for line in plain(summary(60, commits=1, rows=rows, cols=cols)).split("\n"):
            assert len(line) <= cols


def test_the_wrapped_chart_draws_nothing_while_the_child_runs():
    c = TuiSummary(None, clock=iter([100.0, 130.0]).__next__)
    assert c.feed(b"anything at all") is None
    assert c.line() is None
    assert plain(c.finish("")) == "session  30 s"


# --------------------------------------------------------------------------
# the greeting


class Sink:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s

    def flush(self):
        pass


def test_splash_takes_its_lines_back():
    out, slept = Sink(), []
    splash("claude", out=out, delay=1.0, sleep=slept.append)
    assert slept == [1.0]
    shown, cleared = out.text.split("\n", 1)
    assert "starting claude…" in plain(shown)
    # the creature keeps its width, and the line it used is taken back
    assert plain(shown).index("  starting") == len(recipes.MARGIN) + WIDTH
    assert cleared == "\x1b[A\x1b[2K\r"
    assert "\x1b]4700" in shown


def test_splash_clears_every_line_it_used():
    out = Sink()
    splash("vim", out=out, delay=0, size=4, sleep=lambda d: None)
    body, cleared = out.text.rsplit("\n", 1)
    assert len(body.split("\n")) == 4
    assert cleared == "\x1b[A\x1b[2K" * 4 + "\r"


def test_splash_without_envelopes_is_still_a_drawing():
    out = Sink()
    splash("claude", out=out, delay=0, emit=False, sleep=lambda d: None)
    assert "\x1b]4700" not in out.text
    assert len(plain(out.text.split("\n")[0])) > len(recipes.MARGIN) + WIDTH


# --------------------------------------------------------------------------
# the numbers, from a real repository


@needs_git
def test_snapshot_is_none_where_there_is_no_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert snapshot() is None


@needs_git
def test_the_recorded_tree_includes_uncommitted_work(tmp_path, monkeypatch):
    d = repo(tmp_path / "r")
    monkeypatch.chdir(d)
    snap = snapshot()
    assert snap["tree"] == snap["head"]          # clean: HEAD is the tree
    (d / "f.txt").write_text("a\nb\nc\nd\ne\n")
    (d / "new.txt").write_text("one\n")
    git(d, "add", "new.txt")
    # approximately: the elapsed time is the difference of two monotonic
    # readings, and that is a float, not a whole number of seconds
    elapsed, commits, rows = since(snap, now=snap["at"] + 61.5)
    assert elapsed == pytest.approx(61.5) and commits == 0
    assert sorted(rows) == [(1, 0, "new.txt"), (2, 0, "f.txt")]
    p = plain(summary(elapsed, commits, rows, cols=100))
    assert p.split("\n")[0] == "session  1 min  ·  tree changed"


@needs_git
def test_commits_made_while_the_tool_ran_are_counted(tmp_path, monkeypatch):
    d = repo(tmp_path / "r")
    monkeypatch.chdir(d)
    snap = snapshot()
    (d / "f.txt").write_text("a\nb\nc\nd\n")
    git(d, "commit", "-qam", "while the tool was up")
    elapsed, commits, rows = since(snap, now=snap["at"] + 5.5)
    assert commits == 1
    assert rows == [(1, 0, "f.txt")]             # committed work still counts
    assert plain(summary(elapsed, commits, rows)).startswith(
        "session  5 s  ·  1 commit  ·  tree changed")


@needs_git
def test_a_session_that_changed_nothing_says_so(tmp_path, monkeypatch):
    d = repo(tmp_path / "r")
    monkeypatch.chdir(d)
    snap = snapshot()
    elapsed, commits, rows = since(snap, now=snap["at"] + 3.5)
    assert (commits, rows) == (0, [])
    assert plain(summary(elapsed, commits, rows)) == "session  3 s  ·  nothing changed"


# --------------------------------------------------------------------------
# the relay itself, on a real pty


FAKE_TUI = """\
#!{python}
import os, sys
sys.stdout.write({screen!r})
sys.stdout.flush()
sys.stdin.readline()                       # the keyboard has to reach here
open("ran.marker", "w").write("yes")
open("f.txt", "a").write("x\\ny\\nz\\n")
"""

# No newline anywhere in it: a pty translates \\n to \\r\\n on the way out,
# and this test is about bytes arriving unchanged.
SCREEN = "\x1b[?1049h\x1b[2J\x1b[HFAKE TUI\x1b[3;1Hthe whole screen\x1b[?1049l"


def fake_tui(d):
    p = d / "tui.py"
    p.write_text(FAKE_TUI.format(python=sys.executable, screen=SCREEN))
    p.chmod(0o755)
    return str(p)


def run_around(d, argv, keys=b"q\n", after=None, delay=0.05):
    """Run `around(argv)` on a pty in `d`, typing `keys` once `after` has
    been drawn, the way a person types once the tool is up. Returns
    everything the terminal saw, and the exit code."""
    import pty
    import select
    code = ("import sys; sys.path[:0] = %r\n"
            "from gracefall.recipes_tui import around\n"
            "raise SystemExit(around(%r, delay=%r))\n" % (sys.path, argv, delay))
    after = SCREEN.encode() if after is None else after
    pid, fd = pty.fork()
    if pid == 0:                                        # pragma: no cover
        os.chdir(str(d))
        os.environ["COLUMNS"] = "100"
        os.execvp(sys.executable, [sys.executable, "-c", code])
    out, sent = b"", not keys
    while True:
        r, _, _ = select.select([fd], [], [], 20)
        if not r:
            break                                       # the child hung
        try:
            chunk = os.read(fd, 65536)
        except OSError:                 # macOS returns b"", Linux raises
            break
        if not chunk:
            break
        out += chunk
        if not sent and after in out:
            os.write(fd, keys)
            sent = True
    _, status = os.waitpid(pid, 0)
    return out, os.waitstatus_to_exitcode(status)


@needs_pty
@needs_git
def test_the_tools_own_screen_is_exactly_what_it_is_without_gfl(tmp_path):
    d = repo(tmp_path / "r")
    out, rc = run_around(d, [fake_tui(d)])
    assert rc == 0
    assert (d / "ran.marker").exists()           # the keyboard reached it

    # the child's bytes, in one unbroken run, exactly as it wrote them
    assert out.count(SCREEN.encode()) == 1
    head, tail = out.split(SCREEN.encode())

    # before it: the greeting, and the lines it used taken back again
    assert b"starting tui.py" in head
    assert head.endswith(b"\x1b[A\x1b[2K\r")
    # nothing of ours between the greeting and the child's first byte
    assert head.split(b"\x1b[A\x1b[2K\r")[-1] == b""

    # after it: the key we typed, echoed by the child's own tty because
    # this one does not turn echo off, and then the summary, and only then
    assert b"session" not in head
    assert tail.startswith(b"q\r\n")
    assert b"session" in tail and b"tree changed" in tail
    assert b"f.txt" in tail and b"+3" in tail


@needs_pty
@needs_git
def test_the_summary_is_only_the_time_outside_a_repository(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    out, rc = run_around(d, [fake_tui(d)])
    assert rc == 0
    tail = out.split(SCREEN.encode())[1]
    assert b"session" in tail
    assert b"nothing changed" not in tail and b"total" not in tail


@needs_pty
def test_a_missing_command_is_a_clean_error(tmp_path, capsys):
    from gracefall.recipes_tui import around
    assert around(["definitely-not-a-command-xyz"]) == 127
    assert around([]) == 2
    assert "Traceback" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# the recipe


def test_claude_is_a_wrap_recipe_with_a_shell_function():
    r = recipes.get("claude")
    assert r["mode"] == "wrap"
    script = recipes.init_script("zsh")
    assert "claude() {" in script
    assert "gfl fmt claude \"$@\"" in script
    assert "command claude \"$@\"" in script     # not a terminal: untouched


@needs_pty
def test_around_takes_the_rest_of_the_line(tmp_path):
    # `gfl fmt --around vim -c q`: the flags after the command are the
    # command's own, not ours.
    r = run_cli("fmt", "--around", "/bin/echo", "-c", "q")
    assert r.returncode == 0
    assert "-c q" in r.stdout
    assert "\x1b]4700" not in r.stdout          # piped: fallback only

    r = run_cli("fmt", "--around")
    assert r.returncode == 2 and "name a command" in r.stderr
    assert "Traceback" not in r.stderr


def run_cli(*args):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), COLUMNS="100")
    return subprocess.run([sys.executable, "-m", "gracefall.cli"] + list(args),
                          capture_output=True, text=True, env=env)
