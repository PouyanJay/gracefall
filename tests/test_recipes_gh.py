"""The gh recipes: parsers against captured --json output and the chart
layout. No network: the CLI path is exercised only for silence when gh
is not usable."""

import json
import re

from gracefall import strip_spans
from gracefall.recipes_gh import (FLOW_CAP, _passthrough, checks_chart, parse_checks,
                                  parse_prs, parse_runs, prs_chart, runs_chart)

SGR = re.compile(r"\x1b\[[0-9;]*m")


def plain(s):
    return SGR.sub("", strip_spans(s))


PRS = json.dumps([
    {"number": 185, "title": "Merge fix/overview-videos-placement", "createdAt": "2026-08-15T10:00:00Z",
     "isDraft": False, "reviewDecision": "APPROVED",
     "statusCheckRollup": [
         {"__typename": "CheckRun", "conclusion": "SUCCESS", "status": "COMPLETED"},
         {"__typename": "CheckRun", "conclusion": "FAILURE", "status": "COMPLETED"},
         {"__typename": "StatusContext", "state": "SUCCESS"}]},
    {"number": 184, "title": "Draft thing", "createdAt": "2026-08-10T10:00:00Z", "isDraft": True,
     "reviewDecision": "REVIEW_REQUIRED",
     "statusCheckRollup": [{"__typename": "CheckRun", "conclusion": None, "status": "IN_PROGRESS"}]},
    {"number": 183, "title": "No checks", "createdAt": "2026-08-01T10:00:00Z", "isDraft": False,
     "reviewDecision": "", "statusCheckRollup": []},
])


def test_parse_prs_counts_checks_of_both_shapes():
    now = 1787200000  # 2026-08-19
    rows = parse_prs(PRS, now=now)
    n, title, age, draft, review, p, f, pend = rows[0]
    assert (n, p, f, pend) == (185, 2, 1, 0) and review == "approved" and not draft
    assert rows[1][3] and rows[1][7] == 1 and rows[1][4] == "review required"
    assert rows[2][5:] == (0, 0, 0)
    assert rows[0][2] < rows[1][2] < rows[2][2]
    assert parse_prs("not json") == []


def test_prs_chart_colours_by_check_state_and_dists_the_ages():
    text = prs_chart(parse_prs(PRS, now=1787200000), cols=110)
    p = plain(text)
    lines = p.split("\n")
    assert lines[0].startswith("#185") and "2/3" in lines[0] and "approved" in lines[0]
    assert "draft" in lines[1] and "0/1" in lines[1]
    assert "no checks" in lines[2]
    assert "c=coral" in text and "c=amber" in text        # a failure and a pending
    assert lines[-1].startswith("open for") and "3 open" in lines[-1]
    assert "t=dist" in text
    for cols in (60, 80, 100, 140):
        for line in plain(prs_chart(parse_prs(PRS, now=1787200000), cols=cols)).split("\n"):
            assert len(line) <= cols, (cols, line)


CHECKS = json.dumps([
    {"name": "test", "bucket": "pass", "state": "SUCCESS"},
    {"name": "lint", "bucket": "fail", "state": "FAILURE"},
    {"name": "build (macos)", "bucket": "pending", "state": "IN_PROGRESS"},
    {"name": "docs", "bucket": "skipping", "state": "SKIPPED"},
])


def test_parse_checks_maps_buckets_to_flow_statuses():
    assert parse_checks(CHECKS) == [("test", "done"), ("lint", "failed"),
                                    ("build (macos)", "active"), ("docs", "pending")]


def test_checks_chart_is_a_flow_and_a_meter():
    text = checks_chart(parse_checks(CHECKS), cols=100)
    p = plain(text)
    assert "t=flow" in text and " test " in p and " lint " in p
    assert "1 of 4 passed, 1 failed, 1 running" in p
    assert "c=coral" in text
    # past the cap only the interesting ones are drawn, and it says so
    many = [(f"check {i}", "done") for i in range(FLOW_CAP + 10)] + [("broken", "failed")]
    p = plain(checks_chart(many, cols=100))
    assert " broken " in p and " check 1 " not in p
    assert f"1 shown of {FLOW_CAP + 11}" in p
    # a narrow terminal wraps the flow into rows
    wide = [(f"a-rather-long-check-name-{i}", "done") for i in range(8)]
    assert plain(checks_chart(wide, cols=60)).count("\n") >= 3


RUNS = json.dumps([
    {"workflowName": "ci", "conclusion": "success", "status": "completed",
     "startedAt": "2026-08-18T10:00:00Z", "updatedAt": "2026-08-18T10:00:30Z"},
    {"workflowName": "ci", "conclusion": "failure", "status": "completed",
     "startedAt": "2026-08-18T09:00:00Z", "updatedAt": "2026-08-18T09:01:00Z"},
    {"workflowName": "release", "conclusion": "success", "status": "completed",
     "startedAt": "2026-08-17T09:00:00Z", "updatedAt": "2026-08-17T09:05:00Z"},
    {"workflowName": "ci", "conclusion": "", "status": "in_progress",
     "startedAt": "2026-08-18T11:00:00Z", "updatedAt": "2026-08-18T11:00:10Z"},
])


def test_parse_runs_and_chart():
    runs = parse_runs(RUNS)
    assert [r[0] for r in runs] == ["ci", "ci", "release", "ci"]
    text = runs_chart(runs, cols=100)
    p = plain(text)
    lines = p.split("\n")
    assert lines[0].startswith("ci") and "1/2 passed, 1 running" in lines[0]
    assert lines[1].startswith("release") and "1/1 passed" in lines[1]
    assert lines[-1].startswith("duration") and "3 runs" in lines[-1] and "last 30s" in lines[-1]
    assert "t=spark;d=300,60,30" in text            # oldest first, in seconds
    assert "c=amber" in text                          # ci at 50% is amber


def test_passthrough_keeps_filters_and_drops_output_flags():
    assert _passthrough(["-L", "5", "--json", "x", "-s", "open", "--web"],
                        ("-L", "-s"), ("--web",)) == ["-L", "5", "-s", "open", "--web"]
    assert _passthrough(["--limit=3"], ("--limit",), ()) == ["--limit=3"]
