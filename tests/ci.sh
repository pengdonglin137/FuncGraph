#!/usr/bin/env bash
set -euo pipefail

if (( $# == 0 )); then
    printf 'Usage: bash tests/ci.sh COMMAND [ARGUMENT...]\n' >&2
    exit 2
fi
log=$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/funcgraph-ci.XXXXXXXX.log")
set +e
"$@" 2>&1 | tee "$log"
statuses=("${PIPESTATUS[@]}")
set -e
result=${statuses[0]}
if (( result == 0 && statuses[1] != 0 )); then
    result=${statuses[1]}
fi
if (( result != 0 )) && [[ ${GITHUB_ACTIONS:-} == true ]]; then
    message=$(tail -n 120 "$log")
    if (( ${#message} > 16000 )); then
        message=${message: -16000}
    fi
    message=${message//'%'/'%25'}
    message=${message//$'\r'/'%0D'}
    message=${message//$'\n'/'%0A'}
    printf '\n::error title=Command failed (exit %s)::%s\n' "$result" "$message"
fi
exit "$result"
