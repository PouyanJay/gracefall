"""Recipes: charts for commands people already run.

A recipe adds a gracefall chart to a command whose output has numbers in
it. `gfl init zsh` prints shell functions that call these; the user adds
one line to their rc file and `git log`, `df`, `du`, `ping` and their test
runner start showing a chart, as blocks in a plain terminal and drawn in
one that implements OSC 4700.

Three rules, all enforced here or in the generated shell:

1. Only when stdout is a terminal. Pipes, scripts and CI are never touched.
2. Add, never replace. The command's own output stays byte for byte. A
   recipe either prints its chart *after* the real command returns, from
   a query it makes itself, or relays the command's output *through* a pty
   and adds the chart beside it.
3. On anything the parser does not recognise, draw nothing and say
   nothing. Silence is the correct failure here.

Two modes, chosen per recipe:

- "after": the shell function runs the user's command untouched, with its
  pager, colours and flags, and when it returns the recipe runs its own
  machine-readable query (`df -Pk`, `git log --format=%ct`) and prints a
  chart under it. Nothing can go wrong with the original output because
  it was never in our hands. After rather than before, because a command
  that pages would otherwise cover the chart until the pager quits, and a
  chart under a table reads as its summary.
- "wrap": the recipe runs the command on a pty, relays its output as it
  arrives, and adds a chart: live under the output for `ping`, after the
  summary for a test runner. The pty is what keeps the child's colours and
  progress bars, because the child still sees a terminal.

Everything is stdlib. `pty` and `select` are Unix only, which is also true
of every command here.
"""

import glob
import math
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time

from . import SGR, dist, heat, meter, spark, strip_spans
from .creature import Creature

R = "\x1b[0m"
D = SGR["dim"]

# Chart width in cells. Wide enough to read, narrow enough to leave the
# label and the number on the same line in an 80-column terminal.
W = 30

# The breathing room every chart gets, wherever it is printed: a blank
# line above and below, and this margin on the left of every line. One
# rule in one place, so a chart under df, under git log and under a live
# ping all sit the same way.
MARGIN = "  "


def frame(text):
    """`text` with the recipe margin: a blank line above and below and
    MARGIN at the start of every non-empty line."""
    body = "\n".join((MARGIN + l if l else l) for l in text.split("\n"))
    return "\n" + body + "\n"


def wrap_parts(parts, width, indent, sep="  "):
    """Lay short chart pieces (each a (text, visible_len) pair) into rows no
    wider than `width`, continuation rows indented by `indent` cells. For
    the breakdown lines that would otherwise run off a narrow terminal."""
    rows, row, w = [], [], 0
    for text, n in parts:
        extra = n + (len(sep) if row else 0)
        if row and w + extra > width:
            rows.append(row)
            row, w = [], 0
            extra = n
        row.append(text)
        w += extra
    if row:
        rows.append(row)
    return ("\n" + " " * indent).join(sep.join(r) for r in rows)


def cols_():
    """The width a chart may use: the terminal's, less the margin and one
    cell of slack at the right edge. Every recipe that sizes itself to the
    terminal asks this, so the margin never pushes a chart over."""
    return max(20, shutil.get_terminal_size((80, 24)).columns - len(MARGIN) - 1)


_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible(s):
    """The cell width of a chart line: envelopes and colour do not print."""
    return len(_SGR_RE.sub("", strip_spans(s)))


_RECIPES = {}


def recipe(name, mode, matches=None, when=None, help="", full=False):
    """Register a recipe.

    `matches(argv)` decides whether the user's arguments are the case this
    recipe is for (`git log`, not `git push`); None means always. `when` is
    the same test as shell syntax, so the generated function can skip
    starting Python at all for the cases it does not handle. `full` says
    the function takes `full=True` for a detailed view (`gfl fmt --full`).
    """
    def deco(fn):
        _RECIPES[name] = dict(name=name, mode=mode, fn=fn,
                              matches=matches or (lambda argv: True),
                              when=when, help=help, full=full)
        return fn
    return deco


def names():
    return sorted(_RECIPES)


def get(name):
    return _RECIPES.get(name)


# Commands with several subcommands worth a chart (git, gh) register one
# recipe per command and one entry per subcommand here. The recipe's
# `matches` and shell `when` are derived, so adding a subcommand is one
# decorator.
_SUBS = {}


def sub(command, name, matches=None, when=None, help=""):
    """Register the chart for `command name ...`. `matches(rest)` sees the
    arguments after the subcommand; `when` is the same test in shell on
    "$2" and later, or None for always."""
    def deco(fn):
        _SUBS.setdefault(command, {})[name] = dict(
            fn=fn, matches=matches or (lambda rest: True), when=when, help=help)
        return fn
    return deco


def subs(command):
    return _SUBS.get(command, {})


def _sub_matches(command):
    def m(argv):
        e = subs(command).get(argv[0]) if argv else None
        return bool(e) and e["matches"](argv[1:])
    return m


def _sub_when(command):
    def w():
        parts = []
        for name, e in sorted(subs(command).items()):
            parts.append(f'"$1" == {name}' + (f' && ( {e["when"]} )' if e["when"] else ""))
        return " || ".join(f"( {p} )" for p in parts)
    return w


def _sub_dispatch(command):
    def fn(argv, full=False):
        return subs(command)[argv[0]]["fn"](argv[1:], full=full)
    return fn


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


def _fit(label, width):
    """Truncate a path label from the left, keeping the end, which is the
    part that tells volumes and directories apart."""
    return label if len(label) <= width else "\u2026" + label[-(width - 1):]


# --------------------------------------------------------------------------
# git log: commit activity per day, and with --full also when in the week
# the commits land, who made them, how big they are and where they touch



