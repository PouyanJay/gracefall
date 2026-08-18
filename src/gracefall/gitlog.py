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

import re

from . import ROLE_RGB, SGR, lanes, meter, strip_spans
from .recipes import (_run, git_dashboard, git_query_args, git_time_bounds,
                      git_window, _RENAME)

R = "\x1b[0m"
D = SGR["dim"]
BOLD = "\x1b[1m"
FORMAT = "--format=%x01%h%x09%at%x09%an%x09%P%x09%D%x09%s"
DEFAULT_PAGER = "less -rFX"

# The graph view. git draws the lanes (its graph algorithm has seen every
# shape a history can take); we give it the role palette for the lane
# colours and own the columns to the right of the graph.
GRAPH_FORMAT = "--format=%x01%h%x02%at%x02%an%x02%P%x02%D%x02%s"
GRAPH_COLORS = "log.graphColors=" + ",".join(
    "#%02x%02x%02x" % ROLE_RGB[r] for r in ("teal", "blue", "amber", "coral", "violet"))
GRAPH_LIMIT = 300
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
# git's graph characters as lanes cells (SPEC.md): the commit kinds are
# decided per line from the parent count.
_KINDS = {"|": "b", "/": "l", "\\": "r", "_": "h", "-": "h", " ": "."}
_RGB_ROLE = {"%d;%d;%d" % rgb: role for role, rgb in ROLE_RGB.items()}


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


def _age(ts, now):
    """`3h`, `2d`, `5w`, `4mo`, `1y`: the compact age of a commit."""
    d = max(0, int(now - ts))
    for unit, span in (("s", 60), ("m", 3600), ("h", 86400), ("d", 7 * 86400),
                       ("w", 35 * 86400), ("mo", 365 * 86400)):
        if d < span:
            per = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400,
                   "mo": 30 * 86400}[unit]
            return f"{max(1, d // per) if unit != 's' else 0}{unit}" if unit != "s" else "now"
    return f"{d // (365 * 86400)}y"


def graph_cells(graph, merge=False):
    """git's coloured ASCII lanes for one line as lanes cells: a list of
    (kind, role) with role None where git left the lane uncoloured. The
    commit character becomes `d`, or `m` for a merge; its role is filled
    in later from the rows around it, because git does not colour it."""
    cells = []
    role = None
    pos = 0
    for m in _SGR_RE.finditer(graph):
        for ch in graph[pos:m.start()]:
            cells.append(_cell(ch, role, merge))
        code = m.group(1)
        role = _RGB_ROLE.get(code[5:]) if code.startswith("38;2;") else None
        pos = m.end()
    for ch in graph[pos:]:
        cells.append(_cell(ch, role, merge))
    while cells and cells[-1][0] == ".":
        cells.pop()
    return cells


def _cell(ch, role, merge):
    if ch == "*":
        return ("m" if merge else "d", None)
    return (_KINDS.get(ch, "."), role if ch != " " else None)


def parse_graph(text):
    """Parse `git log --graph --color=always GRAPH_FORMAT` into one entry
    per output line: dict(cells, commit) where `cells` is the row's lanes
    and `commit` a dict for a commit line or None for a line that only
    carries lanes (the `|\\` under a merge). Commit cells then take the
    colour of their lane from the nearest coloured lane cell in the same
    column above or below, and any lane git left uncoloured (a single
    lane, which git does not colour) is teal, the first lane colour. Pure."""
    entries = []
    for line in text.splitlines():
        graph, sep, rest = line.partition("\x01")
        commit = None
        if sep:
            parts = rest.split("\x02", 5)
            if len(parts) == 6 and parts[1].isdigit():
                h, ts, author, parents, refs, subject = parts
                commit = dict(hash=h, ts=int(ts), author=author.strip(),
                              merge=len(parents.split()) > 1,
                              refs=_graph_refs(refs), subject=subject)
        cells = graph_cells(graph, merge=bool(commit and commit["merge"]))
        entries.append(dict(cells=cells, commit=commit))
    for i, e in enumerate(entries):
        for c, (kind, role) in enumerate(e["cells"]):
            if kind in ("d", "m") and role is None:
                e["cells"][c] = (kind, _lane_role(entries, i, c))
    return entries


def _lane_role(entries, i, c):
    """The colour of the lane through column c at row i, from the nearest
    coloured lane cell in that column above or below."""
    for step in (1, -1, 2, -2, 3, -3):
        j = i + step
        if 0 <= j < len(entries) and c < len(entries[j]["cells"]):
            kind, role = entries[j]["cells"][c]
            if kind in ("b", "d", "m") and role:
                return role
    return None


