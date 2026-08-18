"""git recipes beyond `git log`: shortlog, diff, branch, status, blame.

Each is an "after" chart under the command's own output, drawn from a
machine-readable query the recipe makes for itself: `git shortlog -sn`,
`git diff --numstat`, `git for-each-ref`, `git status --porcelain`,
`git blame --line-porcelain`. The parsers are pure functions over that
output. Every chart is meters, sparks and dists, so a terminal drawing
OSC 4700 draws them without knowing anything about git.
"""

import shutil
import time

from . import SGR, meter, spark
from .recipes import W, _label_width, _fit, _run, sub

R = "\x1b[0m"
D = SGR["dim"]
BOLD = "\x1b[1m"


def _cols():
    return shutil.get_terminal_size((80, 24)).columns


def _share(n, total):
    return f"{round(100 * n / total):>3}%" if total else "   "


def _age(ts, now=None):
    d = max(0, int((time.time() if now is None else now) - ts))
    for unit, per, span in (("m", 60, 3600), ("h", 3600, 86400), ("d", 86400, 7 * 86400),
                            ("w", 7 * 86400, 35 * 86400), ("mo", 30 * 86400, 365 * 86400)):
        if d < span:
            return f"{max(1, d // per)}{unit}"
    return f"{d // (365 * 86400)}y"


# --------------------------------------------------------------------------
# git shortlog: one meter per author


def parse_shortlog(text):
    """`git shortlog -sn` lines, `   30\\tName`, as [(count, name)]."""
    rows = []
    for line in text.splitlines():
        n, _, name = line.strip().partition("\t")
        if n.isdigit() and name:
            rows.append((int(n), name.strip()))
    return rows


def _shortlog_args(argv):
    """The user's shortlog arguments with the summary flags we add
    ourselves removed, and HEAD supplied when no revision is named: with
    stdin not a terminal, `git shortlog` would otherwise read a log from
    it and print nothing."""
    args = [a for a in argv if a not in ("-s", "-n", "--summary", "--numbered", "-sn", "-ns")]
    named = any(not a.startswith("-") for a in (args[:args.index("--")] if "--" in args else args))
    if not named:
        args.insert(args.index("--") if "--" in args else len(args), "HEAD")
    return args


def shortlog_chart(rows, cols=None, full=False):
    """Meters per author, most commits first, scaled to the busiest, with
    the count and share of the total after each. Pure."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (-r[0], r[1]))
    total = sum(n for n, _ in rows)
    mx = rows[0][0] or 1
    top = rows if full else rows[:10]
    lw = _label_width([name for _, name in top], cap=24)
    mw = W if not cols else max(10, min(W, cols - lw - 2 - 2 - 12))
    lines = []
    for n, name in top:
        lines.append(f"{D}{_fit(name, lw):<{lw}}{R}  "
                     + meter(n / mx, width=mw, color="blue")
                     + f"  {n:<5}{D}{_share(n, total)}{R}")
    if len(rows) > len(top):
        rest = sum(n for n, _ in rows[len(top):])
        lines.append(f"{D}{'+ ' + str(len(rows) - len(top)) + ' more':<{lw}}{R}  "
                     + meter(rest / mx, width=mw, color="dim")
                     + f"  {rest:<5}{D}{_share(rest, total)}{R}")
    lines.append(f"{D}{'':<{lw}}  {total} commits, {len(rows)} authors{R}")
    return "\n".join(lines)


@sub("git", "shortlog", help="one meter per author, most commits first")
def git_shortlog(argv, full=False):
    if not shutil.which("git"):
        return None
    out = _run(["git", "shortlog", "-s", "-n"] + _shortlog_args(argv))
    if not out:
        return None
    return shortlog_chart(parse_shortlog(out), cols=_cols(), full=full)


# --------------------------------------------------------------------------
# git diff: added against removed, per file and in total

_DIFF_DISPLAY = ("--stat", "--shortstat", "--numstat", "--name-only", "--name-status",
                 "--compact-summary", "--summary", "--dirstat", "--cumulative",
                 "-p", "-u", "--patch", "--no-patch", "-s", "--color", "--no-color",
                 "--word-diff", "--color-words", "--raw", "--patch-with-stat",
                 "--patch-with-raw", "--ext-diff", "--no-ext-diff", "--textconv",
                 "--no-textconv", "-z")


def parse_numstat(text):
    """`git diff --numstat` lines as [(added, deleted, path)], with None
    for a binary file's counts."""
    rows = []
    for line in text.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        a, d, path = parts
        rows.append((int(a) if a.isdigit() else None,
                     int(d) if d.isdigit() else None, path))
    return rows


