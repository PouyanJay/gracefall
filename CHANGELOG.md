# Changelog

## Unreleased

### Added

- `gfl fmt --full git log`, or `GFL_FULL=1` in the environment for the
  shell function, draws the detailed view under a log: the commits-per-day
  spark, a heat of weekday by hour, one meter per author, a distribution
  of lines changed per commit, and one meter per top-level path by churn.
  The diff statistics come from a second query with its own timeout, so a
  large repository still gets the first sections when that one is slow.
- The git recipe now reads the log's own arguments. `--since`, `--until`,
  `--author`, `--grep`, `-n`, `-20`, `--no-merges`, revision ranges and
  pathspecs narrow the chart the same way they narrow the log; display
  flags such as `--oneline`, `--graph` and `-p` are ignored. When the log
  is bounded in time or count the label carries the dates instead of
  "last 8 weeks".
- `gfl git log [git log arguments]`: history as a reading format. The
  summary on top, then commits grouped under day headers with the day's
  count and churn, one line per commit with a size meter, the subject,
  tags and local branches, time, `+added -removed` and file count (the
  author column appears when there is more than one). Merges say so
  instead of showing an empty stat. The page keeps a margin, and fits
  the terminal: the summary's charts shrink to the width, and the file
  count, author and time columns fold away below 100, 90 and 70 cells. Pages through `less -rFX` or
  `$GFL_PAGER`; `-r` because `less -R` strips OSC envelopes. `--no-summary`
  and `--no-pager` do what they say; piped, the output is plain text.
- Disk, files and memory recipes. `du -h --max-depth=1` (also `-d N`)
  charts the entries at that depth, and `du --full` adds every entry and a
  dist of the sizes. `ls -l`: the largest files as meters and a dist of
  every size (from the filesystem, not from parsing ls). `free` and
  `vm_stat`: memory used against total, the breakdown as small meters on
  one scale (used / cached / free from `free -b`; app / wired /
  compressed / cached from vm_stat, counted the way Activity Monitor
  counts Memory Used), and swap. `swapon` and `sysctl vm.swapusage`: swap
  in use per device and in total. `iostat`: relayed through a pty like
  ping, with a live spark of total disk throughput and the per-disk
  figures beside it, reading both the macOS row shape and the Linux
  sysstat report shape. `smartctl -a`: relayed like a test runner because
  it usually needs root, so the chart is read from the output itself:
  wear, temperature and spare as meters, hours and reallocated sectors,
  from NVMe health logs and ATA attribute tables alike.
- Seven more recipes, all "after" charts under the command's own output.
  `git shortlog -sn`: one meter per author. `git diff` (any form): two
  meters per file on one scale, added and removed, and the total's added
  share. `git branch -v`: ahead and behind the upstream per branch, most
  recent first, checked-out bold; listing forms only, never a form that
  creates or deletes. `git status`: the branch against its upstream and
  the working tree as staged / unstaged / untracked / conflicts meters.
  `git blame file`: line ownership per author. `git log --stat` (or -p):
  the commits spark plus churn per commit in time order, also a section
  of the --full dashboard. `gh pr list`: a meter of each PR's checks, its
  age and review state, and a dist of how long PRs have been open. `gh pr
  checks`: the pipeline as a flow, wrapped to the terminal, and a meter of
  passed against all; past two dozen checks the flow keeps only failed and
  running. `gh run list`: a success-rate meter per workflow and a spark of
  run durations. The git and gh recipes are one shell function each that
  dispatch on the subcommand; the shell test skips Python for every other
  subcommand. Every chart folds columns to the terminal width.
- A seventh span type, `lanes`: one row of a commit graph as data. Each
  cell is a lane bar, a commit, a merge, a lane leaving or joining, a lane
  sliding under the row, or blank, with a colour role; the fallback is
  the box characters, one per cell. A receiver draws bars the full row
  height, leaving and joining lanes as S-curves between the centres of
  the neighbouring cells, sliding lanes along the bottom edge, and
  commits as discs on their lane (hollow for a merge), so independent
  rows read as continuous smooth lanes with no cross-row state. SPEC.md
  gives this type alone a drawing rectangle over every cell, blanks
  included, because a blank cell is where a curve lands. Also `gfl lanes
  b:teal r:blue . d:amber`, and a rollout-history section in the demo.
- `gfl git graph`, also `gfl git log --graph`: a compact coloured graph
  of every branch, one commit per line with hash, refs, subject, and the
  author and age dimmed at the right. git computes the lanes and colours
  them with the role palette through `log.graphColors`; each row
  goes out as a `lanes` span, padded into one column, and the view keeps
  remote refs (they matter in a graph of every branch) and marks merges
  with a hollow dot. 300 most recent commits unless -n, --since or a
  range says otherwise. Same pager as `gfl git log`.