def _graph_refs(decoration):
    """`HEAD -> main, origin/main, tag: v1` to [(name, kind)] with kind in
    head, branch, remote, tag. In a graph of every branch the remote refs
    matter, so unlike the listing they are kept."""
    out = []
    for ref in decoration.split(", "):
        ref = ref.strip()
        if not ref:
            continue
        kind = "branch"
        if " -> " in ref:
            ref, kind = ref.split(" -> ", 1)[1], "head"
        elif ref == "HEAD":
            kind = "head"
        elif ref.startswith("tag: "):
            ref, kind = ref[5:], "tag"
        elif "/" in ref:
            kind = "remote"
        out.append((ref, kind))
    return out


_REF_STYLE = {"head": "\x1b[1m" + SGR["teal"], "branch": SGR["teal"],
              "remote": SGR["dim"], "tag": SGR["violet"]}


def render_graph(entries, cols=80, now=None):
    """The graph page: lanes padded to one column, then hash, refs,
    subject, and the author and age dimmed at the right. Pure apart from
    the clock."""
    now = time.time() if now is None else now
    if not entries:
        return ""
    # One column for every row's lanes, one cell wider than the widest,
    # so a lane leaving to the right at the edge has the cell its curve
    # lands in.
    gw = max(len(e["cells"]) for e in entries) + 1
    commits = [e["commit"] for e in entries if e["commit"]]
    hw = max((len(c["hash"]) for c in commits), default=7)
    show_author = cols >= 90
    aw = 14 if show_author else 0
    # graph, 2, hash, 1, refs and subject, 2, author, 1, age (4)
    fixed = gw + 2 + hw + 1 + 2 + (aw + 1 if show_author else 0) + 4
    sw = max(10, cols - fixed)
    lines = []
    for e in entries:
        cells = e["cells"] + [(".", None)] * (gw - len(e["cells"]))
        graph = lanes(cells)
        pad = ""
        c = e["commit"]
        if c is None:
            lines.append(graph)
            continue
        refs = ""
        rlen = 0
        for name, kind in c["refs"]:
            refs += f"{_REF_STYLE[kind]}{name}{R} "
            rlen += len(name) + 1
        room = sw - rlen
        subject = c["subject"]
        if len(subject) > room:
            subject = subject[:max(1, room - 1)] + "\u2026"
        text = refs + subject
        gap = " " * max(0, sw - rlen - len(subject))
        line = f"{graph}{pad}  {SGR['amber']}{c['hash']:<{hw}}{R} {text}{gap}"
        line += f"  {D}"
        if show_author:
            line += f"{c['author'][:aw]:>{aw}} "
        line += f"{_age(c['ts'], now):>4}{R}"
        lines.append(line)
    return "\n".join(lines)


def _has_revs(filters):
    """True when the user named revisions or ref groups, so --all must
    not be added on top of them."""
    for a in filters:
        if a == "--":
            return False
        if a in ("--all", "--branches", "--tags", "--remotes"):
            return True
        if not a.startswith("-") and not os.path.exists(a):
            return True
    return False


def build_graph(argv, cols=80, now=None):
    """Run git's graph for the user's arguments and return the page, or
    None when git is missing or the query fails."""
    if not shutil.which("git"):
        return None
    filters, bounded, _ = git_query_args(argv)
    cmd = ["git", "-c", GRAPH_COLORS, "log", "--graph", "--color=always", GRAPH_FORMAT]
    if not _has_revs(filters):
        cmd.append("--all")
    if not bounded:
        cmd.append(f"-{GRAPH_LIMIT}")
    out = _run(cmd + filters, timeout=30)
    if out is None:
        return None
    entries = parse_graph(out)
    if not any(e["commit"] for e in entries):
        return f"{D}no commits{R}"
    parts = [render_graph(entries, cols=cols - 3, now=now)]
    if not bounded:
        parts.append("")
        parts.append(f"{D}{GRAPH_LIMIT} most recent commits on every branch. "
                     f"-n, --since or a range for more{R}")
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
    cols = shutil.get_terminal_size((80, 24)).columns
    if rest[:1] == ["graph"] or (rest[:1] == ["log"] and "--graph" in rest):
        text = build_graph([a for a in rest[1:] if a != "--graph"], cols=cols)
    elif rest[:1] == ["log"]:
        text = build(rest, summary=summary, cols=cols)
    else:
        raise SystemExit("gfl git supports `log` and `graph`, as in: "
                         "gfl git log -20, gfl git graph")
    if text is None:
        return 1
    if not emit_osc:
        text = strip_spans(text)
    return page(text, use_pager=use_pager)
