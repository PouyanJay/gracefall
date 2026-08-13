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
    'demo:full showcase'
    'strip:remove envelopes from a stream'
    'render:reference renderer to SVG'
  )
  if (( CURRENT == 2 )); then
    _describe 'command' cmds
  fi
}
compdef _gracefall gracefall gfl
