#!/usr/bin/env bash
# Universal installer for codex-session-hooks.
#
# Two modes:
#   * From a repository checkout: runs install.py in place.
#   * Standalone (e.g. piped through curl): downloads the repository into a
#     temporary directory and runs install.py from there. Everything the
#     installer writes lands inside the target repository — the download is
#     transient because hook commands resolve via `git rev-parse`.
#
# Usage (run from inside the repo to install into):
#   curl -fsSL https://raw.githubusercontent.com/IGUNUBLUE/codex-session-hooks/main/install.sh | bash
#   curl -fsSL ... | bash -s -- --hooks openspec --skip-framework-updates
#   ./install.sh --repo /path/to/repo
#
# Environment overrides:
#   CODEX_HOOKS_REF         git ref to download (default: main; e.g. v2.0.0)
#   CODEX_HOOKS_SOURCE_URL  full tarball URL override
set -euo pipefail

REPO="IGUNUBLUE/codex-session-hooks"
REF="${CODEX_HOOKS_REF:-main}"
SOURCE_URL="${CODEX_HOOKS_SOURCE_URL:-https://github.com/${REPO}/archive/${REF}.tar.gz}"

REQUIRED_FILES=(install.py superpowers-bootstrap.py openspec-context.py)

have_repo_files() {
    local dir="$1" f
    for f in "${REQUIRED_FILES[@]}"; do
        [ -f "$dir/$f" ] || return 1
    done
}

download() {
    local url="$1" out="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$out"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$out" "$url"
    else
        echo "error: curl or wget is required to download ${REPO}" >&2
        return 1
    fi
}

script_dir=""
if [ -n "${BASH_SOURCE[0]:-}" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || script_dir=""
fi

if [ -n "$script_dir" ] && have_repo_files "$script_dir"; then
    src_dir="$script_dir"
else
    command -v tar >/dev/null 2>&1 || { echo "error: tar is required" >&2; exit 1; }

    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT
    echo "Downloading ${REPO} (${REF})..."
    download "$SOURCE_URL" "$tmp_dir/repo.tar.gz"
    mkdir "$tmp_dir/extract"
    tar -xzf "$tmp_dir/repo.tar.gz" --strip-components=1 -C "$tmp_dir/extract"
    if ! have_repo_files "$tmp_dir/extract"; then
        echo "error: downloaded archive is missing installer files" >&2
        exit 1
    fi
    src_dir="$tmp_dir/extract"
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required" >&2
    exit 1
fi

# Reattach stdin to the controlling terminal when one is usable, so the
# interactive prompts work even though this script itself arrived via a pipe.
# If /dev/tty cannot be opened there is no controlling terminal to recover.
if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then
    exec python3 "$src_dir/install.py" "$@" </dev/tty
fi
exec python3 "$src_dir/install.py" "$@"
