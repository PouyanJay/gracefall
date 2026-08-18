"""`gfl git log`: history as a reading format.

The recipe under `git log` adds a chart and leaves the log alone. This is
the other view, chosen on purpose when the question is "what happened"
rather than "which commit": the same summary on top, then commits grouped
under day headers, one line each with a size meter, so the big commits
and the busy days stand out before a word is read.

Navigation is the pager. Output goes through `less -rFX` (or $GFL_PAGER)
when stdout is a terminal, so `/`, `n`, `g`, `G` and `q` work as they do
under `git log`. `-r` rather than `-R` because less -R strips the OSC
envelopes and prints their attributes as text, while -r passes them
through; the terminal decides what to do with them, as everywhere else.
Piped, the output is plain text.

Same argument grammar as `git log`: `-20`, `--since`, `--author`,
`--grep`, `--no-merges`, revision ranges and pathspecs narrow the list.
Patches are not this view's job; `git log -p` and `delta` own those.
"""

import os
import shlex
import shutil
import subprocess
import sys
import time

from . import SGR, meter, strip_spans
from .recipes import (_run, git_dashboard, git_query_args, git_time_bounds,
                      git_window, _RENAME)

R = "\x1b[0m"
D = SGR["dim"]
BOLD = "\x1b[1m"
FORMAT = "--format=%x01%h%x09%at%x09%an%x09%P%x09%D%x09%s"
DEFAULT_PAGER = "less -rFX"


def parse_listing(text):
    """Parse `git log FORMAT --numstat` into one dict per commit: hash,
    ts, author, merge, refs, subject, add, rm, files, paths (top-level
    path to lines changed). Pure."""
    commits = []
    for line in text.splitlines():
        if line.startswith("\x01"):
            parts = line[1:].split("\t", 5)
            if len(parts) != 6 or not parts[1].isdigit():
                continue
            h, ts, author, parents, refs, subject = parts
            commits.append(dict(hash=h, ts=int(ts), author=author.strip(),
                                merge=len(parents.split()) > 1,
                                refs=_refs(refs), subject=subject,
                                add=0, rm=0, files=0, paths={}))
            continue
        if not commits:
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        c = commits[-1]
        c["files"] += 1
        c["add"] += int(added) if added.isdigit() else 0
        c["rm"] += int(deleted) if deleted.isdigit() else 0
        path = _RENAME.sub(r"\1", path)
        if " => " in path:
            path = path.split(" => ", 1)[1]
        top = path.split("/", 1)[0] if "/" in path else path
        n = (int(added) if added.isdigit() else 0) + \
            (int(deleted) if deleted.isdigit() else 0)
        c["paths"][top] = c["paths"].get(top, 0) + n
    return commits


def _refs(decoration):
    """`HEAD -> main, tag: v0.5.0, origin/main` to ['main', 'v0.5.0'].
    Remote-tracking refs are dropped: they say where a branch was fetched
    from, not what the commit is."""
    out = []
    for ref in decoration.split(", "):
        ref = ref.strip()
        if not ref:
            continue
        if " -> " in ref:
            ref = ref.split(" -> ", 1)[1]
        if ref.startswith("tag: "):
            ref = ref[5:]
        if ref == "HEAD" or "/" in ref:
            continue
        if ref not in out:
            out.append(ref)
    return out


def _day(ts, year):
    return time.strftime("%a %b %-d %Y" if year else "%a %b %-d",
                         time.localtime(ts))