# Arguments of the user's `git log` that narrow *which* commits are shown.
# These are forwarded to the query, so the chart describes the log the
# person just read. Everything else (--oneline, --graph, -p, --stat,
# --format, --color, ...) only changes how commits are printed, and is
# left out because it would break or restyle our own machine-readable
# query.
_GIT_FILTERS = ("--since", "--after", "--until", "--before", "--author",
                "--committer", "--grep", "--max-count", "--skip", "-n", "-S",
                "-G", "--min-parents", "--max-parents", "--all", "--branches",
                "--tags", "--remotes", "--no-merges", "--merges",
                "--first-parent", "--ancestry-path", "--not", "--invert-grep",
                "--all-match", "--follow", "-i", "-E", "-F", "-P",
                "--regexp-ignore-case", "--basic-regexp", "--extended-regexp",
                "--fixed-strings", "--perl-regexp", "--since-as-filter")
# Filters that put their value in the next word when written without `=`.
_GIT_VALUED = ("--since", "--after", "--until", "--before", "--author",
               "--committer", "--grep", "--max-count", "--skip", "-n", "-S",
               "-G", "--min-parents", "--max-parents", "--since-as-filter")
# Display flags whose value may be a separate word, so that word must not
# be mistaken for a revision.
_GIT_DROP_VALUED = ("--date", "--pretty", "--format", "--encoding", "-L",
                    "--output", "--decorate-refs", "--decorate-refs-exclude")
_GIT_TIMED = ("--since", "--after", "--until", "--before", "--since-as-filter")
_GIT_BOUNDED = _GIT_TIMED + ("--max-count", "--skip", "-n")


def git_query_args(argv):
    """Split the user's `git log` arguments into (filters, bounded, timed).

    `filters` is what to forward to the query. `bounded` is True when the
    user already limited the log in time or count, or named revisions, in
    which case the default eight-week window is not added: `git log
    v0.1.0..v0.2.0` from a year ago should still chart those commits.
    `timed` is the subset of filters that bound the log in time, so the
    axis can be resolved from them. Pure, so it can be tested against real
    argument shapes."""
    out, timed, bounded = [], [], False
    args = list(argv[1:]) if argv[:1] == ["log"] else list(argv)
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            out += args[i:]
            break
        if a.startswith("-"):
            name = a.split("=", 1)[0]
            if not a.startswith("--") and a[:2] in ("-n", "-S", "-G"):
                name = a[:2]                # -n5, -Sneedle: value attached
            if re.fullmatch(r"-\d+", a):
                out.append(a)
                bounded = True
            elif name in _GIT_FILTERS:
                take = [a]
                if name in _GIT_VALUED and "=" not in a and i + 1 < len(args):
                    # -n20 and -Sfoo carry their value attached.
                    if not (name in ("-n", "-S", "-G") and len(a) > 2):
                        i += 1
                        take.append(args[i])
                out += take
                if name in _GIT_BOUNDED:
                    bounded = True
                if name in _GIT_TIMED:
                    # One word per bound: `git rev-parse` only reads the
                    # --since=VALUE spelling.
                    timed.append("=".join(take) if len(take) == 2 else a)
            elif name in _GIT_DROP_VALUED and "=" not in a and len(a) == len(name):
                i += 1
        elif os.path.exists(a) and not re.search(r"\.\.|[~^@]", a):
            out.append(a)               # a path, git narrows by it
        else:
            out.append(a)               # a revision or range
            bounded = True
        i += 1
    return out, bounded, timed


def git_time_bounds(timed):
    """Ask git what the user's --since/--until mean in unix time: `git
    rev-parse --since=2.weeks` prints `--max-age=<ts>`, and --until prints
    `--min-age=<ts>`. Returns (start, end), either None when not given."""
    if not timed:
        return None, None
    out = _run(["git", "rev-parse"] + timed)
    start = end = None
    for line in (out or "").splitlines():
        k, _, v = line.partition("=")
        if v.isdigit():
            if k == "--max-age":
                start = int(v)
            elif k == "--min-age":
                end = int(v)
    return start, end


def git_window(commits, bounded, since=None, until=None, now=None):
    """The (start, end) of the activity axis. Unbounded: the last eight
    weeks. Bounded: the time window the user asked for, or failing that
    the span of the commits they saw."""
    now = time.time() if now is None else now
    if not bounded:
        return now - 56 * 86400, now
    stamps = [ts for ts, _ in commits]
    start = since if since is not None else min(stamps)
    end = until if until is not None else (now if since is not None else max(stamps))
    return start, max(start, end)


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


def parse_git_log(text):
    """Parse `git log --format=%at%x09%an` into [(timestamp, author)]."""
    commits = []
    for line in text.splitlines():
        ts, _, author = line.partition("\t")
        if ts.isdigit():
            commits.append((int(ts), author.strip()))
    return commits


_RENAME = re.compile(r"\{[^{}]* => ([^{}]*)\}")


def parse_git_numstat(text):
    """Parse `git log --format=%x01%at --numstat` into one (timestamp,
    lines_changed, {top_level: churn}) per commit. Binary files count as
    touched but add no lines. Renames are charged to the new path."""
    commits = []
    for line in text.splitlines():
        if line.startswith("\x01"):
            ts = line[1:].strip()
            if ts.isdigit():
                commits.append([int(ts), 0, {}])
            continue
        if not commits:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        n = (int(added) if added.isdigit() else 0) + \
            (int(deleted) if deleted.isdigit() else 0)
        path = _RENAME.sub(r"\1", path)
        if " => " in path:
            path = path.split(" => ", 1)[1]
        top = path.split("/", 1)[0] if "/" in path else path
        commits[-1][1] += n
        commits[-1][2][top] = commits[-1][2].get(top, 0) + n
    return [tuple(c) for c in commits]


