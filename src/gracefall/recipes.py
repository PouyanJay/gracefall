"""Recipes: charts for commands people already run.

A recipe adds a gracefall chart to a command whose output has numbers in
it. `gfl init zsh` prints shell functions that call these; the user adds
one line to their rc file and `git log`, `df`, `du`, `ping` and their test
runner start showing a chart, as blocks in a plain terminal and drawn in
one that implements OSC 4700.

Three rules, all enforced here or in the generated shell:

1. Only when stdout is a terminal. Pipes, scripts and CI are never touched.
2. Add, never replace. The command's own output stays byte for byte. A
   recipe either prints its chart *before* the real command runs, from a
   query it makes itself, or relays the command's output *through* a pty
   and adds the chart beside it.
3. On anything the parser does not recognise, draw nothing and say
   nothing. Silence is the correct failure here.

Two modes, chosen per recipe:

- "before": the recipe runs its own machine-readable query (`df -Pk`,
  `git log --format=%ct`), prints a chart, and exits. The shell function
  then runs the user's command untouched, with its pager, colours and
  flags. Nothing can go wrong with the original output because it was
  never in our hands.
- "wrap": the recipe runs the command on a pty, relays its output as it
  arrives, and adds a chart: live under the output for `ping`, after the
  summary for a test runner. The pty is what keeps the child's colours and
  progress bars, because the child still sees a terminal.

Everything is stdlib. `pty` and `select` are Unix only, which is also true
of every command here.
"""

import glob
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time

from . import SGR, meter, spark

R = "\x1b[0m"
D = SGR["dim"]

# Chart width in cells. Wide enough to read, narrow enough to leave the
# label and the number on the same line in an 80-column terminal.
W = 30

_RECIPES = {}


def recipe(name, mode, matches=None, when=None, help=""):
    """Register a recipe.

    `matches(argv)` decides whether the user's arguments are the case this
    recipe is for (`git log`, not `git push`); None means always. `when` is
    the same test as shell syntax, so the generated function can skip
    starting Python at all for the cases it does not handle.
    """
    def deco(fn):
        _RECIPES[name] = dict(name=name, mode=mode, fn=fn,
                              matches=matches or (lambda argv: True),
                              when=when, help=help)
        return fn
    return deco


def names():
    return sorted(_RECIPES)


def get(name):
    return _RECIPES.get(name)


