# Live capture hooks for bash (KAN-26). Sourced via `eval "$(python -m capture start ...)"`.
# Needs CAPTURE_DIR and CAPTURE_PY. Calls `python -m capture hook pre|post`.
# preexec = DEBUG trap (first simple command of each typed line), precmd = PROMPT_COMMAND.

[ -n "${__CAPTURE_LOADED:-}" ] && return 0
__CAPTURE_LOADED=1
__capture_armed=
__capture_last_hist=

__capture_preexec() {
    [ -n "${COMP_LINE:-}" ] && return
    [ -n "$__capture_armed" ] && return
    case "$BASH_COMMAND" in __capture_precmd*) return ;; esac
    local this num cmd
    this=$(HISTTIMEFORMAT= builtin history 1)
    num=${this%%[!0-9 ]*}
    num=${num// /}
    # Enter on an empty line re-runs PROMPT_COMMAND with the same history entry
    if [ -z "$num" ] || [ "$num" = "$__capture_last_hist" ]; then return; fi
    __capture_last_hist=$num
    cmd=$(printf '%s' "$this" | sed -e 's/^ *[0-9][0-9]*[* ] *//')
    __capture_armed=1
    "$CAPTURE_PY" -m capture hook pre --dir "$CAPTURE_DIR" --cwd "$PWD" --cmd="$cmd" 2>/dev/null
}

__capture_precmd() {
    local ec=$?
    if [ -n "$__capture_armed" ]; then
        "$CAPTURE_PY" -m capture hook post --dir "$CAPTURE_DIR" --exit "$ec" \
            --cols "${COLUMNS:-0}" --rows "${LINES:-0}" --jobs "$(jobs -p | tr '\n' ' ')" 2>/dev/null
        __capture_armed=
    fi
    return $ec
}

shopt -s checkwinsize
trap '__capture_preexec' DEBUG
PROMPT_COMMAND="__capture_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