def render_listing(commits, cols=80, now=None):
    """The commit lines with day headers, as text. `commits` is newest
    first, as git gives it. Pure apart from the clock."""
    now = time.time() if now is None else now
    this_year = time.localtime(now).tm_year
    mx = max((c["add"] + c["rm"] for c in commits), default=0) or 1
    many_authors = len({c["author"] for c in commits}) > 1 and cols >= 90
    show_files = cols >= 100
    show_time = cols >= 70
    hw = max(len(c["hash"]) for c in commits)     # git widens this as a repo grows
    # 2 indent, hash, 2, 8 meter, 2, subject, 2, +NNNNN -NNNNN, then the
    # time, NNN files and the author as the terminal gets wider (the
    # author only when there is more than one). What is left is the
    # subject's, never less than 20 cells.
    fixed = (2 + hw + 2 + 8 + 2 + 2 + 13 + (2 + 5 if show_time else 0)
             + (2 + 9 if show_files else 0) + (2 + 14 if many_authors else 0))
    sw = max(20, cols - fixed)
    lines = []
    day = None
    for c in commits:
        t = time.localtime(c["ts"])
        d = _day(c["ts"], t.tm_year != this_year)
        if d != day:
            day = d
            same = [x for x in commits if _day(x["ts"], t.tm_year != this_year) == d]
            add = sum(x["add"] for x in same)
            rm = sum(x["rm"] for x in same)
            n = len(same)
            lines.append("")
            lines.append(f"{BOLD}{d}{R}  {D}{n} commit{'s' if n != 1 else ''}, "
                         f"+{add} -{rm}{R}")
        # Subject on the left, refs (tags, local branches) right-aligned
        # at the end of the same column, so the landmarks line up.
        subject = c["subject"]
        refs = " ".join(c["refs"])[:sw // 2]
        room = sw - (len(refs) + 2 if refs else 0)
        if len(subject) > room:
            subject = subject[:max(1, room - 1)] + "…"
        text = f"{subject:<{room}}" + (f"  {SGR['violet']}{refs}{R}" if refs else "")
        if c["merge"]:
            # A merge's own diff is empty in --numstat; the work is in the
            # commits it brings in, listed below it. Say so, show nothing.
            size = f"{D}{'merge':<8}{R}"
            stat = " " * 13
            files = " " * 9
        else:
            size = meter((c["add"] + c["rm"]) / mx, width=8, color="teal")
            stat = f"+{c['add']:<5} -{c['rm']:<5}"
            files = f"{c['files']:>3} file{'s' if c['files'] != 1 else ' '}"
        clock = f"{time.strftime('%H:%M', t)}  " if show_time else ""
        line = (f"  {SGR['amber']}{c['hash']:<{hw}}{R}  {size}  {text}  "
                f"{D}{clock}{stat}{R}")
        if show_files:
            line += f"  {D}{files}{R}"
        if many_authors:
            line += f"  {D}{c['author'][:14]}{R}"
        lines.append(line)
    return "\n".join(lines[1:]) if lines else ""


def build(argv, summary=True, cols=80, now=None):
    """Run the query for the user's `git log` arguments and return the
    whole page as text, or None when git is missing or the query fails.
    An empty history returns a one-line note, never nothing."""
    if not shutil.which("git"):
        return None
    filters, bounded, timed = git_query_args(argv)
    cmd = ["git", "log", FORMAT, "--numstat"]
    if not bounded:
        cmd.append("--since=8.weeks")
    out = _run(cmd + filters, timeout=30)
    if out is None:
        return None
    commits = parse_listing(out)
    if not commits:
        return (f"{D}no commits in the last 8 weeks. "
                f"gfl git log --since=1.year, -50, or a range for more{R}"
                if not bounded else f"{D}no commits{R}")
    parts = []
    if summary:
        pairs = [(c["ts"], c["author"]) for c in commits]
        numstat = [(c["ts"], c["add"] + c["rm"], c["paths"]) for c in commits]
        window = git_window(pairs, bounded, *git_time_bounds(timed), now=now)
        dash = git_dashboard(pairs, numstat, window, bounded, cols=cols - 3)
        if dash:
            parts.append(dash)
            parts.append("")
    parts.append(render_listing(commits, cols=cols - 3, now=now))
    if not bounded:
        parts.append("")
        parts.append(f"{D}last 8 weeks. --since, -n or a range for more{R}")
    # A blank line above and below, and a two-cell margin on the left, so
    # the page does not start hard against the prompt and the edge.
    body = "\n".join(("  " + l if l else l) for l in "\n".join(parts).split("\n"))
    return "\n" + body + "\n"


def _pager():
    """The pager command as argv, or None to write straight out."""
    p = os.environ.get("GFL_PAGER")
    if p is None:
        p = DEFAULT_PAGER if shutil.which("less") else ""
    p = p.strip()
    if not p or p == "cat":
        return None
    return shlex.split(p)


def page(text, use_pager=True):
    """Write text through the pager when stdout is a terminal, plain
    otherwise. Never lets a pager the user quit early raise."""
    if use_pager and sys.stdout.isatty():
        argv = _pager()
        if argv:
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE)
            except OSError:
                proc = None
            if proc is not None:
                try:
                    proc.stdin.write(text.encode("utf-8", "replace") + b"\n")
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                proc.wait()
                return 0
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return 0


def main(args, emit_osc):
    """`gfl git ...`: dispatch on the git subcommand. Only `log` today."""
    args = list(args)
    if args[:1] == ["--"]:
        args = args[1:]
    summary = True
    use_pager = True
    rest = []
    for a in args:
        if a == "--no-summary":
            summary = False
        elif a == "--no-pager":
            use_pager = False
        else:
            rest.append(a)
    if rest[:1] != ["log"]:
        raise SystemExit("gfl git supports `log`, as in: gfl git log -20")
    cols = shutil.get_terminal_size((80, 24)).columns
    text = build(rest, summary=summary, cols=cols)
    if text is None:
        return 1
    if not emit_osc:
        text = strip_spans(text)
    return page(text, use_pager=use_pager)