def diff_chart(rows, cols=None, full=False):
    """Per file, two meters on one scale: lines added (teal) and removed
    (coral), largest change first, then a total row whose meter is the
    added share of everything changed. Pure."""
    if not rows:
        return None
    def churn(r):
        return (r[0] or 0) + (r[1] or 0)
    rows = sorted(rows, key=lambda r: (-churn(r), r[2]))
    top = rows if full else rows[:12]
    mx = max(max(r[0] or 0, r[1] or 0) for r in rows) or 1
    lw = _label_width([r[2] for r in top],
                      cap=32 if not cols or cols >= 110 else 22 if cols >= 80 else 16)
    # label, meter +NNNNN, meter -NNNNN
    mw = 12 if not cols else max(6, min(15, (cols - lw - 2 - 8 - 2 - 8) // 2))
    lines = []
    for a, d, path in top:
        if a is None:
            lines.append(f"{D}{_fit(path, lw):<{lw}}  {'binary':<{2 * mw + 18}}{R}")
            continue
        lines.append(f"{D}{_fit(path, lw):<{lw}}{R}  "
                     + meter(a / mx, width=mw, color="teal") + f" {SGR['teal']}+{a:<6}{R}"
                     + meter(d / mx, width=mw, color="coral") + f" {SGR['coral']}-{d:<6}{R}")
    if len(rows) > len(top):
        lines.append(f"{D}{'+ ' + str(len(rows) - len(top)) + ' more files':<{lw}}{R}")
    added = sum(r[0] or 0 for r in rows)
    removed = sum(r[1] or 0 for r in rows)
    total = added + removed
    tw = 2 * mw + 8 if not cols else max(8, min(2 * mw + 8, cols - lw - 2 - 2 - 36))
    lines.append(f"{D}{'total':<{lw}}{R}  "
                 + meter(added / total if total else 0, width=tw, color="teal")
                 + f"  {D}{len(rows)} file{'s' if len(rows) != 1 else ''}, "
                 f"{R}{SGR['teal']}+{added}{R} {SGR['coral']}-{removed}{R}"
                 + (f" {D}({round(100 * added / total)}% added){R}" if total else ""))
    return "\n".join(lines)


@sub("git", "diff", help="added against removed per file, and in total")
def git_diff(argv, full=False):
    if not shutil.which("git"):
        return None
    args = [a for a in argv if not a.split("=", 1)[0] in _DIFF_DISPLAY
            and not a.startswith(("--stat-", "--dirstat", "--word-diff", "-U", "--unified"))]
    out = _run(["git", "diff", "--numstat"] + args, timeout=20)
    if not out:
        return None
    return diff_chart(parse_numstat(out), cols=_cols(), full=full)


# --------------------------------------------------------------------------
# git branch: ahead and behind the upstream, per branch

_REF_FORMAT = ("--format=%(refname:short)%09%(upstream:short)%09"
               "%(upstream:track,nobracket)%09%(committerdate:unix)%09%(HEAD)")


def parse_refs(text):
    """for-each-ref rows as dicts: name, upstream, ahead, behind, gone,
    ts, head (True on the checked-out branch)."""
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        name, upstream, track, ts, head = parts
        ahead = behind = 0
        gone = track.strip() == "gone"
        for tok in track.split(","):
            k, _, v = tok.strip().partition(" ")
            if k == "ahead" and v.isdigit():
                ahead = int(v)
            elif k == "behind" and v.isdigit():
                behind = int(v)
        rows.append(dict(name=name, upstream=upstream, ahead=ahead, behind=behind,
                         gone=gone, ts=int(ts) if ts.isdigit() else 0,
                         head=head.strip() == "*"))
    return rows


def _branch_lists_only(rest):
    """Only the listing forms of `git branch` earn a chart; anything that
    creates, deletes, renames or repoints a branch is left alone."""
    mutating = ("-d", "-D", "-m", "-M", "-c", "-C", "-u", "-f", "--delete", "--move",
                "--copy", "--force", "--set-upstream-to", "--unset-upstream",
                "--edit-description", "--track", "--no-track")
    for a in rest:
        if not a.startswith("-"):
            return False
        if a.split("=", 1)[0] in mutating:
            return False
    return True


def branch_chart(rows, cols=None, full=False, now=None):
    """One line per branch, most recently committed first: ahead and behind
    its upstream as two meters on one scale, the age of its tip, and the
    upstream name. The checked-out branch is bold. Pure."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (-r["ts"], r["name"]))
    top = rows if full else rows[:15]
    mx = max(max(r["ahead"], r["behind"]) for r in rows) or 1
    wide = cols is None
    lw = _label_width([r["name"] for r in top],
                      cap=28 if wide or cols >= 100 else 20 if cols >= 80 else 16)
    show_upstream = wide or cols >= 90
    # label, "ahead" meter n "behind" meter n, age, and the upstream name
    # on a wide terminal; the two meters take what is left
    mw = 10 if wide else max(6, min(12, (cols - lw - 2 - 22 - 5 - (22 if show_upstream else 0)) // 2))
    lines = []
    for r in top:
        name = _fit(r["name"], lw)
        label = f"{BOLD}{name:<{lw}}{R}" if r["head"] else f"{D}{name:<{lw}}{R}"
        if r["upstream"] and not r["gone"]:
            body = (f"{D}ahead{R} " + meter(r["ahead"] / mx, width=mw, color="teal")
                    + f" {r['ahead']:<3} {D}behind{R} "
                    + meter(r["behind"] / mx, width=mw, color="amber") + f" {r['behind']:<3}")
        else:
            body = f"{D}{('upstream gone' if r['gone'] else 'no upstream'):<{2 * mw + 22}}{R}"
        lines.append(f"{label}  {body}  {D}{_age(r['ts'], now):>3}"
                     + (f"  {r['upstream'][:20]}" if r["upstream"] and show_upstream else "") + R)
    if len(rows) > len(top):
        lines.append(f"{D}{'+ ' + str(len(rows) - len(top)) + ' more':<{lw}}{R}")
    return "\n".join(lines)


@sub("git", "branch", matches=_branch_lists_only,
     help="ahead and behind the upstream, per branch (listing forms only)")
def git_branch(argv, full=False):
    if not shutil.which("git"):
        return None
    refs = ["refs/heads"]
    if "-r" in argv or "--remotes" in argv:
        refs = ["refs/remotes"]
    elif "-a" in argv or "--all" in argv:
        refs = ["refs/heads", "refs/remotes"]
    out = _run(["git", "for-each-ref", _REF_FORMAT] + refs)
    if not out:
        return None
    return branch_chart(parse_refs(out), cols=_cols(), full=full)


# --------------------------------------------------------------------------
# git status: the branch against its upstream, and the working tree


def parse_status(text):
    """`git status --porcelain=v1 -b` as dict: branch, upstream, ahead,
    behind, staged, unstaged, untracked, conflicts."""
    st = dict(branch="", upstream="", ahead=0, behind=0,
              staged=0, unstaged=0, untracked=0, conflicts=0)
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:]
            track = ""
            if " [" in head and head.endswith("]"):
                head, track = head[:-1].split(" [", 1)
            if "..." in head:
                st["branch"], st["upstream"] = head.split("...", 1)
            else:
                st["branch"] = head
            for tok in track.split(","):
                k, _, v = tok.strip().partition(" ")
                if k == "ahead" and v.isdigit():
                    st["ahead"] = int(v)
                elif k == "behind" and v.isdigit():
                    st["behind"] = int(v)
                elif k == "gone":
                    st["upstream"] = ""
            continue
        if len(line) < 3:
            continue
        x, y = line[0], line[1]
        if x == "?" and y == "?":
            st["untracked"] += 1
            continue
        if x == "!" and y == "!":
            continue
        if x == "U" or y == "U" or (x == y and x in "AD"):
            st["conflicts"] += 1
            continue
        if x not in " ?!":
            st["staged"] += 1
        if y not in " ?!":
            st["unstaged"] += 1
    return st


def status_chart(st, cols=None):
    """Two lines: the branch, ahead and behind its upstream as meters, then
    the working tree as one meter per kind of change. Pure."""
    lw = len("working tree")
    ab = max(st["ahead"], st["behind"]) or 1
    if st["upstream"]:
        top = (f"{D}ahead{R} " + meter(st["ahead"] / ab, width=10, color="teal")
               + f" {st['ahead']:<3} {D}behind{R} "
               + meter(st["behind"] / ab, width=10, color="amber") + f" {st['behind']:<3}"
               + f"  {D}{st['upstream']}{R}")
    else:
        top = f"{D}no upstream{R}"
    lines = [f"{BOLD}{_fit(st['branch'] or 'HEAD', lw):<{lw}}{R}  {top}"]
    kinds = [("staged", st["staged"], "teal"), ("unstaged", st["unstaged"], "amber"),
             ("untracked", st["untracked"], "dim"), ("conflicts", st["conflicts"], "coral")]
    total = sum(n for _, n, _ in kinds)
    if total == 0:
        lines.append(f"{D}{'working tree':<{lw}}  clean{R}")
        return "\n".join(lines)
    mx = max(n for _, n, _ in kinds) or 1
    parts = []
    for name, n, color in kinds:
        if n or name in ("staged", "unstaged"):
            parts.append(f"{D}{name}{R} " + meter(n / mx, width=8, color=color) + f" {n:<3}")
    lines.append(f"{D}{'working tree':<{lw}}{R}  " + " ".join(parts))
    return "\n".join(lines)


@sub("git", "status", help="the branch against its upstream, and the working tree")
def git_status(argv, full=False):
    if not shutil.which("git"):
        return None
    out = _run(["git", "status", "--porcelain=v1", "-b"] + [a for a in argv if not a.startswith("-")])
    if out is None:
        return None
    return status_chart(parse_status(out), cols=_cols())


# --------------------------------------------------------------------------
# git blame: who owns the lines


def parse_blame_authors(text):
    """Line ownership from `git blame --line-porcelain`: [(lines, author)],
    most lines first. Only header lines start with `author `; content
    lines are tab-indented."""
    counts = {}
    for line in text.splitlines():
        if line.startswith("author "):
            a = line[7:].strip()
            counts[a] = counts.get(a, 0) + 1
    return sorted(((n, a) for a, n in counts.items()), key=lambda r: (-r[0], r[1]))


def blame_chart(rows, cols=None, full=False):
    """Meters per author, scaled to the whole file, with lines and share.
    Pure."""
    if not rows:
        return None
    total = sum(n for n, _ in rows)
    top = rows if full else rows[:8]
    lw = _label_width([a for _, a in top], cap=24)
    mw = W if not cols else max(10, min(W, cols - lw - 2 - 2 - 14))
    lines = []
    for n, a in top:
        lines.append(f"{D}{_fit(a, lw):<{lw}}{R}  "
                     + meter(n / total, width=mw, color="violet")
                     + f"  {n:<6}{D}{_share(n, total)}{R}")
    if len(rows) > len(top):
        rest = sum(n for n, _ in rows[len(top):])
        lines.append(f"{D}{'+ ' + str(len(rows) - len(top)) + ' more':<{lw}}{R}  "
                     + meter(rest / total, width=mw, color="dim")
                     + f"  {rest:<6}{D}{_share(rest, total)}{R}")
    lines.append(f"{D}{'':<{lw}}  {total} lines, {len(rows)} authors{R}")
    return "\n".join(lines)


@sub("git", "blame", matches=lambda rest: any(not a.startswith("-") for a in rest),
     help="line ownership: one meter per author")
def git_blame(argv, full=False):
    if not shutil.which("git"):
        return None
    args = [a for a in argv if a not in ("-p", "--porcelain", "--line-porcelain",
                                          "--incremental", "-s", "-e", "-f", "-n", "-c")]
    out = _run(["git", "blame", "--line-porcelain"] + args, timeout=30)
    if not out:
        return None
    return blame_chart(parse_blame_authors(out), cols=_cols(), full=full)


# --------------------------------------------------------------------------
# git log --stat: churn per commit over time, added to the log recipe

def churn_line(numstat, cols=None):
    """A spark of lines changed per commit in time order, from
    parse_git_numstat's rows (newest first). Pure."""
    if not numstat:
        return None
    sizes = [n for _, n, _ in reversed(numstat)]
    total = sum(sizes)
    width = min(len(sizes), 56)
    if cols:
        width = max(8, min(width, cols - 22 - 30))
    return (f"{D}{'lines per commit':<21}{R}  "
            + spark(sizes, lo=0, width=width, color="amber")
            + f"  {D}{total} over {len(sizes)} commits, largest {max(sizes)}{R}")


__all__ = ["parse_shortlog", "shortlog_chart", "parse_numstat", "diff_chart",
           "parse_refs", "branch_chart", "parse_status", "status_chart",
           "parse_blame_authors", "blame_chart", "churn_line"]