- `gfl fmt --full df`: every volume df printed, most full first, with a
  space meter, percent, used / total, an inode meter (from `df -Pki`,
  parsed by header so macOS and Linux both read) and the device. Zero-size
  pseudo volumes stay, dim, so the panel covers df's whole table. The
  one-line view gains the percent, and both count it the way df's
  Capacity column does: used against used plus available, rounded up.
- `gfl fmt --watch [--every SECONDS]` redraws a recipe that queries for
  itself (df, du, git log) in place until Ctrl-C.
- `heat(rows, lo=, hi=)` pins the scale, as `spark` and `dist` already
  could, so heats drawn as neighbouring rows agree on what hot means.

### Changed

- The `git log`, `df` and `du` recipes print their chart after the command
  instead of before it, and the shell functions return the command's own
  exit status. A command that pages would otherwise hide the chart until
  the pager quits, and a chart under a table reads as its summary.
- The git recipe charts author dates, which is what `git log` prints,
  rather than committer dates.

## 0.5.0

### Added

- Recipes: charts for commands people already run. `eval "$(gfl init zsh)"`
  (or bash) in an rc file, and `git log` gets a spark of commits per day,
  `df` and `du` get one meter per volume or entry, `ping` gets a live
  latency spark under its replies, and `pytest` and `npm test` get a
  meter of passed against failed after their summary. The command's own
  output is never touched: a recipe either prints its chart before the
  real command runs, from a query it makes itself, or relays the command
  through a pty and adds the chart beside it. Nothing runs unless stdout
  is a terminal. `gfl fmt` lists them; `docs/recipes.md` is the longer
  menu of what could earn one next.

## 0.4.0

### Changed

- The flow fallback pads each stage name with one space on each side, and
  SPEC.md now documents that layout as normative. The padding is what lets
  a receiver draw a stage marker with room around the name: the text's
  size belongs to the terminal, and a marker widened past its own cells
  eats the join characters drawn on top of it. A flow line is four cells
  wider per stage than 0.3.x emitted.

## 0.3.5

### Changed

- The `gfl shell` prompt now says what gracefall is instead of what the
  current terminal cannot do. Naming someone's terminal tells them nothing
  they did not already know, and this prompt is often the first sight of
  the project.

## 0.3.4

### Fixed

- The message that declines to draw named the terminal by printing
  `TERM_PROGRAM` raw, producing lines like "vscode cannot draw images".
  That names an editor rather than a terminal and reads like a bug. Known
  terminals now get a human name (the VS Code terminal, Terminal.app,
  iTerm2, Warp, Ghostty, kitty, WezTerm, Alacritty, Hyper, Tabby, Rio) and
  anything unrecognised is simply "this terminal", because a raw
  environment value is worse than no name.

## 0.3.3

### Fixed

- The example charts in the `gfl shell` prompt are labelled and aligned.
  Two unlabelled runs of block characters asked the reader to already know
  what a sparkline looks like, which undercuts the point being made.

## 0.3.2

### Changed

- The `gfl shell` relaunch prompt is now drawn with gracefall's own output:
  a sparkline, a meter, and the palette. The charts in it are real spans
  rendered as text, which is the argument rather than decoration. This is
  what you already have, shown next to an offer of the smooth version.
  Each example is labelled and aligned, because unlabelled block art is a
  puzzle and the point being made is that it is readable. `NO_COLOR` strips
  it back to plain text.

## 0.3.1

### Added

- `gfl shell` in a terminal without graphics support now offers to open one
  that has them, listing whichever of Ghostty, kitty, and WezTerm are
  actually installed, and launching it in the same working directory.
  Being told your terminal cannot do something is only useful when the next
  step is obvious, and here the next step is just a different window.
  `--no-relaunch` opts out, and the prompt is skipped when stdin is not
  interactive so it can never hang a script.

## 0.3.0

### Added

- `gfl shell`: runs your shell on a pseudo-terminal and renders every
  gracefall span as it scrolls past. Nothing to pipe and nothing to
  configure, and every program inside is unmodified and unaware. Inside it
  `isatty` is true, so emitters produce their envelopes naturally with no
  `--force-osc`.

  It works out where a chart landed by counting the cells its fallback
  wrote, which makes the answer relative to the cursor and avoids emulating
  a terminal. When something happens mid-span that it does not model, such
  as absolute cursor positioning, it leaves the fallback alone rather than
  painting in the wrong place: a missing chart is just the fallback, but a
  misplaced one corrupts the screen.

  Images are opaque in this mode, since the fallback text is already on
  screen underneath and would otherwise show through the chart.

## 0.2.3

### Fixed

