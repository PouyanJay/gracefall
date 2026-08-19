# gracefall zsh integration: completions for the CLI.
# Load via any plugin manager, or: source shell/gracefall.zsh

_gracefall() {
  local -a cmds
  cmds=(
    'spark:inline trend line'
    'meter:horizontal gauge'
    'dist:histogram'
    'flow:pipeline strip'
    'scatter:x/y points from stdin'
    'heat:value grid from stdin'
    'lanes:one row of a commit graph, cells such as b:teal r:blue . d:amber'
    'demo:full showcase'
    'view:paint spans as graphics in a capable terminal'
    'shell:run your shell inside gracefall, rendering everything'
    'strip:remove envelopes from a stream'
    'render:reference renderer to SVG'
    'fmt:add a chart to a command you already run'
    'git:history as a reading format, gfl git log and gfl git graph'
    'pet:the creature, breathing until you press a key'
    'init:print the shell functions that turn recipes on'
  )
  if (( CURRENT == 2 )); then
    _describe 'command' cmds
  elif (( CURRENT == 3 )) && [[ $words[2] == fmt ]]; then
    local -a recipes
    recipes=(
      '--full:the detailed view where a recipe has one'
      '--watch:redraw in place until ctrl-c'
      '--every:seconds between redraws'
      'df:one meter per volume, --full for every volume and inodes'
      'du:one meter per entry, --max-depth honoured'
      'free:memory used against total, breakdown and swap'
      'iostat:a live spark of disk throughput'
      'ls:ls -l, the largest files and a dist of sizes'
      'smartctl:wear, temperature and spare as meters'
      'swapon:swap in use per device'
      'sysctl:sysctl vm.swapusage, swap in use'
      'vm_stat:memory used against total, breakdown and swap'
      'gh:pr list, pr checks, run list'
      'git:log, shortlog, diff, branch, status, blame'
      'npm:npm test, a meter of passed against failed'
      'ping:a live latency spark'
      'pytest:a meter of passed against failed'
    )
    _describe 'recipe' recipes
  elif (( CURRENT == 3 )) && [[ $words[2] == git ]]; then
    _values 'git subcommand' log graph
  elif (( CURRENT >= 3 )) && [[ $words[2] == pet ]]; then
    local -a pet
    pet=(
      '--mood:hold one mood: idle working happy sad sleepy'
      '--size:lines to draw on, 1, 2 or 4'
      '--every:seconds between frames, default 0.25'
      '--once:print one frame and exit'
    )
    _describe 'pet option' pet
  elif (( CURRENT == 3 )) && [[ $words[2] == init ]]; then
    _values 'shell' zsh bash
  fi
}
compdef _gracefall gracefall gfl
