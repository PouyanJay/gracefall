"""Wrapping a full-screen tool: a greeting on the way in, what the session
changed on the way out.

`claude`, `vim`, `lazygit` and anything else that takes the whole screen
are the hard case for a recipe. Rule 2 is add, never replace, and a TUI
leaves nothing to add to: it owns every cell, and any byte printed while
it runs lands somewhere it did not plan for. So nothing at all is printed
in between. The creature waves for a second before the child starts, and
the lines it used are taken back before the first paint, so the tool opens
onto the screen it would have had anyway. Then the pty is relayed byte for
byte, with the keyboard and every window size change passed through.

The payoff is on the way out: how long the tool ran, how many commits were
made while it was up, and the diff between the tree as it was and the tree
as it is, through the same `diff_chart` that draws `git diff`, so
uncommitted work counts too. The tree is recorded with `git stash create`,
which writes a commit for a dirty tree without touching the working tree,
the index or the stash ref, and prints nothing when the tree is clean, in
which case HEAD is already the answer.

Outside a repository there is no diff to draw and the summary is the
elapsed time alone; when the tree and the history both come back unchanged
it is one dim line saying so, which is more honest than an empty chart.
"""

import os
import shutil
import signal
import sys
import time

from . import SGR, strip_spans
from .creature import Creature
from .recipes import Chart, MARGIN, _run, _wrap, cols_, recipe
from .recipes_git import diff_chart, parse_numstat

R = "\x1b[0m"
D = SGR["dim"]

#: How long the greeting stays up. Long enough to read one frame, short
#: enough that a tool people start fifty times a day does not feel slower.
SPLASH = 1.0


# --------------------------------------------------------------------------
# the tree, before and after


def _git(args, timeout=10):
    return _run(["git"] + list(args), timeout=timeout)


def _tree(head):
    """A commit for the working tree as it stands. `git stash create`
    makes one when the tree is dirty and prints nothing when it is clean,
    so HEAD is the fallback, and neither case touches anything."""
    out = _git(["stash", "create"])
    return (out or "").strip() or head


def snapshot(now=None):
    """What to compare against later: HEAD, a commit for the tree
    including uncommitted work, and the time. None when there is no git
    here, or no commit to start from."""
    if not shutil.which("git"):
        return None
    head = _git(["rev-parse", "HEAD"])
    if not head or not head.strip():
        return None
    head = head.strip()
    return {"head": head, "tree": _tree(head),
            "at": time.monotonic() if now is None else now}


def since(snap, now=None):
    """The session as numbers: (elapsed seconds, commits, numstat rows).
    Runs the two queries itself, so it is called once, on the way out."""
    elapsed = (time.monotonic() if now is None else now) - snap["at"]
    count = _git(["rev-list", "--count", snap["head"] + "..HEAD"])
    try:
        commits = int((count or "").strip())
    except ValueError:
        commits = 0
    head = (_git(["rev-parse", "HEAD"]) or snap["head"]).strip()
    tree = _tree(head)
    rows = []
    if tree != snap["tree"]:
        out = _git(["diff", "--numstat", snap["tree"], tree], timeout=20)
        if out:
            rows = parse_numstat(out)
    return elapsed, commits, rows


# --------------------------------------------------------------------------
# the summary


def human_time(seconds):
    """An elapsed time a person reads at a glance, never more than two
    units: `9 s`, `4 min`, `1 h 12 min`."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    h, m = divmod(s // 60, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def summary(elapsed, commits=0, rows=(), cols=None, full=False, repo=True):
    """The whole of what is printed when the tool exits. Pure: the queries
    happen in `since`, so this can be tested on made-up numbers."""
    head = f"{D}session{R}  {human_time(elapsed)}"
    if not repo:
        return head
    rows = list(rows)
    chart = diff_chart(rows, cols=cols, full=full) if rows else None
    parts = []
    if commits:
        parts.append(f"{commits} commit{'s' if commits != 1 else ''}")
    if chart:
        parts.append("tree changed")
    if not parts:
        return f"{D}session  {human_time(elapsed)}  ·  nothing changed{R}"
    line = head + "".join(f"{D}  ·  {R}{p}" for p in parts)
    return line + "\n\n" + chart if chart else line


class TuiSummary(Chart):
    """The chart for a wrapped full-screen tool: nothing while it runs,
    the session on exit. `feed` never returns a line, and it carries no
    companion, which together are what keep `_wrap` silent for the whole
    run. The relay refuses to draw beside an interactive child anyway;
    this is the same promise said from the chart's side."""

    def __init__(self, snap, cols=None, full=False, clock=time.monotonic):
        super().__init__(pet=None, clock=clock)
        self.snap = snap
        self.cols = cols
        self.full = full
        self.clock = clock
        # the base already read the clock once; reading it again here would
        # take a second reading of a clock the caller may be injecting
        self.started = self._t0

    def feed(self, line):
        return None

    def line(self):
        return None

    def finish(self, text):
        if self.snap is None:
            return summary(self.clock() - self.started, repo=False)
        elapsed, commits, rows = since(self.snap)
        return summary(elapsed, commits, rows, cols=self.cols, full=self.full)


