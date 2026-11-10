#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 1 || -z $1 ]]; then
    echo "Usage: bash site/build.sh OUTPUT_DIRECTORY" >&2
    exit 2
fi

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mkdir -p -- "$1"
cp -- "$root"/site/index.html "$root"/sample{,_mixed}.{html,png} "$root"/samples/sample_text.png "$1/"
touch -- "$1/.nojekyll"
