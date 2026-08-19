<div align="center">

<img src="docs/logo.svg" width="96" height="96" alt="gracefall" />

# gracefall

**Charts for the terminal that never break. Text everywhere, vector graphics where the terminal can draw.**

A program prints a chart once. In every terminal that exists today it shows as
readable unicode blocks. In a terminal that implements OSC 4700 the same bytes
are drawn as smooth, theme-aware graphics. Nothing to detect, nothing to
negotiate, and the text stays selectable, greppable and readable by a screen
reader either way.

[![PyPI](https://img.shields.io/pypi/v/gracefall?style=flat-square&color=5fe3c0&label=PyPI)](https://pypi.org/project/gracefall/)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![Zero dependencies](https://img.shields.io/badge/dependencies-none-2b2f3a?style=flat-square)](pyproject.toml)
[![CI](https://img.shields.io/github/actions/workflow/status/PouyanJay/gracefall/ci.yml?style=flat-square&label=CI)](https://github.com/PouyanJay/gracefall/actions/workflows/ci.yml)
[![Spec: CC0](https://img.shields.io/badge/spec-OSC%204700%20draft%201%20%C2%B7%20CC0-8b94a6?style=flat-square)](SPEC.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-e8a33d?style=flat-square)](LICENSE)

[Why it exists](#why-it-exists) ·
[How it works](#how-it-works) ·
[Quick start](#quick-start) ·
[The seven types](#the-seven-types) ·
[See it drawn](#see-it-drawn) ·
[Recipes](#recipes) ·
[Status](#status) ·
[Spec](SPEC.md)

</div>

---

## Why it exists

Putting a chart in a terminal today means choosing between two bad options.
Draw pixels (Sixel, kitty graphics, iTerm2 images) and a terminal that lacks
the protocol prints garbage or nothing, so you first have to ask what the
terminal is, and that question fails over SSH, inside tmux, in a pipe and in
a recording. Or print unicode blocks, which work everywhere and never get
better.

gracefall does both at once. Every chart is generated unicode text wrapped in
an envelope that carries the data behind it:

```
ESC ] 4700 ; t=spark ; d=1,4,2,8 ; c=blue ST   ▁▄▂█   ESC ] 4700 ; ST
```

A terminal that does not know OSC 4700 ignores the envelope and prints
`▁▄▂█`, which is already a chart. A terminal that does know it draws those
same cells as vector graphics in the theme's colours. Here is `gracefall
demo`, the same bytes, in two real Ghostty windows: the released 1.3.1 on
the left, and a build with OSC 4700 on the right.

![gracefall demo in Ghostty 1.3.1 as fallback text, and in a Ghostty build with OSC 4700 drawn as graphics](docs/ghostty-compare.png)

Because the fallback is ordinary text in ordinary cells, everything a
terminal already does keeps working: selection, copy, grep, scrollback, tmux
replay, screen readers, `tee` to a log file. And because the envelope carries
data rather than pixels, the terminal owns the rendering and adapts it to
theme, font size and DPI.

## How it works

```mermaid
flowchart LR
    APP["your program<br/>print(spark(values))"] --> BYTES["one byte stream<br/>OSC 4700 data, then ▁▄▂█, then OSC 4700 close"]
    BYTES --> A["any terminal today<br/>shows ▁▄▂█"]
    BYTES --> B["a terminal with OSC 4700<br/>draws the same cells as graphics"]
    BYTES --> C["pipe, log, grep, tmux, SSH<br/>plain text, nothing lost"]
```

Four rules hold the whole thing together. They are the contract in
[SPEC.md](SPEC.md), and every part of this repository is checked against them.

| Rule | What it means in practice |
|---|---|
| **Live inside the byte stream** | No side channel, no new file format, no daemon. It is text with an envelope. |
| **Zero coordination** | No capability query, no round trip. Emitting is safe over SSH, into a pipe, into a recording. |
| **Degrade at the receiver** | The emitter never asks what the terminal is. The terminal decides what it can draw. |
| **Preserve text semantics** | The fallback is real cells, so selection, grep, scrollback and screen readers work by construction. |

Two consequences follow. The fallback is always generated from the same data
by the same function, so it cannot drift from the envelope. And payloads are
data, never pixels and never drawing commands, so a chart is small (an
envelope is capped at 2048 bytes) and a terminal can re-render it however it
likes.

## Quick start

```sh
uv tool install gracefall        # or: pipx install gracefall
```

Pure Python 3.9+, standard library only, no dependencies. `gfl` is installed
as a short alias.

```sh
seq 1 20 | gracefall spark
gracefall meter 0.62 -c amber
gracefall flow build:done test:done canary:active prod:pending
gracefall dist --bins 20 < latencies.txt
paste xs.txt ys.txt | gracefall scatter
gracefall demo
```

![typing gracefall commands in a plain xterm-256color terminal, ending with one envelope made visible](docs/demo.gif)

That recording is a plain `xterm-256color`, so every chart in it is the
fallback text. The last line makes one envelope visible: the data, the
blocks, the close.

Piping is safe by default. Envelopes are emitted only when stdout is a
terminal, so `gracefall spark ... | less` and shell captures get plain text.
`--force-osc` keeps them (to save a stream), `--no-osc` strips them always.

```sh
gracefall demo --force-osc > examples/inference.gfall
cat examples/inference.gfall                        # safe in any terminal
grep "kv cache" examples/inference.gfall            # it is text
gracefall render examples/inference.gfall -o out.svg
```

As a library:

```python
from gracefall import spark, meter, flow

print("p99  " + spark([92, 88, 84, 90, 97, 84], color="blue") + "  84ms")
print("disk " + meter(0.62, color="amber"))
print(flow(["build", "test", "deploy"], ["done", "active", "pending"]))
```

## The seven types

Every type is declarative data. The middle column is what a plain terminal
shows; the right column is what a drawing terminal makes of the same bytes.

| Type | Plain terminal | Drawn |
|---|---|---|
| `spark` | `▁▃▂▆▄█▇█` | smooth line with a marker on the last value |
| `meter` | `███████▍▁▁▁▁` | rounded gauge with a gradient fill |
| `dist` | `██▃▁▃▃▁▁▁▃` | histogram bars |
| `flow` | ` build ── test ── deploy ` | status capsules around each stage name |
| `scatter` | braille dots | points with a dashed trend line |
| `heat` | half-block cells | a grid of rounded, graded cells |
| `lanes` | `│╲ ●` box drawing | one row of a commit graph: lanes as smooth curves, commits as discs, merges hollow |

Colours are roles, not values: `fg dim teal blue amber coral violet`. The
terminal resolves them against its theme, which is what makes one stream
correct on both light and dark backgrounds.

### The creature: charts all the way down

The mascot at the end of `gracefall demo` is made of those same seven
types and nothing else. Its head is a `lanes` figure, the primitive a
commit graph row is made of: the crown and the smile are lanes curving
away, and each eye is a commit disc on its lane. Its arms are a `spark`
of recent load, its belly a `meter`, and the air around it a `heat` glow
or a `scatter` of specks.

```
⡀⠁⠐⢀ ╱ ╲ ⡀⠂⠈⢀    ▀▀▀▀ ╱ ╲ ▀▀▀▀
▆▆▄ ●   ● ▄▆▆    ▃▄▅ ●   ● ▅▄▃
     ───              ╲ ╱
  ██████▋▁▁        ██▊▁▁▁▁▁▁
```

So it degrades like every other chart: box characters and blocks in a
plain terminal, smooth curves and beads where OSC 4700 is drawn, and the
frames are pure functions of a mood, a tick and a dict of signals, so they
can be golden-tested like anything else. No type was added for it, and
none was needed: if a mascot can be assembled out of seven declarative
data types, an application's real chart certainly can.
[docs/creature.md](docs/creature.md) is the anatomy.

#### `gfl pet`

```sh
gfl pet              # watch it until you press a key
gfl pet --once       # one frame, for a prompt or a recording
```

It repaints four times a second through the same loop the live recipes
use, and the machine drives it: the load average over the core count is
the belly and the swing of the arms, an uncommitted tree turns the crown
amber, and `GFL_CI=pass` or `GFL_CI=fail` in the environment is the mood.
The tree is asked at most every five seconds and nothing here touches the
network, so the loop costs well under one percent of a core. `--mood`
holds one mood, `--size` takes one, two or four lines, and any key leaves
with the last frame still on screen.

## See it drawn

There are three ways to see the smooth rendering, in increasing order of
how native they are.

**`gfl view`** paints the drawing over the text using the kitty graphics
protocol, which Ghostty, kitty and WezTerm already speak. It is a stand-in
for a terminal that implements OSC 4700, and it works today:

```sh
uv tool install "gracefall[view]"     # adds Pillow, the only optional dependency
gracefall demo --force-osc | gfl view
gfl view --watch examples/sysmon.sh   # live, repainting in place
```

**`gfl shell`** runs your normal shell inside gracefall so anything any
program emits is drawn as it scrolls past, with nothing to pipe. In a
terminal that cannot draw, it offers to open one that can:

```sh
gfl shell
```

**A terminal that implements OSC 4700 natively** needs neither. A working
implementation exists as a
[Ghostty branch](https://github.com/ghostty-org/ghostty/compare/main...PouyanJay:ghostty:osc-4700-mvp):
about 3000 lines under `src/terminal/`, the six original types (`lanes` is next), drawn through
Ghostty's existing image storage so reflow and scrollback come for free.
It is proposed upstream in
[ghostty-org/ghostty#13884](https://github.com/ghostty-org/ghostty/discussions/13884).
The comparison at the top of this page is that branch next to Ghostty
1.3.1.

Notes on how `gfl view` finds each span's cells, and its limits under tmux
and scrollback, are in [docs/view-notes.md](docs/view-notes.md).

## Recipes

Charts for commands you already run. One line in your rc file:

```sh
eval "$(gfl init zsh)"      # or bash
```

and these commands start showing a chart, in any terminal:

| you type | you also get |
|---|---|
| `git log` | a spark of commits per day over the last eight weeks, under the log. `gfl fmt --full git log` (or `export GFL_FULL=1`) adds when in the week they land, who made them, how big they are and which paths they touch, and the log's own `--since`, `--author`, `-n` and range arguments narrow the chart |
| `df` | one meter per volume, most full first, with df's own percent. `gfl fmt --full df` is every volume df printed: space meter, percent, used / total, an inode meter and the device |
| `du -s *` | one meter per entry, largest first |
| `ping host` | a live latency spark that stays under the replies |
| `pytest`, `npm test` | a meter of passed against failed, after the summary |
| `git shortlog -sn` | one meter per author, most commits first |
| `git diff`, `git diff --stat` | added against removed per file, two meters on one scale, and the total's added share |
| `git branch -v` | ahead and behind the upstream per branch, most recent first, the checked-out one bold |
| `git status`, `git status -sb` | the branch against its upstream, and staged / unstaged / untracked / conflicts as meters |
| `git blame file` | line ownership, one meter per author |
| `git log --stat` | the commits spark plus churn per commit in time order |
| `gh pr list` | a meter of each PR's checks (teal, amber while pending, coral on a failure), its age and review state, and a dist of how long PRs have been open |
| `gh pr checks` | the pipeline as a flow (only what needs attention past two dozen checks) and a meter of passed against all |
| `gh run list` | a success-rate meter per workflow and a spark of run durations |
| `du -h --max-depth=1` | one meter per entry at that depth, largest first; `--full` every entry and a dist of the sizes |
| `ls -l` | the largest files as meters and a dist of every size in the listing |
| `free`, `vm_stat` | memory used against total, the breakdown (used / cached / free, or app / wired / compressed / cached on macOS), and swap |
| `swapon`, `sysctl vm.swapusage` | swap in use, per device and in total |
| `iostat -w 1` | a live spark of disk throughput under the output, per-disk figures beside it (macOS and Linux shapes) |
| `smartctl -a /dev/…` | wear, temperature and spare as meters, hours and reallocated sectors, read from the output itself (NVMe and ATA) |

The command's own output is never touched. `git log` still pages and
colours; `pytest` still prints its dots and its tracebacks. A recipe either
prints its chart *after* the real command returns, from a query it makes
itself, or relays the command through a pty and adds the chart beside it.
Nothing runs unless stdout is a terminal, so pipes, scripts and CI see
exactly what they saw before. And when a parser does not recognise the
output, it draws nothing and says nothing.

`gfl fmt` lists the recipes. `gfl init zsh` prints the functions, so you
can read them before you eval them. `gfl fmt --watch df` (or `du`, or
`git log`, `--every 5` for the interval) redraws the chart in place until
Ctrl-C, which with `--full` makes a live disk panel out of `df`. More candidates, and the three tests a
command has to pass to earn one, are in
[docs/recipes.md](docs/recipes.md).

### Reading history: `gfl git log`

The recipe leaves `git log` alone. When the question is "what happened
here" rather than "which commit", `gfl git log` is the other view: the
same summary on top, then the commits grouped under day headers, one line
each with a size meter, so the busy days and the big commits stand out
before you read a word.

```sh
gfl git log                  # last 8 weeks
gfl git log -50              # or any git log argument: --since, --author,
gfl git log v0.4.0..HEAD     # --grep, --no-merges, a range, a pathspec
```

![gfl git log in a plain xterm-256color terminal: the summary, then commits under day headers, a search with slash, and q to quit](docs/gitlog.gif)

That is the fallback, in a terminal with no graphics support at all. The
same bytes through the reference renderer's enhanced view, which is what
a terminal implementing OSC 4700 draws:

![the same gfl git log page with the summary and size meters drawn as graphics](docs/gitlog.png)

Tags and local branches sit at the end of the subject column; merges say
`merge` where the meter would be. It pages through `less -rFX` (or
`$GFL_PAGER`), so `/`, `n`, `g`, `G` and `q` are the navigation, as under
`git log`. `-r` rather than `-R` because `less -R` strips the OSC
envelopes and prints their attributes as text. `--no-summary` skips the
charts, `--no-pager` writes straight out, and piped it is plain text.
Patches are not this view's job; `git log -p` and `delta` own those.

`gfl git graph` (or `gfl git log --graph`) is the branch view: a compact
coloured graph of every branch, one commit per line with hash, refs,
subject, and the author and age dimmed at the right. git computes the
lanes, in the role palette; each row goes out as a `lanes` span, so a
plain terminal shows box characters, padded into one column with merges
as a hollow dot, and a terminal that draws OSC 4700 shows the lanes as
smooth curves and the commits as discs. Same arguments,
same pager; 300 most recent commits unless you say `-n`, `--since` or a
range.

```
  ○ │             159cf6d7e pkg/wuffs: use C-only mirror of wuffs (#13789)      Mitchell Hashi  13h
  │╲ ╲
  │ ● │           7c4c7adad pkg/wuffs: use C-only mirror of wuffs               Jeffrey C. Oll   4d
  ○ │ │           385a378fe termio: preserve UTF-8 in desktop notification tr…  Mitchell Hashi  13h
  │╲ ╲ ╲
  │ ● │ │         53c6fdbe7 apprt: own desktop notification truncation            dolzhenko.e4   2d
```

### By hand

Any pipeline that produces numbers is a recipe. Each of these is a live
reading of your machine, in any terminal:

```sh
# disk usage as a meter
df -H /System/Volumes/Data | awk 'NR==2{gsub(/%/,"",$5); print $5/100}' \
  | xargs -I{} gracefall meter {} -c amber -w 30

# cpu across the busiest processes
ps -A -o %cpu | tail -n +2 | sort -rn | head -30 | gracefall spark -c teal

# commits per day, last 30 days
git log --since=30.days --format=%ad --date=short | sort | uniq -c \
  | awk '{print $1}' | gracefall spark -c violet

# request latency from a log, as a histogram
awk '{print $NF}' access.log | gracefall dist --bins 30

# deploy status
gracefall flow build:done test:done canary:active prod:pending
```

[examples/sysmon.sh](examples/sysmon.sh) puts several of these together into
a dashboard. Run it once, or live:

```sh
examples/sysmon.sh
gfl view --watch examples/sysmon.sh
```

## Status

| Part | State |
|---|---|
| Emitter, CLI, library | Working. On PyPI as `gracefall`. |
| Reference renderer | Working. SVG and PNG out, and the thing CI checks the emitter against. |
| `gfl view` and `gfl shell` | Working, verified in Ghostty and kitty. |
| Recipes (`gfl fmt`, `gfl init`) | git log, shortlog, diff, branch, status, blame; gh pr list, pr checks, run list; df, du, ls -l, free / vm_stat, swapon / sysctl vm.swapusage, iostat, smartctl, ping, pytest and npm test. |
| `gfl git log` | History as a reading format: summary on top, commits under day headers with a size meter, through your pager. |
| `gfl git graph` | Every branch as a compact coloured graph, one commit per line, git's lanes in the role palette. |
| Native OSC 4700 in a terminal | One implementation, the Ghostty branch above. Proposed upstream, not merged. No released terminal ships it yet. |
| Specification | Draft 1, in [SPEC.md](SPEC.md), CC0. |

That last row is the honest state of a young protocol, and it is the row
that matters most. If you maintain a terminal, the whole thing is seven
declarative types and three drawing primitives; the geometry lives in one
module ([shapes.py](src/gracefall/shapes.py)) so it can be ported line by
line, and the Ghostty branch shows what a complete port looks like. Open an
issue and we will help.

## Development

```sh
make help          # everything below, and more
make test          # the suite; CI runs this exact target
make verify        # tests plus a pixel diff of the rendering against HEAD
make visual-diff REF=v0.3.5   # pixel diff against any git ref
make smoke         # build the wheel, install it clean, exercise the CLI
make compare       # docs/compare.png, both views through the reference renderer
make gitlog-demo   # docs/gitlog.gif via vhs, docs/gitlog.png through the renderer
make ghostty-run   # build, sign and launch the OSC 4700 Ghostty fork
```

The tests are invariants, not coverage: the fallback is clean text, the
renderer reads what the emitter writes, every span fits the size cap,
piped output is plain text. `make visual-diff` rasterizes the rendering
before and after a change and compares pixels, because a string diff of SVG
once hid a real regression in 79 pixels.

## Shell integration

`shell/gracefall.zsh` provides completions for every subcommand:

```sh
zinit light PouyanJay/gracefall
```

## Contributing

Read [SPEC.md](SPEC.md) first; it is short. Adding a type means touching the
emitter, the spec, the CLI, the geometry module, the completions and the
tests, and the [CHANGELOG](CHANGELOG.md) says what changed in each release.
Bug reports with a saved `.gfall` stream attached are the easiest to act on.

## License

Code is MIT. The specification text in [SPEC.md](SPEC.md) is CC0, so any
terminal can copy it without attribution. The logo is built from two
[Hugeicons](https://hugeicons.com) free icons (MIT).
