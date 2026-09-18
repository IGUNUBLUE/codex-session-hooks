#!/usr/bin/env bash
# Universal installer for codex-session-hooks.
#
# Two modes:
#   * From a repository checkout: runs install.py in place.
#   * Standalone (e.g. piped through curl): downloads the repository into a
#     persistent directory first, then runs install.py from there. Hook
#     definitions point at that directory, so it must not be temporary.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/IGUNUBLUE/codex-session-hooks/main/install.sh | bash
#   curl -fsSL ... | bash -s -- --hooks openspec --skip-framework-updates
#
# Environment overrides:
#   CODEX_HOOKS_HOME        install directory for standalone mode
#                           (default: ${XDG_DATA_HOME:-~/.local/share}/codex-session-hooks)
#   CODEX_HOOKS_REF         git ref to download (default: main; e.g. v1.0.0)
#   CODEX_HOOKS_SOURCE_URL  full tarball URL override
set -euo pipefail

REPO="IGUNUBLUE/codex-session-hooks"
REF="${CODEX_HOOKS_REF:-main}"
INSTALL_DIR="${CODEX_HOOKS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/codex-session-hooks}"
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
    case "$INSTALL_DIR" in
        ""|"/"|"$HOME")
            echo "error: unsafe CODEX_HOOKS_HOME: '$INSTALL_DIR'" >&2
            exit 1
            ;;
    esac

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

    mkdir -p "$(dirname "$INSTALL_DIR")"
    rm -rf "$INSTALL_DIR"
    mv "$tmp_dir/extract" "$INSTALL_DIR"
    src_dir="$INSTALL_DIR"
    echo "Installed source to $src_dir"
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required" >&2
    exit 1
fi
exec python3 "$src_dir/install.py" "$@"
