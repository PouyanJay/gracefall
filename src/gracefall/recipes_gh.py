"""gh recipes: `gh pr list`, `gh pr checks`, `gh run list`.

Each is an "after" chart under gh's own table, from a `--json` query the
recipe makes for itself, so the parsers read a stable shape rather than
gh's human output. Network again, on gh's own credentials; when gh is
missing, not logged in, or slow, the query fails and the chart is silent,
which is the recipe rule.

Charts: pull requests get a meter of their checks and a dist of how long
they have been open; checks get a flow of the pipeline and a meter of
passed against all; runs get a success-rate meter per workflow and a
spark of run durations in time order.
"""

import json
import shutil
import time

from . import SGR, dist, flow, meter, spark
from .recipes import W, _label_width, _fit, _run, sub

R = "\x1b[0m"
D = SGR["dim"]

_PASS = ("SUCCESS", "NEUTRAL", "SKIPPED")
_FAIL = ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")


def _cols():
    return shutil.get_terminal_size((80, 24)).columns


def _iso(ts):
    """Unix time from gh's ISO-8601 timestamps, or None."""
    if not ts:
        return None
    try:
        t = time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return int(time.mktime(t) - time.timezone if ts.endswith("Z") else time.mktime(t))


def _passthrough(argv, valued, flags):
    """Keep the user's filters (which PRs, which runs) and drop everything
    that changes gh's own output shape."""
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        name = a.split("=", 1)[0]
        if name in valued:
            out.append(a)
            if "=" not in a and i + 1 < len(argv):
                i += 1
                out.append(argv[i])
        elif name in flags:
            out.append(a)
        i += 1
    return out


def _age(seconds):
    d = max(0, int(seconds))
    for unit, per, span in (("m", 60, 3600), ("h", 3600, 86400), ("d", 86400, 7 * 86400),
                            ("w", 7 * 86400, 35 * 86400), ("mo", 30 * 86400, 365 * 86400)):
        if d < span:
            return f"{max(1, d // per)}{unit}"
    return f"{d // (365 * 86400)}y"


# --------------------------------------------------------------------------
# gh pr list


def parse_prs(text, now=None):
    """`gh pr list --json ...` into [(number, title, age_seconds, draft,
    review, passed, failed, pending)]. Checks come from statusCheckRollup,
    which mixes CheckRun (conclusion/status) and StatusContext (state)."""
    now = time.time() if now is None else now
    try:
        items = json.loads(text)
    except ValueError:
        return []
    rows = []
    for it in items if isinstance(items, list) else []:
        passed = failed = pending = 0
        for c in it.get("statusCheckRollup") or []:
            verdict = (c.get("conclusion") or c.get("state") or "").upper()
            status = (c.get("status") or "").upper()
            if verdict in _PASS:
                passed += 1
            elif verdict in _FAIL:
                failed += 1
            elif status and status != "COMPLETED" or not verdict:
                pending += 1
            else:
                pending += 1
        created = _iso(it.get("createdAt"))
        rows.append((it.get("number", 0), it.get("title", ""),
                     now - created if created else 0, bool(it.get("isDraft")),
                     (it.get("reviewDecision") or "").lower().replace("_", " "),
                     passed, failed, pending))
    return rows


