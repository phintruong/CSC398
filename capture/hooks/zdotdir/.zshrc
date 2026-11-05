# ZDOTDIR for `python -m capture record` (zsh): the user's normal rc, then the hooks.
[[ -f "$HOME/.zshrc" ]] && ZDOTDIR="$HOME" source "$HOME/.zshrc"
source "${${(%):-%x}:A:h:h}/capture.zsh"