def _run(cmd, timeout=10):
    """Run a query command, returning stdout or None. Never raises: a
    missing binary or a failing command means no chart, not a traceback."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _paths(argv):
    """The non-flag arguments: the paths a user handed to df or du."""
    return [a for a in argv if not a.startswith("-")]


def _human(kb):
    for unit in ("K", "M", "G", "T"):
        if kb < 1024:
            return f"{kb:.0f}{unit}" if kb >= 10 or unit == "K" else f"{kb:.1f}{unit}"
        kb /= 1024
    return f"{kb:.1f}P"


def _label_width(labels, cap=28):
    return min(cap, max((len(s) for s in labels), default=0))


# --------------------------------------------------------------------------
# git log: commit activity, one point per day, over the last eight weeks


def _git_log_matches(argv):
    return bool(argv) and argv[0] == "log"


def git_activity(timestamps, days=56, now=None):
    """Bucket unix timestamps into per-day counts, oldest first, so a spark
    of them reads left to right in time. Pure, so it can be tested."""
    now = time.time() if now is None else now
    day = 86400
    start = now - days * day
    counts = [0] * days
    for ts in timestamps:
        if ts < start or ts > now:
            continue
        counts[min(days - 1, int((ts - start) // day))] += 1
    return counts


@recipe("git", "before", matches=_git_log_matches, when='"$1" == log',
        help="git log: a spark of commits per day over the last eight weeks")
def git_log(argv):
    if not shutil.which("git"):
        return None
    # Pathspecs after `--` narrow the query the same way they narrow the
    # log the user is about to see. Everything else is left to the log.
    cmd = ["git", "log", "--format=%ct", "--since=8.weeks"]
    if "--" in argv:
        cmd += argv[argv.index("--"):]
    out = _run(cmd)
    if not out:
        return None
    stamps = [int(t) for t in out.split() if t.isdigit()]
    counts = git_activity(stamps)
    total = sum(counts)
    if total == 0:
        return None
    return (f"{D}commits, last 8 weeks{R}  "
            + spark(counts, lo=0, color="violet")
            + f"  {D}{total} total, {counts[-1]} in the last day{R}")


# --------------------------------------------------------------------------
# df: one meter per volume


def parse_df(text):
    """Parse `df -Pk` output into (mount, used_kb, total_kb), skipping the
    pseudo and system volumes that would drown the ones a person means."""
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        mount = " ".join(parts[5:])
        try:
            total, used = int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if total <= 0:
            continue
        fs = parts[0]
        if fs in ("devfs", "tmpfs", "udev", "map") or fs.startswith("map "):
            continue
        # macOS mounts a dozen APFS helpers under /System/Volumes; only
        # Data is a place a person puts files.
        if mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
            continue
        if mount.startswith(("/private/var/", "/dev", "/proc", "/sys", "/run",
                             "/boot/efi", "/snap/")):
            continue
        rows.append((mount, used, total))
    return rows


@recipe("df", "before",
        help="df: one meter per volume, most full first")
def df(argv):
    if not shutil.which("df"):
        return None
    out = _run(["df", "-Pk"] + _paths(argv))
    if not out:
        return None
    rows = parse_df(out)
    if not rows:
        return None
    rows.sort(key=lambda r: r[1] / r[2], reverse=True)
    lw = _label_width([m for m, _, _ in rows])
    lines = []
    for mount, used, total in rows[:8]:
        frac = used / total
        color = "coral" if frac > 0.9 else "amber" if frac > 0.75 else "teal"
        lines.append(f"{D}{mount[:lw]:<{lw}}{R}  "
                     + meter(frac, width=W, color=color)
                     + f"  {_human(used)} / {_human(total)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# du: one meter per entry, largest first, scaled to the total


def parse_du(text):
    rows = []
    for line in text.splitlines():
        parts = line.split("\t", 1) if "\t" in line else line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            rows.append((parts[1].strip(), int(parts[0])))
        except ValueError:
            continue
    return rows


@recipe("du", "before",
        help="du: one meter per entry, largest first")
def du(argv):
    if not shutil.which("du"):
        return None
    paths = _paths(argv)
    if not paths:
        # `du` alone walks the tree; the question people ask with a chart
        # is "which of these is big", so answer it for the visible entries.
        paths = sorted(p for p in glob.glob("*") if not p.startswith("."))
    if not paths:
        return None
    out = _run(["du", "-sk"] + paths, timeout=30)
    if not out:
        return None
    rows = parse_du(out)
    total = sum(kb for _, kb in rows)
    if not rows or total <= 0:
        return None
    rows.sort(key=lambda r: r[1], reverse=True)
    lw = _label_width([n for n, _ in rows])
    lines = []
    for name, kb in rows[:8]:
        lines.append(f"{D}{name[:lw]:<{lw}}{R}  "
                     + meter(kb / total, width=W, color="blue")
                     + f"  {_human(kb)}")
    if len(rows) > 8:
        rest = sum(kb for _, kb in rows[8:])
        lines.append(f"{D}{'+ ' + str(len(rows) - 8) + ' more':<{lw}}{R}  "
                     + meter(rest / total, width=W, color="dim")
                     + f"  {_human(rest)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# ping: a live latency spark under the output

_PING_TIME = re.compile(rb"time[=<]\s*([0-9.]+)\s*ms")


def parse_ping_line(line):
    """The round-trip time on one ping reply line, or None."""
    m = _PING_TIME.search(line)
    return float(m.group(1)) if m else None


@recipe("ping", "wrap",
        help="ping: a live latency spark under the replies")
def ping(argv, emit):
    return _wrap(["ping"] + argv, PingChart(), emit)


class PingChart:
    """Keeps the last 40 round-trip times and draws them as one line that
    stays below the output. `feed` returns the chart line for the relay to
    keep at the bottom, or None if nothing changed."""

    def __init__(self):
        self.times = []

    def feed(self, line):
        t = parse_ping_line(line)
        if t is None:
            return None
        self.times.append(t)
        self.times = self.times[-40:]
        return self.line()

    def line(self):
        if not self.times:
            return None
        cur = self.times[-1]
        lo, hi = min(self.times), max(self.times)
        color = "teal" if cur < 80 else "amber" if cur < 200 else "coral"
        return (f"{D}rtt{R}  " + spark(self.times, lo=0, color=color)
                + f"  {cur:.3g} ms  {D}min {lo:.3g}  max {hi:.3g}{R}")

    def finish(self, text):
        return None


# --------------------------------------------------------------------------
# test runners: a meter of passed against everything that ran

# pytest: "== 3 passed, 1 failed in 0.4s ==", or without the bars under -q.
_PYTEST = re.compile(r"^[= ]*((?:\d+ \w+(?:, )?)+) in [0-9.]+s", re.M)
_JEST = re.compile(r"^Tests:\s+(.*?)$", re.M)
_MOCHA_PASS = re.compile(r"(\d+) passing")
_MOCHA_FAIL = re.compile(r"(\d+) failing")
_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|"
                    r"xpassed|deselected|warnings?|todo|total)")


def parse_test_summary(text):
    """Find the run's totals in a pytest, jest or mocha transcript.
    Returns dict(passed=, failed=, skipped=) or None."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    for rx in (_PYTEST, _JEST):
        found = rx.findall(text)
        if found:
            counts = dict(passed=0, failed=0, skipped=0)
            for n, what in _COUNT.findall(found[-1]):
                n = int(n)
                if what == "passed":
                    counts["passed"] += n
                elif what in ("failed", "error", "errors"):
                    counts["failed"] += n
                elif what in ("skipped", "todo", "xfailed"):
                    counts["skipped"] += n
            if counts["passed"] or counts["failed"]:
                return counts
    p = _MOCHA_PASS.findall(text)
    if p:
        f = _MOCHA_FAIL.findall(text)
        return dict(passed=int(p[-1]), failed=int(f[-1]) if f else 0,
                    skipped=0)
    return None