def prs_chart(rows, cols=None, full=False):
    """One line per pull request: a meter of its checks (teal when all
    pass, coral when any failed, amber while some are pending), passed of
    total, age, review state and title; then a dist of how long the open
    ones have been waiting. Pure."""
    if not rows:
        return None
    top = rows if full else rows[:15]
    nw = max(len(str(n)) for n, *_ in top) + 1
    mw = 10
    tw = max(16, (cols or 100) - nw - 2 - mw - 1 - 6 - 5 - 20)
    lines = []
    for n, title, age, draft, review, p, f, pend in top:
        total = p + f + pend
        color = "coral" if f else "amber" if pend else "teal"
        frac = p / total if total else 0
        checks = (meter(frac, width=mw, color=color) + f" {p}/{total:<3}") if total \
            else f"{D}{'no checks':<{mw + 5}}{R}"
        state = "draft" if draft else review or ""
        t = title if len(title) <= tw else title[:tw - 1] + "…"
        lines.append(f"{SGR['amber']}#{n:<{nw}}{R} {checks} {D}{_age(age):>3}  "
                     f"{state[:12]:<12}{R} {t}")
    if len(rows) > len(top):
        lines.append(f"{D}+ {len(rows) - len(top)} more{R}")
    ages = [age / 86400 for _, _, age, *_ in rows]
    if len(ages) >= 3:
        cap = max(1.0, sorted(ages)[int(0.9 * (len(ages) - 1))])
        med = sorted(ages)[len(ages) // 2]
        bins = 20 if not cols else max(8, min(20, cols - nw - 2 - 44))
        lines.append(f"{D}{'open for':<{nw + 1}}{R} "
                     + dist([min(a, cap) for a in ages], bins=bins, lo=0, hi=cap,
                            color="blue")
                     + f"  {D}median {med:.0f}d, oldest {max(ages):.0f}d, {len(ages)} open{R}")
    return "\n".join(lines)


@sub("gh", "pr", matches=lambda rest: rest[:1] in (["list"], ["checks"]),
     when='"$2" == list || "$2" == checks',
     help="pr list: a meter of checks and the age per PR; pr checks: the pipeline as a flow")
def gh_pr(argv, full=False):
    if not shutil.which("gh"):
        return None
    if argv[:1] == ["checks"]:
        return _pr_checks(argv[1:], full)
    args = _passthrough(argv[1:], ("-s", "--state", "-A", "--author", "-l", "--label",
                                   "-B", "--base", "-H", "--head", "-S", "--search",
                                   "-L", "--limit", "--app", "-R", "--repo"),
                        ("-d", "--draft", "-w", "--web"))
    if not any(a.split("=", 1)[0] in ("-L", "--limit") for a in args):
        args += ["-L", "30"]
    out = _run(["gh", "pr", "list", "--json",
                "number,title,createdAt,isDraft,reviewDecision,statusCheckRollup"] + args,
               timeout=20)
    if not out:
        return None
    return prs_chart(parse_prs(out), cols=_cols(), full=full)


# --------------------------------------------------------------------------
# gh pr checks


def parse_checks(text):
    """`gh pr checks --json name,bucket,state,startedAt,completedAt` into
    [(name, status)] with status a flow status: done, failed, active
    (running), pending (skipped or cancelled, drawn dim)."""
    try:
        items = json.loads(text)
    except ValueError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        bucket = (it.get("bucket") or "").lower()
        status = {"pass": "done", "fail": "failed", "pending": "active"}.get(bucket, "pending")
        out.append((it.get("name") or it.get("workflow") or "?", status))
    return out


#: Above this many checks the flow shows only the ones that need a look
#: (failed, running); a wall of two hundred passed stages says nothing.
FLOW_CAP = 24


def checks_chart(checks, cols=None):
    """The checks as a flow, wrapped to the terminal width in rows, and a
    meter of passed against all. Past FLOW_CAP checks the flow keeps only
    the failed and running ones and the meter carries the rest. Pure."""
    if not checks:
        return None
    cols = cols or 100
    shown = checks
    if len(checks) > FLOW_CAP:
        shown = [c for c in checks if c[1] in ("failed", "active")][:FLOW_CAP]
    rows, row, width = [], [], 0
    for name, st in shown:
        name = name if len(name) <= 24 else name[:23] + "…"
        cell = len(name) + 2 + (2 if row else 0)
        if row and width + cell > cols - 4:
            rows.append(row)
            row, width = [], 0
        row.append((name, st))
        width += cell
    if row:
        rows.append(row)
    lines = [flow([n for n, _ in r], [s for _, s in r]) for r in rows]
    if len(checks) > FLOW_CAP:
        hidden = len(checks) - len(shown)
        lines.append(f"{D}{'':<8}{len(shown)} shown of {len(checks)}; "
                     f"{hidden} passed or skipped not drawn{R}")
    done = sum(1 for _, s in checks if s == "done")
    failed = sum(1 for _, s in checks if s == "failed")
    running = sum(1 for _, s in checks if s == "active")
    color = "coral" if failed else "amber" if running else "teal"
    tail = f"{done} of {len(checks)} passed"
    if failed:
        tail += f", {failed} failed"
    if running:
        tail += f", {running} running"
    lines.append(f"{D}{'checks':<7}{R} " + meter(done / len(checks), width=W, color=color)
                 + f"  {D}{tail}{R}")
    return "\n".join(lines)


def _pr_checks(rest, full):
    args = _passthrough(rest, ("-R", "--repo"), ()) + [a for a in rest if not a.startswith("-")][:1]
    out = _run(["gh", "pr", "checks", "--json", "name,bucket,state,startedAt,completedAt,workflow"]
               + args, timeout=20)
    if not out:
        return None
    return checks_chart(parse_checks(out), cols=_cols())


# --------------------------------------------------------------------------
# gh run list


def parse_runs(text):
    """`gh run list --json ...` into [(workflow, conclusion, started,
    updated)] newest first, as gh lists them."""
    try:
        items = json.loads(text)
    except ValueError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        out.append((it.get("workflowName") or it.get("name") or "?",
                    (it.get("conclusion") or "").upper(),
                    _iso(it.get("startedAt") or it.get("createdAt")),
                    _iso(it.get("updatedAt"))))
    return out


def runs_chart(runs, cols=None, full=False):
    """Per workflow, a success-rate meter over the runs listed; then the
    duration of every completed run as a spark in time order. Pure."""
    if not runs:
        return None
    by = {}
    for wf, concl, *_ in runs:
        by.setdefault(wf, [0, 0, 0])
        if concl in _PASS:
            by[wf][0] += 1
        elif concl in _FAIL:
            by[wf][1] += 1
        else:
            by[wf][2] += 1
    names = sorted(by, key=lambda k: -sum(by[k]))
    top = names if full else names[:8]
    lw = _label_width(top, cap=28)
    mw = W if not cols else max(10, min(W, cols - lw - 2 - 2 - 30))
    lines = []
    for wf in top:
        ok, bad, other = by[wf]
        done = ok + bad
        frac = ok / done if done else 0
        color = "teal" if frac >= 0.9 else "amber" if frac >= 0.5 else "coral"
        tail = f"{ok}/{done} passed" + (f", {other} running" if other else "")
        lines.append(f"{D}{_fit(wf, lw):<{lw}}{R}  " + meter(frac, width=mw, color=color)
                     + f"  {D}{tail}{R}")
    if len(names) > len(top):
        lines.append(f"{D}{'+ ' + str(len(names) - len(top)) + ' more':<{lw}}{R}")
    durs = [(s, u - s) for _, c, s, u in reversed(runs) if s and u and u >= s and c]
    if len(durs) >= 2:
        d = [x for _, x in durs]
        lines.append(f"{D}{'duration':<{lw}}{R}  "
                     + spark(d, lo=0, width=min(len(d), mw), color="blue")
                     + f"  {D}last {_dur(d[-1])}, median {_dur(sorted(d)[len(d) // 2])}, "
                     f"{len(d)} runs{R}")
    return "\n".join(lines)


def _dur(sec):
    sec = int(sec)
    return f"{sec}s" if sec < 90 else f"{sec // 60}m{sec % 60:02d}s" if sec < 3600 \
        else f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


@sub("gh", "run", matches=lambda rest: rest[:1] == ["list"], when='"$2" == list',
     help="run list: a success-rate meter per workflow and a spark of run durations")
def gh_run(argv, full=False):
    if not shutil.which("gh"):
        return None
    args = _passthrough(argv[1:], ("-w", "--workflow", "-b", "--branch", "-u", "--user",
                                   "-e", "--event", "-s", "--status", "-L", "--limit",
                                   "-c", "--commit", "-R", "--repo"), ("-a", "--all"))
    if not any(a.split("=", 1)[0] in ("-L", "--limit") for a in args):
        args += ["-L", "30"]
    out = _run(["gh", "run", "list", "--json",
                "workflowName,name,conclusion,status,startedAt,updatedAt,createdAt"] + args,
               timeout=20)
    if not out:
        return None
    return runs_chart(parse_runs(out), cols=_cols(), full=full)
