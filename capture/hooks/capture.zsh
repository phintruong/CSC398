# Live capture hooks for zsh (KAN-26). Sourced via `eval "$(python -m capture start ...)"`.
# Needs CAPTURE_DIR and CAPTURE_PY.

[[ -n ${__CAPTURE_LOADED:-} ]] && return 0
typeset -g __CAPTURE_LOADED=1 __capture_armed=
zmodload zsh/parameter 2>/dev/null
autoload -Uz add-zsh-hook

__capture_preexec() {
    __capture_armed=1
    "$CAPTURE_PY" -m capture hook pre --dir "$CAPTURE_DIR" --cwd "$PWD" --cmd="$1" 2>/dev/null
}

__capture_precmd() {
    local ec=$?
    if [[ -n $__capture_armed ]]; then
        "$CAPTURE_PY" -m capture hook post --dir "$CAPTURE_DIR" --exit "$ec" \
            --cols "${COLUMNS:-0}" --rows "${LINES:-0}" --jobs "${(j: :)${(k)jobstates}}" 2>/dev/null
        __capture_armed=
    fi
    return $ec
}

add-zsh-hook preexec __capture_preexec
add-zsh-hook precmd __capture_precmd