class TestChart:
    def feed(self, line):
        return None

    def finish(self, text):
        c = parse_test_summary(text)
        if not c:
            return None
        ran = c["passed"] + c["failed"]
        frac = c["passed"] / ran if ran else 0
        color = "teal" if c["failed"] == 0 else "coral"
        tail = f"{c['passed']} passed"
        if c["failed"]:
            tail += f", {c['failed']} failed"
        if c["skipped"]:
            tail += f", {c['skipped']} skipped"
        return f"{D}tests{R}  " + meter(frac, width=W, color=color) + f"  {tail}"


def _npm_test_matches(argv):
    return bool(argv) and argv[0] in ("test", "t", "run-script")


@recipe("pytest", "wrap", help="pytest: a meter of passed against failed")
def pytest_(argv, emit):
    return _wrap(["pytest"] + argv, TestChart(), emit)


@recipe("npm", "wrap", matches=_npm_test_matches,
        when='"$1" == test || "$1" == t',
        help="npm test: a meter of passed against failed")
def npm(argv, emit):
    return _wrap(["npm"] + argv, TestChart(), emit)


# --------------------------------------------------------------------------
# the pty relay behind every wrap recipe


def _wrap(cmd, chart, emit, out=None):
    """Run `cmd` on a pty, relay its output unchanged, and keep the chart's
    line below it. Returns the child's exit code.

    The chart line is redrawn by moving up one line and clearing it. That
    is safe only if nothing else sits between the last complete output
    line and the chart, so the chart is shown only while no partial line
    is pending: a runner printing progress dots keeps the chart hidden
    until the line completes. Ctrl-C goes to the child, so `ping` prints
    its own statistics on the way out, and the last chart stays on screen
    under them.
    """
    import pty
    import fcntl
    import struct
    import termios

    out = out or sys.stdout.buffer
    if not shutil.which(cmd[0]):
        sys.stderr.write(f"gfl fmt: {cmd[0]} not found\n")
        return 127

    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)

    # Hand the child our window size, or it will wrap at 80.
    try:
        size = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
    except (OSError, ValueError):
        pass

    def forward(signum, _frame):
        try:
            os.kill(pid, signum)
        except OSError:
            pass
    old = signal.signal(signal.SIGINT, forward)

    transcript = []
    partial = b""
    chart_line = None
    shown = False
    try:
        while True:
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            transcript.append(chunk)
            if shown:
                out.write(b"\x1b[1A\x1b[2K\r")
                shown = False
            out.write(chunk)
            partial += chunk
            *lines, partial = partial.split(b"\n")
            for line in lines:
                new = chart.feed(line)
                if new is not None:
                    chart_line = new
            if chart_line is not None and not partial:
                out.write(_encode(chart_line, emit) + b"\r\n")
                shown = True
            out.flush()
    finally:
        signal.signal(signal.SIGINT, old)
    _, status = os.waitpid(pid, 0)

    text = b"".join(transcript).decode("utf-8", "replace")
    final = chart.finish(text)
    if final is not None:
        if partial:
            out.write(b"\r\n")
        out.write(b"\r\n" + _encode(final, emit) + b"\r\n")
        out.flush()
    return os.waitstatus_to_exitcode(status)


def _encode(line, emit):
    from . import strip_spans
    return (line if emit else strip_spans(line)).encode("utf-8")


# --------------------------------------------------------------------------
# shell integration

_ZSH_HEADER = """\
# gracefall recipes. Generated by `gfl init`; do not edit, re-run.
# Each function adds a chart to a command and never touches the command's
# own output. Nothing here runs unless stdout is a terminal.
"""


def init_script(shell):
    """The shell functions for every recipe. zsh and bash share the syntax
    used here."""
    if shell not in ("zsh", "bash"):
        raise ValueError(f"unsupported shell {shell!r}, use zsh or bash")
    lines = [_ZSH_HEADER]
    for name in names():
        r = _RECIPES[name]
        cond = "-t 1" + (f" && ( {r['when']} )" if r["when"] else "")
        if r["mode"] == "before":
            body = (f"  [[ {cond} ]] && gfl fmt {name} \"$@\"\n"
                    f"  command {name} \"$@\"")
        else:
            body = (f"  if [[ {cond} ]]; then gfl fmt {name} \"$@\"; "
                    f"else command {name} \"$@\"; fi")
        lines.append(f"{name}() {{\n{body}\n}}")
    return "\n".join(lines) + "\n"