- `gfl view --watch` now exports `COLUMNS` and `LINES` to the watched
  command. The child's stdout is a pipe, so it cannot measure the terminal
  itself, and a dashboard laid out for a default 80 columns wrapped every
  line on a narrower window.
- `examples/sysmon.sh` sizes its bars to the terminal and keeps each value
  on the same line as its chart. Every gracefall command ends with a
  newline, so the value had been landing on the line below.

## 0.2.2

Both found by running it in Terminal.app, which does not swallow APC
sequences the way the graphics-capable terminals do.

### Fixed

- The capability probe printed `Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA` onto the
  screen. Terminal.app displays the contents of an APC sequence instead of
  consuming it, so probing corrupted the display of exactly the terminals
  the probe exists to rule out. It now saves the cursor, probes, then
  restores and erases forward, which cleans up any leak and is a no-op on a
  terminal that behaved.
- `gfl view --watch` did nothing useful without graphics support: it printed
  the message and exited. It now runs the loop in text mode, repainting the
  fallback in place, because a live text dashboard is still a live dashboard
  and `--watch` should not be the one feature that needs a special terminal.
  The text path emits no APC at all, including the image delete.

## 0.2.1

Found by using the tool on real data rather than the demo.

### Fixed

- Empty or non-numeric input raised a traceback. A pipeline that produces
  nothing is normal (a grep that misses, a log with no lines yet), so
  `spark` and `dist` now exit 1 with "no data: stdin was empty" or
  "not a number: 'x'".
- `gfl view --watch` repainted text with no graphics on it. The watched
  command's stdout is a pipe, so its own isatty check stripped the very
  envelopes it was being asked to produce. The watch loop now sets
  `GRACEFALL_FORCE_OSC=1` for the child, and the emitter honours it.
  `--no-osc` still wins.

### Added

- `examples/sysmon.sh`, a real dashboard of disk, memory, battery, load, and
  process CPU, built entirely out of gracefall and readable in any terminal.
- A recipes section in the README.

## 0.2.0

The release that makes the smooth rendering real. `gfl view` paints spans as
graphics in terminals that already speak the kitty graphics protocol, and
the geometry behind it is now shared, so the two renderings cannot drift.

Nothing about the wire format changed. A 0.1 stream renders identically.

### Added

- `gfl view`: paints spans as graphics over their own cells using the kitty
  graphics protocol, verified in Ghostty 1.3.1 and kitty 0.48.2. Falls back
  to printing the stream untouched, with a reason on stderr, in a terminal
  without graphics support. Behind the optional `view` extra:
  `pip install "gracefall[view]"`.
- `gfl view --watch CMD`: re-runs a command on an interval and repaints in
  place, inside synchronized output, deleting the previous frame's images
  each cycle so they cannot accumulate.
- `gracefall render --png`: composes a whole stream, text and spans, to a
  PNG without needing a terminal. `docs/compare.png` is now generated from
  this rather than by hand.
- `src/gracefall/shapes.py`: the shared geometry core. One
  `shapes_for(attrs, box)` feeds the SVG renderer, the terminal viewer, and
  anything added later.
- `src/gracefall/raster.py`: the Pillow backend, including block elements
  drawn as exact cell fractions rather than font glyphs.
- `SPEC.md` appendices on rendering a span and on multiplexer passthrough,
  both non-normative.
- A developer task runner (`make help`), including `make visual-diff`, which
  rasterizes the rendering at two git refs and compares actual pixels.

### Fixed

- `--force-osc` and `--no-osc` are accepted after the subcommand, not only
  before it. The README's own `gracefall demo --force-osc` exited 2 and,
  when redirected, left an empty file. (0.1.1)
- `gracefall --help` crashed with `ValueError: incomplete format`, because
  argparse `%`-expands help text and meter's "0..1 or N%" is an incomplete
  format specifier. (0.1.1)
- Span rendering is now clipped to the span's own cells, as SPEC.md
  requires. The spark's end marker was centered on the box edge, so half of
  it was undrawable by any conforming receiver; it is now held inside by
  its own radius.
- Inside tmux without `allow-passthrough on`, `gfl view` now prints the
  fallback and says why. It previously blanked the span's cells and then
  lost the images tmux had swallowed, leaving empty space where a chart
  should be.
- Placeholder URLs (`CHANGEME`, `<user>`) in the packaging metadata and the
  README. (0.1.1)

### Changed

- CI runs `make test`, the same target used locally, and now covers Python
  3.9, the floor declared in `requires-python`.

## 0.1.1

- `--force-osc` and `--no-osc` accepted on both sides of the subcommand.
- `gracefall --help` no longer raises.
- Placeholder URLs fixed in packaging metadata and the README.

## 0.1.0

First release. Emitter, CLI, and the SVG reference renderer, with six span
types: spark, meter, dist, flow, scatter, heat.