# --------------------------------------------------------------------------
# the greeting


def splash(label, out=None, delay=SPLASH, size=1, mood="working", tick=0,
           emit=True, sleep=None):
    """One creature frame, then take its lines back.

    The lines are cleared rather than left behind because the tool's first
    paint is next and a full-screen one does not know they are there. The
    cursor is moved up over each line and the line cleared, which is
    relative, so it is correct wherever on the screen we started.
    """
    out = sys.stdout if out is None else out
    sleep = time.sleep if sleep is None else sleep
    lines = Creature(mood, {"cpu": 0.4, "rate": 1.0}, size=size).lines(tick)
    lines[0] = lines[0] + f"  {D}starting {label}…{R}"
    text = "\n".join(MARGIN + l for l in lines) + "\n"
    out.write(text if emit else strip_spans(text))
    out.flush()
    sleep(delay)
    out.write("\x1b[A\x1b[2K" * len(lines) + "\r")
    out.flush()


# --------------------------------------------------------------------------
# the relay's interactive half: the keyboard and the window size
#
# `_wrap` takes this object and asks it for the extra descriptors to watch.
# It lives here rather than in recipes.py because it is only ever wanted by
# a child that owns the screen.


class InteractiveTTY:
    """Raw keyboard for the child, and the window size kept current.

    A full-screen tool reads keys one at a time and draws its own echo, so
    our terminal goes into raw mode for the duration and every byte is
    handed to the pty. SIGWINCH is passed on because a tool that misses a
    resize keeps drawing to the old size. Both are restored on the way
    out, including when the child dies badly: none of this may leave a
    terminal in raw mode.
    """

    def __init__(self, tty_fd=None):
        self.tty_fd = tty_fd
        self.child_fd = None
        self.saved = None
        self.old_winch = None

    def start(self, child_fd):
        """Take the keyboard and start forwarding resizes. Returns the
        descriptors `_wrap` should add to its select, which is empty when
        there is no usable input here."""
        self.child_fd = child_fd
        if self.tty_fd is None:
            try:
                self.tty_fd = sys.stdin.fileno()
            except (AttributeError, ValueError, OSError):
                return []
        try:
            import termios
            import tty
            self.saved = termios.tcgetattr(self.tty_fd)
            # TCSADRAIN, not setraw's default TCSAFLUSH: keys typed before
            # the tool was up are the tool's, and a relay may not eat them.
            tty.setraw(self.tty_fd, termios.TCSADRAIN)
        except Exception:
            self.saved = None
        try:
            self.old_winch = signal.signal(signal.SIGWINCH, self._resize)
        except (ValueError, AttributeError, OSError):
            self.old_winch = None
        return [self.tty_fd]

    def stop(self):
        if self.saved is not None:
            try:
                import termios
                termios.tcsetattr(self.tty_fd, termios.TCSADRAIN, self.saved)
            except Exception:
                pass
            self.saved = None
        if self.old_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, self.old_winch)
            except (ValueError, AttributeError, OSError):
                pass
            self.old_winch = None

    def _resize(self, signum=None, frame=None):
        try:
            import fcntl
            import termios
            size = fcntl.ioctl(self.tty_fd, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(self.child_fd, termios.TIOCSWINSZ, size)
        except Exception:
            pass


# --------------------------------------------------------------------------
# the recipe, and the generic form of it


def around(argv, emit=True, delay=SPLASH, label=None):
    """Run `argv` with the screen entirely its own, and print the session
    under it when it exits. This is `gfl fmt --around cmd ...`, and the
    `claude` recipe is one call to it."""
    if not argv:
        sys.stderr.write("gfl fmt --around: name a command to run\n")
        return 2
    argv = list(argv)
    if not shutil.which(argv[0]):
        sys.stderr.write(f"gfl fmt: {argv[0]} not found\n")
        return 127
    if delay and sys.stdout.isatty():
        splash(label or os.path.basename(argv[0]), delay=delay, emit=emit)
    # After the greeting, not before it: nothing can change the tree while
    # we are the only thing running, and the clock should be the tool's.
    chart = TuiSummary(snapshot(), cols=cols_(), full=_full())
    return _wrap(argv, chart, emit, interactive=InteractiveTTY())


def _full():
    return os.environ.get("GFL_FULL", "") not in ("", "0")


@recipe("claude", "wrap",
        help="claude: a greeting on the way in, the session's diff on the way out")
def claude(argv, emit):
    return around(["claude"] + list(argv), emit)
