# Renders docs/demo/demo.gif: run `vhs docs/demo/demo.tape` from the repository root
# (needs vhs and the dev environment in .venv).
Output docs/demo/demo.gif

Set Shell bash
Set FontSize 15
Set Width 1000
Set Height 520
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

Hide
Type `export PATH="$PWD/.venv/bin:$PATH" PS1='$ ' d=$(mktemp -d) && cp -R tests/fixtures/project "$d/MyGame" && cp docs/demo/see.json "$d/MyGame/" && cd "$d/MyGame" && clear`
Enter
Sleep 1s
Show

Type "nodecanvas-cli show Bot_BT"
Sleep 300ms
Enter
Sleep 3.5s

Type "nodecanvas-cli refs Bot.Attack"
Sleep 300ms
Enter
Sleep 2.5s

Type "clear" Enter
Type "nodecanvas-cli apply Bot_BT see.json --dry-run"
Sleep 300ms
Enter
Sleep 4s

Type "nodecanvas-cli lint Broken_BT"
Sleep 300ms
Enter
Sleep 5s