def _date(ts, year=False):
    return time.strftime("%b %-d %Y" if year else "%b %-d", time.localtime(ts))


def _span_label(start, end):
    """`Aug 4 to Aug 17`, with years once the span crosses one."""
    y = time.localtime(start).tm_year != time.localtime(end).tm_year
    return f"{_date(start, y)} to {_date(end, y)}"


def git_when(timestamps):
    """A 7 x 24 grid of commit counts, weekday (Monday first) by local
    hour. Pure."""
    grid = [[0] * 24 for _ in range(7)]
    for ts in timestamps:
        t = time.localtime(ts)
        grid[t.tm_wday][t.tm_hour] += 1
    return grid


def _git_activity_line(commits, window, bounded, lw, now=None, cols=None):
    """The one-line summary: a spark of commits per day, with the label
    and figures that make it readable, or None when nothing is in the
    window."""
    now = time.time() if now is None else now
    start, end = window
    days = max(1, int((end - start) // 86400) + 1)
    counts = git_activity([ts for ts, _ in commits], days=days, now=end + 1)
    total = sum(counts)
    if total == 0:
        return None
    if not bounded:
        label = "commits, last 8 weeks"
    elif days == 1:
        label = f"commits, {_date(start)}"
    else:
        label = f"commits, {_span_label(start, end)}"
    if end > now - 86400 and days > 1:
        tail = f"{total} total, {counts[-1]} in the last day"
    elif days > 1:
        tail = f"{total} total, busiest day {max(counts)}"
    else:
        tail = f"{total} total"
    # One cell per day up to eight weeks, fewer when the terminal is
    # narrower than label, spark and figures together.
    width = min(days, 56)
    if cols:
        room = cols - max(lw, len(label)) - len(tail) - 4
        if room < 8:
            # a narrow terminal: keep the spark readable, drop the detail
            tail = f"{total} total"
            room = cols - max(lw, len(label)) - len(tail) - 4
        width = max(8, min(width, room))
    return (f"{D}{label:<{lw}}{R}  "
            + spark(counts, lo=0, width=width, color="violet")
            + f"  {D}{tail}{R}")


def git_dashboard(commits, numstat, window, bounded=False, cols=None):
    """The full view. `commits` is [(timestamp, author)], `numstat` is the
    output of parse_git_numstat or None when that query did not run.
    `cols` is the terminal width; the charts shrink to fit it. Returns the
    text, or None when there is nothing to draw."""
    lw = len("by weekday and hour")
    line = _git_activity_line(commits, window, bounded, lw, cols=cols)
    if line is None:
        return None
    lines = [line]
    # Meters and the dist leave room for the figures and names after them.
    mw = W if not cols else max(10, min(W, cols - lw - 2 - 2 - 5 - 30))

    # when: a heat of weekday by hour, two weekdays per text row. One
    # span per row on a shared scale, so the day names can sit outside
    # the spans; text inside a span's lines would be part of its box.
    grid = git_when(ts for ts, _ in commits)
    hi = max(max(r) for r in grid)
    names = ["Mon Tue", "Wed Thu", "Fri Sat", "Sun"]
    for i, name in enumerate(names):
        label = "by weekday and hour" if i == 0 else ""
        lines.append(f"{D}{label:<{lw}}{R}  "
                     + heat(grid[2 * i:2 * i + 2], color="teal", lo=0, hi=hi)
                     + f"  {D}{name}{R}")
    lines.append(f"{'':<{lw}}  {D}0h    6h    12h   18h{R}")

    # who: one meter per author, scaled to the busiest
    by = {}
    for _, author in commits:
        by[author] = by.get(author, 0) + 1
    top = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
    mx = top[0][1]
    for i, (author, n) in enumerate(top[:5]):
        label = "by author" if i == 0 else ""
        lines.append(f"{D}{label:<{lw}}{R}  " + meter(n / mx, width=mw, color="blue")
                     + f"  {n:<4} {D}{author[:28]}{R}")
    if len(top) > 5:
        rest = sum(n for _, n in top[5:])
        lines.append(f"{'':<{lw}}  " + meter(rest / mx, width=mw, color="dim")
                     + f"  {rest:<4} {D}+ {len(top) - 5} more{R}")

    if numstat:
        # size: lines changed per commit, capped at the 90th percentile so
        # one huge commit does not flatten the rest into the first bin
        sizes = sorted(n for _, n, _ in numstat)
        cap = sizes[int(0.9 * (len(sizes) - 1))]
        if cap > 0:
            median = sizes[len(sizes) // 2]
            tail = f"median {median}, largest {sizes[-1]}"
            if cols and lw + 2 + 8 + 2 + len(tail) > cols:
                tail = f"median {median}"
            bins = min(26, mw) if not cols else max(6, min(26, mw, cols - lw - 4 - len(tail)))
            lines.append(f"{D}{'lines per commit':<{lw}}{R}  "
                         + dist([min(n, cap) for n in sizes], bins=bins,
                                lo=0, hi=cap, color="amber")
                         + f"  {D}{tail}{R}")
            # and the same sizes in time order: churn per commit as it happened
            order = [n for _, n, _ in reversed(numstat)]
            tail = f"{sum(order)} lines over {len(order)} commits"
            ow = min(len(order), mw)
            if cols and lw + 2 + ow + 2 + len(tail) > cols:
                tail = f"{sum(order)} lines"
            lines.append(f"{D}{'churn, in order':<{lw}}{R}  "
                         + spark(order, lo=0, width=ow, color="amber")
                         + f"  {D}{tail}{R}")
        # where: churn per top-level path, as a share of the total
        churn = {}
        for _, _, paths in numstat:
            for top_, n in paths.items():
                churn[top_] = churn.get(top_, 0) + n
        total = sum(churn.values())
        if total > 0:
            paths = sorted(churn.items(), key=lambda kv: (-kv[1], kv[0]))
            for i, (name, n) in enumerate(paths[:5]):
                label = "by path" if i == 0 else ""
                lines.append(f"{D}{label:<{lw}}{R}  "
                             + meter(n / total, width=mw, color="teal")
                             + f"  {round(100 * n / total):>3}% {D}{name[:28]}{R}")
            if len(paths) > 5:
                rest = sum(n for _, n in paths[5:])
                lines.append(f"{'':<{lw}}  " + meter(rest / total, width=mw, color="dim")
                             + f"  {round(100 * rest / total):>3}% "
                             f"{D}+ {len(paths) - 5} more{R}")
    return "\n".join(lines)


@sub("git", "log", help="a spark of commits per day over the last eight weeks; "
                        "--full adds when, who, size and where")
def git_log(argv, full=False):
    if not shutil.which("git"):
        return None
    filters, bounded, timed = git_query_args(argv)
    # Author dates, because that is what `git log` itself prints.
    cmd = ["git", "log", "--format=%at%x09%an"]
    if not bounded:
        cmd.append("--since=8.weeks")
    out = _run(cmd + filters)
    if not out:
        return None
    commits = parse_git_log(out)
    if not commits:
        return None
    window = git_window(commits, bounded, *git_time_bounds(timed))
    cols = cols_()
    if not full:
        line = _git_activity_line(commits, window, bounded, 0, cols=cols)
        # `git log --stat` (or -p, --shortstat, --numstat) is a question
        # about size, so answer it: churn per commit, in time order.
        if line and any(a.split("=", 1)[0] in ("--stat", "--shortstat", "--numstat",
                                                "-p", "--patch") for a in argv):
            stat = _run(cmd[:2] + ["--format=%x01%at", "--numstat"]
                        + cmd[3:] + filters, timeout=20)
            numstat = parse_git_numstat(stat) if stat else None
            if numstat:
                from .recipes_git import churn_line
                churn = churn_line(numstat, cols=cols)
                if churn:
                    line = line + "\n" + churn
        return line
    # The diff stats are a second, slower query with its own timeout, so a
    # large repository still gets the first four sections when this one
    # takes too long.
    stat = _run(cmd[:2] + ["--format=%x01%at", "--numstat"]
                + cmd[3:] + filters, timeout=20)
    numstat = parse_git_numstat(stat) if stat else None
    return git_dashboard(commits, numstat, window, bounded, cols=cols)


# --------------------------------------------------------------------------
# df: one meter per volume, and with --full every volume, space and inodes


def _split_df_line(parts, ncols):
    """Split one df row into (filesystem, numeric columns, mount) when the
    filesystem or the mount point contains spaces (`map auto_home`,
    `/Volumes/Backup Disk`): the numeric block is `ncols` wide, so find
    the first run of that many number-or-percent fields."""
    def num(t):
        return t.rstrip("%").isdigit() or t in ("-",)
    for i in range(1, len(parts) - ncols + 1):
        if all(num(t) for t in parts[i:i + ncols]):
            return " ".join(parts[:i]), parts[i:i + ncols], " ".join(parts[i + ncols:])
    return None


def parse_df_full(blocks, inodes=None):
    """Parse `df -Pk` (and `df -Pki` when given) into one row per volume,
    every volume, in df's own order: dicts with fs, mount, used, total
    (both in KB), iused, ifree (None when unknown). Header driven, so the
    macOS `iused ifree %iused` and Linux `Inodes IUsed IFree` shapes both
    read."""
    rows = []
    lines = blocks.splitlines()
    if not lines:
        return rows
    for line in lines[1:]:
        parts = line.split()
        got = _split_df_line(parts, 4)
        if not got:
            continue
        fs, cols, mount = got
        try:
            total, used, avail = int(cols[0]), int(cols[1]), int(cols[2])
        except ValueError:
            continue
        rows.append(dict(fs=fs, mount=mount, used=used, total=total,
                         avail=avail, iused=None, ifree=None))
    if inodes:
        ilines = inodes.splitlines()
        head = [h.lower() for h in ilines[0].split()] if ilines else []
        try:
            m = head.index("mounted")
        except ValueError:
            m = None
        if m:
            ncols = m - 1
            if "iused" in head and "ifree" in head:
                ui, fi = head.index("iused") - 1, head.index("ifree") - 1
            elif "inodes" in head:
                ui, fi = head.index("inodes"), head.index("inodes") + 1
            else:
                ui = fi = None
            by_mount = {r["mount"]: r for r in rows}
            for line in ilines[1:]:
                got = _split_df_line(line.split(), ncols)
                if not got or ui is None:
                    continue
                _, cols, mount = got
                r = by_mount.get(mount)
                if r is None or fi >= len(cols):
                    continue
                if cols[ui].isdigit() and cols[fi].isdigit():
                    r["iused"], r["ifree"] = int(cols[ui]), int(cols[fi])
    return rows


def parse_df(text):
    """Parse `df -Pk` output into (mount, used_kb, total_kb, avail_kb),
    skipping the pseudo and system volumes that would drown the ones a
    person means."""
    rows = []
    for r in parse_df_full(text):
        mount, total, used, fs = r["mount"], r["total"], r["used"], r["fs"]
        if total <= 0:
            continue
        if fs in ("devfs", "tmpfs", "udev", "map") or fs.startswith("map "):
            continue
        # macOS mounts a dozen APFS helpers under /System/Volumes; only
        # Data is a place a person puts files.
        if mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
            continue
        if mount.startswith(("/private/var/", "/dev", "/proc", "/sys", "/run",
                             "/boot/efi", "/snap/")):
            continue
        rows.append((mount, used, total, r["avail"]))
    return rows


def _fill_color(frac):
    return "coral" if frac > 0.9 else "amber" if frac > 0.75 else "teal"


def _df_frac(used, total, avail):
    """How full, the way df's Capacity column counts it: used against
    used plus available, so reserved blocks and shared APFS containers
    give the same percent df prints."""
    return used / (used + avail) if used + avail > 0 else (used / total if total else 0.0)


def _pct(frac):
    """df rounds its percent up; so does the label next to the meter."""
    return math.ceil(100 * frac - 1e-9)


def df_panel(rows, cols=None):
    """The full view: every volume, most full first, with a space meter,
    percent, used / total, an inode meter when df reported inodes, and
    the device. Zero-size pseudo volumes come last, dim, so the panel
    covers df's whole table. Pure."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (-_df_frac(r["used"], r["total"], r["avail"])
                                       if r["total"] else 1, r["mount"]))
    wide = cols is None
    lw = _label_width([r["mount"] for r in rows], cap=24 if wide or cols >= 100 else 16)
    fw = _label_width([r["fs"] for r in rows], cap=16)
    # The inode column and the device fold away as the terminal narrows;
    # label, meter, percent and used / total always stay.
    has_inodes = any(r["iused"] is not None for r in rows) and (wide or cols >= 90)
    show_fs = wide or cols >= 80
    # label, meter, " 35%", "311G / 926G", then "inodes ▁▁▁▁▁▁▁▁  0%" and
    # the device; the space meter takes what the width leaves
    fixed = (lw + 2 + 2 + 4 + 2 + 13 + (2 + 7 + 8 + 5 if has_inodes else 0)
             + (2 + fw if show_fs else 0))
    mw = W if wide else max(10, min(W, cols - fixed))
    lines = []
    for r in rows:
        if r["total"] <= 0:
            lines.append((f"{D}{_fit(r['mount'], lw):<{lw}}  {'':<{mw}}  {'':<4}  "
                          f"{'':<13}"
                          + (f"  {'':<20}" if has_inodes else "")
                          + (f"  {r['fs'][:fw]}" if show_fs else "")).rstrip() + R)
            continue
        frac = _df_frac(r["used"], r["total"], r["avail"])
        line = (f"{D}{_fit(r['mount'], lw):<{lw}}{R}  "
                + meter(frac, width=mw, color=_fill_color(frac))
                + f"  {_pct(frac):>3}%  "
                + f"{_human(r['used']) + ' / ' + _human(r['total']):<13}")
        if has_inodes:
            if r["iused"] is not None and r["iused"] + r["ifree"] > 0:
                ifrac = r["iused"] / (r["iused"] + r["ifree"])
                line += (f"  {D}inodes{R} " + meter(ifrac, width=8, color=_fill_color(ifrac))
                         + f" {_pct(ifrac):>3}%")
            else:
                line += f"  {'':<20}"
        if show_fs:
            line += f"  {D}{r['fs'][:fw]}{R}"
        lines.append(line)
    return "\n".join(lines)


@recipe("df", "after", full=True,
        help="df: one meter per volume, most full first; --full adds every "
             "volume, percent, inodes and the device")
def df(argv, full=False):
    if not shutil.which("df"):
        return None
    out = _run(["df", "-Pk"] + _paths(argv))
    if not out:
        return None
    if full:
        rows = parse_df_full(out, _run(["df", "-Pki"] + _paths(argv)))
        cols = cols_()
        return df_panel(rows, cols=cols)
    rows = parse_df(out)
    if not rows:
        return None
    rows.sort(key=lambda r: _df_frac(r[1], r[2], r[3]), reverse=True)
    lw = _label_width([m for m, _, _, _ in rows])
    mw = max(10, min(W, cols_() - lw - 2 - 6 - 13))
    lines = []
    for mount, used, total, avail in rows[:8]:
        frac = _df_frac(used, total, avail)
        lines.append(f"{D}{_fit(mount, lw):<{lw}}{R}  "
                     + meter(frac, width=mw, color=_fill_color(frac))
                     + f"  {_pct(frac):>3}%  {_human(used)} / {_human(total)}")
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


def du_depth(argv):
    """The depth a `du --max-depth=N`, `-d N` or `-dN` asked for, else None."""
    for i, a in enumerate(argv):
        if a.startswith("--max-depth="):
            v = a.split("=", 1)[1]
            return int(v) if v.isdigit() else None
        if a == "--max-depth" or a == "-d":
            v = argv[i + 1] if i + 1 < len(argv) else ""
            return int(v) if v.isdigit() else None
        if a.startswith("-d") and a[2:].isdigit():
            return int(a[2:])
    return None


def du_chart(rows, cols=None, full=False):
    """Meters per entry scaled to the sum, largest first, the tail folded
    into "+ N more"; with --full every entry and a dist of the sizes. Pure."""
    total = sum(kb for _, kb in rows)
    if not rows or total <= 0:
        return None
    rows = sorted(rows, key=lambda r: (-r[1], r[0]))
    top = rows if full else rows[:8]
    lw = _label_width([n for n, _ in top])
    mw = W if not cols else max(10, min(W, cols - lw - 2 - 2 - 8))
    lines = []
    for name, kb in top:
        lines.append(f"{D}{_fit(name, lw):<{lw}}{R}  "
                     + meter(kb / total, width=mw, color="blue")
                     + f"  {_human(kb)}")
    if len(rows) > len(top):
        rest = sum(kb for _, kb in rows[len(top):])
        lines.append(f"{D}{'+ ' + str(len(rows) - len(top)) + ' more':<{lw}}{R}  "
                     + meter(rest / total, width=mw, color="dim")
                     + f"  {_human(rest)}")
    if full and len(rows) >= 3:
        sizes = [kb for _, kb in rows]
        cap = max(1, sorted(sizes)[int(0.9 * (len(sizes) - 1))])
        tail = f"{len(rows)} entries, {_human(total)} total"
        bins = min(26, mw) if not cols else max(6, min(26, mw, cols - lw - 4 - len(tail)))
        lines.append(f"{D}{'sizes':<{lw}}{R}  "
                     + dist([min(s, cap) for s in sizes], bins=bins, lo=0, hi=cap,
                            color="violet")
                     + f"  {D}{tail}{R}")
    return "\n".join(lines)


@recipe("du", "after", full=True,
        help="du: one meter per entry, largest first; --max-depth honoured; "
             "--full adds every entry and a dist of the sizes")
def du(argv, full=False):
    if not shutil.which("du"):
        return None
    depth = du_depth(argv)
    # `-d 1` carries its value as a separate word, which is not a path
    args = list(argv)
    for i, a in enumerate(args):
        if a in ("-d", "--max-depth") and i + 1 < len(args):
            args[i + 1] = "-" + args[i + 1]
    paths = _paths(args)
    if depth is not None:
        # `du -h --max-depth=1`: chart the entries at that depth, not the
        # totals of the paths themselves, which the table already shows.
        out = _run(["du", "-k", "-d", str(depth)] + (paths or ["."]), timeout=30)
        if not out:
            return None
        rows = [(n, kb) for n, kb in parse_du(out)
                if n.rstrip("/") not in {p.rstrip("/") for p in (paths or ["."])}]
        return du_chart(rows, cols=cols_(), full=full)
    if not paths:
        # `du` alone walks the tree; the question people ask with a chart
        # is "which of these is big", so answer it for the visible entries.
        paths = sorted(p for p in glob.glob("*") if not p.startswith("."))
    if not paths:
        return None
    out = _run(["du", "-sk"] + paths, timeout=30)
    if not out:
        return None
    return du_chart(parse_du(out), cols=cols_(), full=full)


# --------------------------------------------------------------------------
# the live line of a wrap recipe, and the creature that rides it

#: Seconds the relay waits on the child before redrawing the live line.
#: Short enough that the creature keeps time, long enough to be nothing.
PET_TICK = 0.25

#: Animation frames a second. The creature's frames are pure functions of
#: a tick, and time is what turns them into motion.
PET_HZ = 2.0

#: Values of GFL_PET that mean "no creature".
PET_OFF = ("0", "no", "off", "false")

_PET = True


def set_pet(on):
    """Turn the companion off for this process, for `gfl fmt --no-pet`."""
    global _PET
    _PET = bool(on)


def companion(mood="idle", size=1):
    """The creature for a wrap recipe's live line, or None.

    None when `--no-pet` was passed, when GFL_PET says so, and whenever
    stdout is not a terminal. The last one is the important one: the
    companion is an animation redrawn in place, and a pipe, a log file or
    a CI transcript has nothing to redraw.
    """
    if not _PET or os.environ.get("GFL_PET", "") in PET_OFF:
        return None
    try:
        if not sys.stdout.isatty():
            return None
    except (AttributeError, ValueError):
        return None
    return Creature(mood, size=size)


class Chart:
    """What the pty relay talks to, and where the companion lives.

    The relay calls `feed(line)` with every complete line the child wrote,
    `peek(partial)` with the incomplete line still on screen, `tick()`
    about four times a second while the child is quiet, and `finish(text)`
    with the whole transcript once it has exited. `feed` and `tick` return
    the line to keep under the output, or None to leave what is there.

    With no companion `tick` returns None and `live` hands back the
    chart's own line untouched, so a chart with the pet off writes exactly
    the bytes it wrote before the pet existed.
    """

    def __init__(self, pet=None, clock=time.monotonic):
        self.pet = pet
        self._clock = clock
        self._t0 = clock()

    # ---- the protocol the relay uses

    def feed(self, line):
        return None

    def peek(self, partial):
        """The line the child has not finished writing. Charts that read a
        progress stream override this; the rest ignore it."""
        return None

    def tick(self):
        """One animation frame, or None when nothing is moving."""
        if self.pet is None:
            return None
        return self.live(self.line())

    def finish(self, text):
        return None

    # ---- the chart's own line

    def line(self):
        """The chart's live line, or None while it has nothing to say."""
        return None

    # ---- the companion

    @property
    def ticks(self):
        """The frame number, taken from the clock rather than counted, so
        the creature moves at one speed whether the child is flooding the
        relay or has been silent for a minute."""
        return int((self._clock() - self._t0) * PET_HZ)

    def live(self, line, tick=None):
        """`line` with the creature beside it, or the creature on its own
        while the chart has nothing to draw yet. The chart always wins: if
        the two do not fit the terminal, the creature is dropped."""
        if self.pet is None:
            return line
        pet = self.pet.frame(self.ticks if tick is None else tick)
        if line is None:
            return pet
        if visible(line) + 2 + self.pet.width() > cols_():
            return line
        return line + "  " + pet

    def verdict(self, line):
        """`live` for the frame that stays on screen once the command is
        done. Tick 0 is the still pose, and its eyes are open."""
        return self.live(line, tick=0)


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
    return _wrap(["ping"] + argv, PingChart(companion("idle")), emit)


class PingChart(Chart):
    """Keeps the last 40 round-trip times and draws them as one line that
    stays below the output. `feed` returns the chart line for the relay to
    keep at the bottom, or None if nothing changed."""

    def __init__(self, pet=None, clock=time.monotonic):
        Chart.__init__(self, pet, clock)
        self.times = []

    def feed(self, line):
        t = parse_ping_line(line)
        if t is None:
            return None
        self.times.append(t)
        self.times = self.times[-40:]
        self._read(t)
        return self.live(self.line())

    def _read(self, t):
        """The mood of a reply, on the same thresholds the spark is
        coloured by: a quick answer is a happy one, a slow one is work,
        and past a fifth of a second the creature gives up on it. The
        round trip itself is the droop in the arms, so a bad link bobs.
        """
        if self.pet is None:
            return
        lo, hi = min(self.times), max(self.times)
        self.pet.mood = "happy" if t < 80 else "working" if t < 200 else "sad"
        self.pet.update(latency=t / 100.0, rate=1.0,
                        cpu=0.0 if hi <= lo else (t - lo) / (hi - lo))

    def line(self):
        if not self.times:
            return None
        cur = self.times[-1]
        lo, hi = min(self.times), max(self.times)
        color = "teal" if cur < 80 else "amber" if cur < 200 else "coral"
        return (f"{D}rtt{R}  " + spark(self.times, lo=0, color=color)
                + f"  {cur:.3g} ms  {D}min {lo:.3g}  max {hi:.3g}{R}")


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


# The progress stream: pytest writes one character per test as it runs,
# `.` for a pass and `F` or `E` for a failure, alone under -q and after
# the file's path otherwise, with a percentage at the end of a full line.
_PCT = re.compile(r"\[\s*\d+%\]\s*$")
_MARKS = re.compile(r"^[.FEsxXuP]+$")


def parse_progress(text):
    """(passed, failed) from one line of a test runner's progress stream.

    Only the last token is read, and only when the line is nothing but
    marks or the token before it is a test file. `collecting ...` ends in
    three dots and is not three passing tests, and a miscount here would
    put the creature in the wrong mood in front of the person running the
    suite.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    line = _PCT.sub("", _SGR_RE.sub("", text).rstrip("\r\n")).rstrip()
    parts = line.split()
    if not parts or not _MARKS.match(parts[-1]):
        return 0, 0
    if len(parts) > 1 and not (parts[-2].endswith(".py") or "::" in parts[-2]):
        return 0, 0
    marks = parts[-1]
    return marks.count("."), marks.count("F") + marks.count("E")


class TestChart(Chart):
    """A meter of passed against everything that ran.

    The meter itself is drawn once, from the runner's own summary line,
    and that is the only thing this chart printed before the companion
    existed. With a companion it also keeps a live line while the suite
    runs, counted off the progress stream: the creature paces, and turns
    coral the moment a test fails.
    """

    #: pytest collects any class called Test*, and this one is a chart.
    __test__ = False

    def __init__(self, pet=None, clock=time.monotonic):
        Chart.__init__(self, pet, clock)
        self.passed = self.failed = 0
        self._done = (0, 0)         # from the lines that are complete

    def feed(self, line):
        if self.pet is None:
            return None
        p, f = parse_progress(line)
        self._done = (self._done[0] + p, self._done[1] + f)
        self._count(0, 0)
        return self.live(self.line())

    def peek(self, partial):
        """The dots arrive without a newline behind them, so the line the
        child is still writing is where the count actually lives."""
        if self.pet is None:
            return None
        self._count(*parse_progress(partial))
        return None

    def _count(self, p, f):
        self.passed, self.failed = self._done[0] + p, self._done[1] + f
        self.pet.mood = "sad" if self.failed else "working"
        n = self.passed + self.failed
        self.pet.update(ci="fail" if self.failed else None,
                        cpu=min(1.0, 0.25 + n / 200.0), rate=1.0)

    def line(self):
        if self.pet is None or not (self.passed + self.failed):
            return None
        return self._meter(self.passed, self.failed, 0)

    def _meter(self, passed, failed, skipped):
        ran = passed + failed
        tail = f"{passed} passed"
        if failed:
            tail += f", {failed} failed"
        if skipped:
            tail += f", {skipped} skipped"
        return (f"{D}tests{R}  "
                + meter(passed / ran if ran else 0, width=W,
                        color="teal" if failed == 0 else "coral")
                + f"  {tail}")

    def finish(self, text):
        c = parse_test_summary(text)
        if not c:
            return None
        if self.pet is not None:
            self.pet.mood = "sad" if c["failed"] else "happy"
            self.pet.update(ci="fail" if c["failed"] else "pass",
                            cpu=0.0, rate=0.0)
        return self.verdict(self._meter(c["passed"], c["failed"], c["skipped"]))


def _npm_test_matches(argv):
    return bool(argv) and argv[0] in ("test", "t", "run-script")


@recipe("pytest", "wrap", help="pytest: a meter of passed against failed")
def pytest_(argv, emit):
    return _wrap(["pytest"] + argv, TestChart(companion("working")), emit)


@recipe("npm", "wrap", matches=_npm_test_matches,
        when='"$1" == test || "$1" == t',
        help="npm test: a meter of passed against failed")
def npm(argv, emit):
    return _wrap(["npm"] + argv, TestChart(companion("working")), emit)


# --------------------------------------------------------------------------
# watch: redraw an "after" recipe in place


def watch(draw, every=2.0, emit=True, out=None, ticks=None, wait=None):
    """Call `draw()` every `every` seconds and repaint its text in place
    until ctrl-c. Each repaint moves the cursor back up over the previous
    frame and clears from there down, so a frame with fewer lines leaves
    nothing behind. `ticks` bounds the loop, for tests.

    `wait(seconds)` replaces the sleep between frames, and returning true
    from it stops the loop with the last frame still on screen. That is
    how `gfl pet` leaves on a keypress."""
    out = sys.stdout if out is None else out
    prev = 0
    n = 0
    try:
        while ticks is None or n < ticks:
            text = draw() or f"{D}nothing to draw{R}"
            if not emit:
                text = strip_spans(text)
            text = frame(text + f"\n{D}every {every:g}s, ctrl-c to stop{R}")
            up = f"\x1b[{prev}A" if prev else ""
            out.write(up + "\r\x1b[J" + text)
            out.flush()
            prev = text.count("\n")
            n += 1
            if ticks is None or n < ticks:
                if (wait or time.sleep)(every):
                    break
    except KeyboardInterrupt:
        out.write("\n")
        out.flush()
    return 0


# --------------------------------------------------------------------------
# the pty relay behind every wrap recipe


def _wrap(cmd, chart, emit, out=None, interactive=None):
    """Run `cmd` on a pty, relay its output unchanged, and keep the chart's
    line below it. Returns the child's exit code.

    The chart line, and the blank line above it, are redrawn by moving up
    two lines and clearing from there, which lands the cursor back on the
    line the child was writing. Without a companion that line is always
    empty, because the chart is only shown once a line is complete: a
    runner printing progress dots keeps it hidden until the dots end. With
    a companion the creature has to keep moving while the dots are still
    coming, so the pending text is written again as the line is cleared,
    putting the cursor back where the child left it.

    A second clock runs alongside the child: `feed` only happens when
    there is output, so on every quiet quarter second the relay asks the
    chart to `tick`, which is what animates the creature under a command
    that is thinking. A chart with no companion returns None from `tick`
    and nothing is written at all.

    Ctrl-C goes to the child, so `ping` prints its own statistics on the
    way out, and the last chart stays on screen under them.

    `interactive` is for a child that owns the whole screen: an object
    whose `start(fd)` takes the keyboard and returns the descriptors to
    relay from, and whose `stop()` gives it back. Nothing is drawn beside
    such a child and nothing of its output is kept, because its bytes are
    cursor addressing rather than lines.
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
    ins = interactive.start(fd) if interactive is not None else []

    transcript = []
    partial = b""
    chart_line = None
    shown = False
    pet = chart.pet is not None

    def hide():
        # the live chart sits two lines down: a blank line and itself.
        # Take both away, and put back whatever the child had written on
        # the line we land on, which is nothing unless a pet is drawing
        # over an unfinished line.
        out.write(b"\x1b[2A\x1b[J\r" + partial)

    try:
        while True:
            r, _, _ = select.select([fd] + ins, [], [], PET_TICK)
            if not r:
                if interactive is not None:
                    continue        # a full-screen child is never drawn beside
                new = chart.tick()
                if new is None:
                    continue
                chart_line = new
                if shown:
                    hide()
                out.write(b"\r\n" + _encode(MARGIN + chart_line, emit) + b"\r\n")
                shown = True
                out.flush()
                continue
            if ins and ins[0] in r:
                try:                        # the keyboard belongs to the child
                    key = os.read(ins[0], 65536)
                    if key:
                        os.write(fd, key)
                except OSError:
                    key = b""
                if not key:
                    ins = []                # closed: the child keeps its own
            if fd not in r:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            if interactive is None:
                transcript.append(chunk)
            if shown:
                hide()
                shown = False
            out.write(chunk)
            # a full-screen child writes cursor addressing rather than
            # lines: there is nothing to feed a chart and nothing to keep
            partial += chunk if interactive is None else b""
            *lines, partial = partial.split(b"\n")
            for line in lines:
                new = chart.feed(line)
                if new is not None:
                    chart_line = new
            chart.peek(partial)
            if chart_line is not None and (not partial or pet):
                out.write(b"\r\n" + _encode(MARGIN + chart_line, emit) + b"\r\n")
                shown = True
            out.flush()
    finally:
        signal.signal(signal.SIGINT, old)
        if interactive is not None:
            interactive.stop()
    _, status = os.waitpid(pid, 0)

    text = b"".join(transcript).decode("utf-8", "replace")
    final = chart.finish(text)
    if final is not None:
        if pet and shown:
            # the live line is about to be said better, and once the pet
            # is drawing over unfinished lines it may be sitting on one
            hide()
            shown = False
        if partial:
            out.write(b"\r\n")
        out.write(_encode(frame(final).replace("\n", "\r\n"), emit) + b"\r\n")
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
        when = r["when"]() if callable(r["when"]) else r["when"]
        cond = "-t 1" + (f" && ( {when} )" if when else "")
        if r["mode"] == "after":
            # The function's exit status must stay the command's own, not
            # the recipe's, or `df / || echo full` stops meaning anything.
            body = (f"  command {name} \"$@\"\n"
                    f"  local rc=$?\n"
                    f"  [[ {cond} ]] && gfl fmt {name} \"$@\"\n"
                    f"  return $rc")
        else:
            body = (f"  if [[ {cond} ]]; then gfl fmt {name} \"$@\"; "
                    f"else command {name} \"$@\"; fi")
        lines.append(f"{name}() {{\n{body}\n}}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# the multi-subcommand recipes: git and gh. Their subcommands live in
# recipes_git.py and recipes_gh.py (git log is above); importing them
# registers the entries, and the two recipes below dispatch on "$1".

from . import recipes_git, recipes_gh, recipes_sys, recipes_tui  # noqa: E402,F401  (registration)

recipe("git", "after", matches=_sub_matches("git"), when=_sub_when("git"), full=True,
       help="git log, shortlog, diff, branch, status, blame")(_sub_dispatch("git"))
recipe("gh", "after", matches=_sub_matches("gh"), when=_sub_when("gh"), full=True,
       help="gh pr list, gh pr checks, gh run list")(_sub_dispatch("gh"))
