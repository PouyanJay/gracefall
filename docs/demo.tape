# Recorded with VHS: https://github.com/charmbracelet/vhs
#   vhs docs/demo.tape
#
# VHS emulates xterm-256color, which has no graphics support, so this
# records exactly what a non-implementing terminal shows. That is the
# point: the stream is emitted blind and degrades to real charts.

Output "docs/demo.gif"
Set Shell "bash"
Set FontSize 18
Set Width 1100
Set Height 620
Set Padding 20
Set TypingSpeed 40ms

Hide
Type "export PATH=/tmp/gflv/bin:$PATH; clear"
Enter
Show

Sleep 800ms
Type "seq 1 40 | gracefall spark -c teal"
Enter
Sleep 1500ms

Type "gracefall meter 62% -c amber"
Enter
Sleep 1500ms

Type "gracefall flow build:done test:done canary:active prod:pending"
Enter
Sleep 2s

Type "gracefall demo"
Enter
Sleep 4s

Type "# every chart above carries its data in an OSC 4700 envelope"
Enter
Sleep 1500ms
Type "gracefall demo --force-osc | head -c 120 | cat -v"
Enter
Sleep 3s
