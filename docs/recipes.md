# Recipes: commands whose output earns a chart

A recipe adds a gracefall chart to a command people already run. It never
touches the command's own output: it either prints the chart after the
real command returns, from a query it makes itself, or relays the command
through a pty and adds the chart beside it. Same bytes everywhere: blocks
in a plain terminal and over SSH, vector graphics in a terminal that
implements OSC 4700.

`gfl fmt` lists the recipes that exist. `eval "$(gfl init zsh)"` turns them
on. This page is the longer menu: what else could earn one, and the three
tests a command has to pass first. Fifty candidates is enough to see the
shape of the space; five good ones beat fifty shallow ones.

A recipe is only worth adding if it passes all three:

1. **The output has numbers with meaning.** A list of names does not.
2. **The chart says something the text does not say at a glance.** If the
   number is already one glance, a meter around it is decoration.
3. **The command has a stable or machine-readable mode** to parse from
   (`--format`, `--json`, `-P`), or its output has not changed in a decade.

Every recipe follows the same rules: only when stdout is a tty, never touch
a pipe or a script; add to the output, never replace it; on anything the
parser does not recognise, pass through byte for byte.

Ratings: **A** clearly earns it, do first. **B** good, second wave.
**C** plausible, only if asked for. Types: spark, meter, dist, flow,
scatter, heat.

## Built (in `gfl fmt` today)

| # | command | draws | type | why it earns it |
|---|---|---|---|---|
| 1 | `git log` | commit activity, last N weeks; with `--full` also weekday x hour, per author, lines per commit, per path | spark, heat, meter, dist | trend is invisible in a scrolling list; parse from `--format=%at` and `--numstat` |
| 2 | `df` | one meter per mounted volume | meter | "how full" is the only question; parse `df -P` |
| 3 | `ping host` | latency, live | spark | jitter and spikes only show over time; watch mode |
| 4 | `du -s *` | relative sizes | dist | which thing is big, at a glance |
| 5 | `pytest` / `npm test` | pass/fail meter, stage flow | meter, flow | the summary line hides the shape of a run |

## Version control (A/B): 6 to 9, 10 and 12 built in 0.6, plus `gh run list` from 11

| # | command | draws | type | rating |
|---|---|---|---|---|
| 6 | `git shortlog -sn` | commits per author | dist | A |
| 7 | `git diff --stat` | added vs removed per file | meter | A |
| 8 | `git branch -v` / `git status -sb` | ahead/behind per branch | meter | B |
| 9 | `git log --stat` | churn per commit over time | spark | B |
| 10 | `git blame` summary | line ownership | dist | C |
| 11 | `gh pr list` / `gh run list` | CI pass rate over recent runs | spark, meter | B |
| 12 | `gh pr checks` | check pipeline | flow | A |

## Disk, files, memory (A/B)

| # | command | draws | type | rating |
|---|---|---|---|---|
| 13 | `du -h --max-depth=1` | sizes per dir | dist | A (with 4) |
| 14 | `ls -l` sorted by size | size per file | dist | C, forced for most dirs |
| 15 | `free` / `vm_stat` | used vs total | meter | A |
| 16 | `swapon` / `sysctl vm.swapusage` | swap in use | meter | B |
| 17 | `iostat` | read/write throughput, live | spark | B |
| 18 | `smartctl -a` | wear, temperature | meter | C |
| 19 | `ncdu`-style tree | too interactive, skip | | skip |

## Processes and system (A/B)

| # | command | draws | type | rating |
|---|---|---|---|---|
| 20 | `top` / `htop` snapshot | cpu, mem per process | meter | A |
| 21 | `ps aux` sorted | cpu, mem per process | meter | B (with 20) |
| 22 | `uptime` | load 1/5/15 vs cores | meter | A, tiny and honest |
| 23 | `sensors` / `powermetrics` | temperature, power | meter, spark | B |
| 24 | `pmset -g batt` / `acpi` | battery | meter | A, one line |
| 25 | `nvidia-smi` | gpu util, vram | meter | A where it exists |
| 26 | `vmstat 1` | cpu, io, live | spark | B |
| 27 | `sar` | anything over time | spark | C |

## Network (A/B)

| # | command | draws | type | rating |
|---|---|---|---|---|
| 28 | `curl -w '%{time_*}'` | dns, connect, tls, ttfb, total | flow | A, small and striking |
| 29 | `traceroute` / `mtr` | latency per hop | dist, spark | A |
| 30 | `speedtest` | down/up vs plan | meter | B |
| 31 | `netstat -i` / `ifstat` | throughput per interface | spark | B |
| 32 | `ss -s` / `netstat -s` | connections by state | dist | C |
| 33 | `dig +stats` | query time | meter | C |
| 34 | `wget` / `curl` download | progress | meter | B, if the tool does not already draw one |

## Containers, cloud, infra (A/B)

| # | command | draws | type | rating |
|---|---|---|---|---|
| 35 | `docker stats` | cpu, mem per container | meter | A, `--format json` |
| 36 | `docker system df` | images, containers, volumes | dist, meter | A |
| 37 | `docker ps` | uptime per container | meter | C |
| 38 | `kubectl top pods/nodes` | cpu, mem vs requests | meter | A |
| 39 | `kubectl get pods` | phase per pod, rollout | flow, dist | B |
| 40 | `kubectl rollout status` | rollout progress | meter, flow | A |
| 41 | `terraform plan` | add / change / destroy counts | dist | B |
| 42 | `aws s3 ls --summarize` | size per prefix | dist | C |

## Build, test, packages (A/B)

| # | command | draws | type | rating |
|---|---|---|---|---|
| 43 | `cargo test` / `go test` | pass/fail, duration per package | meter, dist | A (with 5) |
| 44 | `pip install` / `npm install` | resolve, fetch, build, link | flow | B |
| 45 | `make` / `cargo build` / `zig build` | step counter | meter | A, proven by ghostty_run.py |
| 46 | `pytest --durations` | slowest tests | dist | A |
| 47 | `coverage report` | coverage per file | meter | A |
| 48 | `hyperfine` | run time distribution | dist | A |
| 49 | `time cmd` | user / sys / real | meter | C, three numbers is already a glance |
| 50 | `webpack` / `vite build` | bundle sizes per chunk | dist | B |

## Data and logs (B/C)

| # | command | draws | type | rating |
|---|---|---|---|---|
| 51 | `wc -l *` | lines per file | dist | B |
| 52 | `sort \| uniq -c` | counts | dist | A, tiny and universal |
| 53 | `awk` any numeric column | that column | spark, dist | B, via `gracefall spark` on a pipe |
| 54 | `journalctl` / `log show` | events per minute | spark | B |
| 55 | `tail -f access.log` | requests/s, status codes | spark, dist | B |
| 56 | `psql \dt+` / `sqlite3 .dbinfo` | table sizes | dist | C |
| 57 | `redis-cli info` | memory, hit rate | meter | C |

## Deliberately not on the list

Charts drawn on these make them worse, not better:

- `git status`, `ls`, `find`, `grep`, `cat`: lists and text. Nothing to chart.
- `git diff` (the diff itself): `delta` already owns this and does it well.
- Anything interactive (`vim`, `htop` proper, `less`): the recipe would fight it.
- Anything that already draws its own progress well: do not paint over it.

## What comes next

Every A above is a small parser and one span type. Two things decide which
get built: which ones people catch themselves looking at after a week with
the five that exist, and which ones people ask for. Open an issue with a
command and a paste of its real output; the parsers here are written
against exactly that.
