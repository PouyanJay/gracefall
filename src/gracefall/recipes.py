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

R = "\x1b[0m"
D = SGR["dim"]

# Chart width in cells. Wide enough to read, narrow enough to leave the
# label and the number on the same line in an 80-column terminal.
W = 30

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


def _git_log_matches(argv):
    return bool(argv) and argv[0] == "log"


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
        width = max(8, min(width, cols - max(lw, len(label)) - len(tail) - 4))
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
            lines.append(f"{D}{'lines per commit':<{lw}}{R}  "
                         + dist([min(n, cap) for n in sizes], bins=min(26, mw),
                                lo=0, hi=cap, color="amber")
                         + f"  {D}median {median}, largest {sizes[-1]}{R}")
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


@recipe("git", "after", matches=_git_log_matches, when='"$1" == log', full=True,
        help="git log: a spark of commits per day over the last eight weeks; "
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
    if not full:
        return _git_activity_line(commits, window, bounded, 0)
    # The diff stats are a second, slower query with its own timeout, so a
    # large repository still gets the first four sections when this one
    # takes too long.
    stat = _run(cmd[:2] + ["--format=%x01%at", "--numstat"]
                + cmd[3:] + filters, timeout=20)
    numstat = parse_git_numstat(stat) if stat else None
    cols = shutil.get_terminal_size((80, 24)).columns
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
        cols = shutil.get_terminal_size((80, 24)).columns
        return df_panel(rows, cols=cols)
    rows = parse_df(out)
    if not rows:
        return None
    rows.sort(key=lambda r: _df_frac(r[1], r[2], r[3]), reverse=True)
    lw = _label_width([m for m, _, _, _ in rows])
    lines = []
    for mount, used, total, avail in rows[:8]:
        frac = _df_frac(used, total, avail)
        lines.append(f"{D}{_fit(mount, lw):<{lw}}{R}  "
                     + meter(frac, width=W, color=_fill_color(frac))
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


@recipe("du", "after",
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
# watch: redraw an "after" recipe in place


def watch(draw, every=2.0, emit=True, out=None, ticks=None):
    """Call `draw()` every `every` seconds and repaint its text in place
    until ctrl-c. Each repaint moves the cursor back up over the previous
    frame and clears from there down, so a frame with fewer lines leaves
    nothing behind. `ticks` bounds the loop, for tests."""
    out = sys.stdout if out is None else out
    prev = 0
    n = 0
    try:
        while ticks is None or n < ticks:
            text = draw() or f"{D}nothing to draw{R}"
            if not emit:
                text = strip_spans(text)
            text += f"\n{D}every {every:g}s, ctrl-c to stop{R}"
            up = f"\x1b[{prev}A" if prev else ""
            out.write(up + "\r\x1b[J" + text + "\n")
            out.flush()
            prev = text.count("\n") + 1
            n += 1
            if ticks is None or n < ticks:
                time.sleep(every)
    except KeyboardInterrupt:
        out.write("\n")
        out.flush()
    return 0


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
