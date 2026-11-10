#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SOURCE="$ROOT/third_party/uftrace"
PATCHES="$ROOT/patches/uftrace"
ACTION=${1:-help}
shift || true
usage() {
    echo 'Usage: scripts/uftrace.sh {check|build|refresh} [--arch ARCH]'
    echo 'ARCH: x86_64, aarch64 (arm64), i386, arm, riscv64; CROSS_COMPILE and JOBS are supported.'
}
case "$ACTION" in
    help|-h|--help) usage; exit 0 ;;
    check|build|refresh) ;;
    *) usage >&2; exit 2 ;;
esac
ARCH=$(uname -m)
if [[ ${1:-} == --arch ]]; then
    [[ $# -ge 2 ]] || { echo 'Missing architecture' >&2; exit 2; }
    ARCH=$2
    shift 2
fi
case "$ARCH" in
    arm64) ARCH=aarch64 ;;
    i?86) ARCH=i386 ;;
    x86_64|aarch64|arm|riscv64) ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 2 ;;
esac
[[ $# == 0 ]] || { echo 'Unexpected arguments' >&2; exit 2; }
[[ -d $SOURCE && -e $SOURCE/.git ]] || {
    echo 'Initialize uftrace: git submodule update --init third_party/uftrace' >&2; exit 1;
}
[[ $(git -C "$SOURCE" rev-parse --show-toplevel) == "$SOURCE" ]] || {
    echo 'uftrace is not an initialized submodule' >&2; exit 1;
}
BASE=$(git -C "$ROOT" ls-files --stage third_party/uftrace | awk '$1 == "160000" && $3 == "0" {print $2}')
[[ -n $BASE && $(git -C "$SOURCE" rev-parse HEAD) == "$BASE" ]] || {
    echo 'uftrace HEAD must match the indexed submodule pin' >&2; exit 1;
}
TEMP=$(mktemp -d)
trap 'rm -rf "$TEMP"' EXIT

read_series() {
    local patch
    declare -A seen=()
    while IFS= read -r patch || [[ -n $patch ]]; do
        [[ -z $patch || $patch == \#* ]] && continue
        [[ $patch =~ ^[a-zA-Z0-9_-][a-zA-Z0-9_.-]*\.patch$ && -z ${seen[$patch]:-} &&
           -f $1/$patch && ! -L $1/$patch ]] || {
            echo "Invalid or duplicate patch: $patch" >&2; return 1;
        }
        seen[$patch]=1
        printf '%s\n' "$patch"
    done < "$1/series"
}
read_series "$PATCHES" > "$TEMP/old-series"

prepare() {
    local patch
    git -C "$SOURCE" archive "$BASE" | tar -x -C "$1"
    while IFS= read -r patch; do
        git --git-dir="$SOURCE/.git" --work-tree="$1" -C "$1" apply --check "$PATCHES/$patch"
        git --git-dir="$SOURCE/.git" --work-tree="$1" -C "$1" apply "$PATCHES/$patch"
    done < "$TEMP/old-series"
}

case "$ACTION" in
    check)
        mkdir "$TEMP/check"
        prepare "$TEMP/check"
        echo 'uftrace patches apply cleanly'
        ;;
    build)
        TARGET="$ROOT/build/uftrace-$ARCH"
        TREE="$ROOT/build/uftrace-src-$ARCH"
        [[ ! -e $TREE && ! -e $TARGET ]] || {
            echo "Move or remove $TREE and $TARGET explicitly before rebuilding" >&2; exit 1;
        }
        [[ ${JOBS:-1} =~ ^[1-9][0-9]*$ ]] || { echo 'JOBS must be positive' >&2; exit 2; }
        mkdir -p "$ROOT/build"
        mkdir "$TREE" "$TARGET"
        prepare "$TREE"
        cd "$TREE"
        export GIT_CEILING_DIRECTORIES="$ROOT/build"
        CONFIGURE=(--prefix="$TARGET" --arch="$ARCH")
        [[ -z ${CROSS_COMPILE:-} ]] || CONFIGURE+=(--cross-compile="$CROSS_COMPILE")
        ./configure "${CONFIGURE[@]}"
        make -j"${JOBS:-$(getconf _NPROCESSORS_ONLN)}"
        make install
        printf '%s\n' "$BASE" > "$TARGET/funcgraph-uftrace.pin"
        (cd "$PATCHES"; while IFS= read -r patch; do sha256sum "$patch"; done < "$TEMP/old-series") > "$TARGET/funcgraph-patches.sha256"
        echo "uftrace executable: $TARGET/bin/uftrace"
        ;;
    refresh)
        DEV="$ROOT/build/uftrace-dev"
        [[ -e $DEV/.git && $(git -C "$DEV" rev-parse --show-toplevel) == "$DEV" &&
           $(git -C "$DEV" rev-parse --path-format=absolute --git-common-dir) == "$(git -C "$SOURCE" rev-parse --path-format=absolute --git-common-dir)" ]] || {
            echo "Create a detached uftrace worktree at $DEV first" >&2; exit 1;
        }
        [[ -z $(git -C "$DEV" status --porcelain) ]] || { echo 'Commit development changes first' >&2; exit 1; }
        git -C "$DEV" merge-base --is-ancestor "$BASE" HEAD
        [[ -z $(git -C "$DEV" rev-list --merges "$BASE..HEAD") ]] || {
            echo 'Patch development must use a linear history' >&2; exit 1;
        }
        mkdir "$TEMP/patches" "$TEMP/check"
        git -C "$DEV" format-patch --no-signature -o "$TEMP/patches" "$BASE..HEAD" >/dev/null
        find "$TEMP/patches" -maxdepth 1 -name '*.patch' -printf '%f\n' | LC_ALL=C sort > "$TEMP/patches/series"
        read_series "$TEMP/patches" > "$TEMP/new-series"
        while IFS= read -r patch; do
            [[ ! -e $PATCHES/$patch ]] || grep -Fxq "$patch" "$TEMP/old-series" || {
                echo "Refusing to overwrite unmanaged patch: $patch" >&2; exit 1;
            }
        done < "$TEMP/new-series"
        git -C "$SOURCE" archive "$BASE" | tar -x -C "$TEMP/check"
        while IFS= read -r patch; do
            git -C "$TEMP/check" apply --check "$TEMP/patches/$patch"
            git -C "$TEMP/check" apply "$TEMP/patches/$patch"
        done < "$TEMP/new-series"
        while IFS= read -r patch; do rm "$PATCHES/$patch"; done < "$TEMP/old-series"
        while IFS= read -r patch; do cp "$TEMP/patches/$patch" "$PATCHES/$patch"; done < "$TEMP/new-series"
        cp "$TEMP/patches/series" "$PATCHES/series"
        echo 'uftrace patch series refreshed and verified'
        ;;
esac
