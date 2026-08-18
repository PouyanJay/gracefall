# Recorded with VHS: https://github.com/charmbracelet/vhs
#   vhs docs/gitlog.tape
#
# VHS emulates xterm-256color, which has no graphics support, so this is
# what `gfl git log` looks like in a terminal that does not implement
# OSC 4700: the fallback, which is already the reading format. The pager
# is less -rFX; `/` searches, `n` is next, `q` quits.

Output "docs/gitlog.gif"
Set Shell "bash"
Set FontSize 16
Set Width 1180
Set Height 760
Set Padding 16
Set TypingSpeed 40ms

Hide
Type "export PATH=$HOME/.local/bin:$PATH; export GFL_PAGER='less -rX'; clear"
Enter
Show

Sleep 600ms
Type "gfl git log"
Enter
Sleep 4s

Type "/Aug 13"
Sleep 400ms
Enter
Sleep 2500ms

Type "G"
Sleep 2s

Type "q"
Sleep 1s
