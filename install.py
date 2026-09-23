#!/usr/bin/env python3
"""Install repo-scoped Codex SessionStart hooks and materialize framework skills."""

from __future__ import annotations

import argparse
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request

PROJECT = "codex-session-hooks"
VERSION = "2.0.0"  # released version; bump before tagging
REPO = "IGUNUBLUE/codex-session-hooks"
SUPERPOWERS_REPO = "obra/superpowers"
PREFS_FILE = "codex-session-hooks.json"
MANIFEST_NAME = ".codex-session-hooks.json"
MARKER = f"--managed-by={PROJECT}"
HOOK_DEFINITIONS = {
    "superpowers": {
        "script": "superpowers-bootstrap.py",
        "legacy_scripts": {"session-start.sh", "superpowers-bootstrap.py"},
        "status": "Loading Superpowers context",
        "legacy_statuses": {"Loading superpowers", "Loading Superpowers context"},
    },
    "openspec": {
        "script": "openspec-context.py",
        "legacy_scripts": {"openspec-detect.sh", "openspec-context.py"},
        "status": "Loading OpenSpec context",
        "legacy_statuses": {"Loading openspec context", "Loading OpenSpec context"},
    },
}
LEGACY_REPO_NAMES = {"codex-session-hooks", "codex-superpowers-hook"}
NODE_MINIMUM = (20, 19, 0)
HARNESSES: dict[str, dict[str, object]] = {
    "codex": {
        "cli": "codex",
        "adapter_rel": None,
        "openspec_tool": "codex",
    },
    "opencode": {
        "cli": "opencode",
        "adapter_rel": ".opencode/plugins/session-hooks.ts",
        "openspec_tool": "opencode",
    },
    "omp": {
        "cli": "omp",
        "adapter_rel": ".omp/extensions/session-hooks.ts",
        "openspec_tool": "oh-my-pi",
    },
}


class InstallError(RuntimeError):
    pass


def run_command(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {shlex.join(args)}")
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise InstallError(f"required command not found: {args[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise InstallError(f"command timed out: {shlex.join(args)}") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise InstallError(
            f"command failed ({error.returncode}): {shlex.join(args)}"
            + (f"\n{detail}" if detail else "")
        ) from error


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", value)
    if not match:
        raise InstallError(f"could not parse version from: {value.strip()!r}")
    return tuple(int(part) for part in match.groups())


def require_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise InstallError(f"required command not found: {name}")
    return path


class _StdTty:
    """Adapter exposing tty-style read/write over stdin+stderr."""

    def write(self, data: str) -> int:
        return sys.stderr.write(data)

    def flush(self) -> None:
        sys.stderr.flush()

    def readline(self) -> str:
        return sys.stdin.readline()

    def fileno(self) -> int:
        return sys.stdin.fileno()


def open_tty():
    """Return a read/write handle to the controlling terminal, if any.

    Prompts prefer /dev/tty so they still work when the script itself is piped
    (`curl ... | bash`), where stdin is the script rather than the keyboard.
    Fall back to stdin when it is itself a terminal (e.g. Windows, containers).
    """
    try:
        return os.fdopen(os.open("/dev/tty", os.O_RDWR), "r+")
    except OSError as error:
        if sys.stdin.isatty():
            return _StdTty()
        if os.environ.get("CODEX_HOOKS_DEBUG"):
            print(
                f"debug: no interactive terminal ({error}; stdin is not a tty)",
                file=sys.stderr,
            )
        return None


def ask(tty, question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        tty.write(f"? {question} {suffix} ")
        tty.flush()
        answer = tty.readline().strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        tty.write("Please answer y or n.\n")


# --- guided terminal UI ---------------------------------------------------
# Minimal clack-style widgets built on stdlib ANSI: intro/note/outro panels,
# single-keypress confirms, and an arrow-key multiselect. Everything degrades
# to plain line input when the terminal cannot report key presses.

_BAR = "│"
_S_ACTIVE = "◇"
_S_DONE = "◆"
_S_ON = "●"
_S_OFF = "○"
_S_CURSOR = "❯"

_COLOR = False


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if _COLOR else text


def _bar() -> str:
    return _c("36", _BAR)


def intro(tty, title: str) -> None:
    tty.write(f"{_c('36', '┌')}  {_c('1', title)}\n{_bar()}\n")
    tty.flush()


def note(tty, title: str, lines: list[str]) -> None:
    tty.write(f"{_c('36', _S_ACTIVE)}  {_c('1', title)}\n")
    for line in lines:
        tty.write(f"{_bar()}  {line}\n")
    tty.write(f"{_bar()}\n")
    tty.flush()


def outro(tty, message: str) -> None:
    tty.write(f"{_c('36', '└')}  {message}\n")
    tty.flush()


def cancel(tty, message: str) -> None:
    tty.write(f"{_c('36', '└')}  {message}\n")
    tty.flush()


def _supports_keys(tty) -> bool:
    """True when the tty can report individual key presses (POSIX termios)."""
    try:
        import termios  # noqa: F401
    except ImportError:
        return False
    try:
        return os.isatty(tty.fileno())
    except (AttributeError, OSError):
        return False


@contextlib.contextmanager
def _cbreak(fd: int):
    """Put fd in cbreak mode: unbuffered, unechoed input; cooked output.

    cbreak (unlike raw) keeps OPOST, so "\\n" still prints as CR+LF.
    """
    import termios
    import tty as tty_module

    old = termios.tcgetattr(fd)
    try:
        tty_module.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


_ESCAPE_KEYS = {"[A": "up", "[B": "down", "[C": "right", "[D": "left",
                "OA": "up", "OB": "down"}


def _read_key(fd: int) -> str:
    """Read one key press from a cbreak fd. Returns 'up', 'down', 'enter',
    'space', 'esc', or the decoded character."""
    ch = os.read(fd, 1)
    if ch == b"\x03":
        raise KeyboardInterrupt
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b" ":
        return "space"
    if ch == b"\x1b":
        if select.select([fd], [], [], 0.05)[0]:
            return _ESCAPE_KEYS.get(os.read(fd, 2).decode(errors="replace"), "esc")
        return "esc"
    return ch.decode(errors="replace").lower()


def confirm(tty, question: str, default: bool = True) -> bool:
    """Single-keypress y/n confirm; falls back to line input without termios."""
    if not _supports_keys(tty):
        return ask(tty, question, default)
    suffix = "[Y/n]" if default else "[y/N]"
    tty.write(f"{_c('36', _S_ACTIVE)}  {question} {_c('2', suffix)} ")
    tty.flush()
    with _cbreak(tty.fileno()):
        while True:
            key = _read_key(tty.fileno())
            if key == "enter":
                answer = default
                break
            if key == "y":
                answer = True
                break
            if key == "n":
                answer = False
                break
    word = "yes" if answer else "no"
    tty.write(f"\r\x1b[2K{_c('36', _S_DONE)}  {question} {_c('2', word)}\n")
    tty.flush()
    return answer


class _Menu:
    """Cursor/toggle state for a multiselect; kept tty-free for testing."""

    def __init__(self, count: int, chosen):
        self.count = count
        self.cursor = 0
        self.chosen = set(chosen)
        self.done = False

    def press(self, key: str) -> None:
        if key in ("up", "k"):
            self.cursor = (self.cursor - 1) % self.count
        elif key in ("down", "j"):
            self.cursor = (self.cursor + 1) % self.count
        elif key == "space":
            self.chosen ^= {self.cursor}
        elif key == "a":
            self.chosen = set() if len(self.chosen) == self.count else set(range(self.count))
        elif key == "enter":
            self.done = True


def _render_menu(question: str, options: list[tuple[str, str, str]], menu: _Menu) -> list[str]:
    lines = [f"{_c('36', _S_ACTIVE)}  {_c('1', question)}"]
    for i, (_key, label, hint) in enumerate(options):
        box = _S_ON if i in menu.chosen else _S_OFF
        entry = f"{label}{_c('2', '  ' + hint) if hint else ''}"
        if i == menu.cursor:
            lines.append(f"{_bar()}  {_c('36', _S_CURSOR + ' ' + box)} {_c('1', label)}{_c('2', '  ' + hint) if hint else ''}")
        else:
            lines.append(f"{_bar()}    {box} {entry}")
    lines.append(f"{_bar()}  {_c('2', '↑/↓ move · space toggle · a all · enter confirm')}")
    return lines


def _write_lines(tty, lines: list[str]) -> None:
    for line in lines:
        tty.write("\x1b[2K" + line + "\n")
    tty.flush()


def _redraw(tty, lines: list[str]) -> None:
    tty.write(f"\x1b[{len(lines)}A")
    _write_lines(tty, lines)


def _clear_lines(tty, count: int) -> None:
    tty.write(f"\x1b[{count}A")
    for _ in range(count):
        tty.write("\x1b[2K\x1b[1B")
    tty.write(f"\x1b[{count}A")
    tty.flush()


def multiselect(tty, question: str, options: list[tuple[str, str, str]], preselected) -> set[str]:
    """options: (key, label, hint). Returns the chosen keys.

    Arrow keys move, space toggles, 'a' toggles all, enter confirms. Without
    termios support it asks one y/n question per option instead."""
    if not _supports_keys(tty):
        return {
            key
            for key, label, _hint in options
            if ask(tty, f"Install the '{label}' SessionStart hook?", True)
        }
    menu = _Menu(len(options), preselected)
    fd = tty.fileno()
    lines = _render_menu(question, options, menu)
    _write_lines(tty, lines)
    try:
        tty.write("\x1b[?25l")
        tty.flush()
        with _cbreak(fd):
            while not menu.done:
                menu.press(_read_key(fd))
                if not menu.done:
                    _redraw(tty, _render_menu(question, options, menu))
    finally:
        tty.write("\x1b[?25h")
    _clear_lines(tty, len(lines))
    names = ", ".join(options[i][1] for i in sorted(menu.chosen)) or "none"
    tty.write(f"{_c('36', _S_DONE)}  {question}: {_c('1', names)}\n")
    tty.flush()
    return {options[i][0] for i in menu.chosen}


class Spinner:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str, tty):
        self.label = label
        self.tty = tty
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if self.tty is None:
            print(self.label)
        else:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self) -> None:
        i = 0
        while not self._done.is_set():
            self.tty.write(f"\r{self.FRAMES[i % len(self.FRAMES)]} {self.label}")
            self.tty.flush()
            i += 1
            self._done.wait(0.08)

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        if self.tty is not None:
            mark = _c("31", "x") if exc_type else _c("32", "ok")
            self.tty.write(f"\r[{mark}] {self.label}\n")
            self.tty.flush()
        return False


def _flag_passed(name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in sys.argv[1:])


def latest_release_tag(repo: str = REPO, timeout: int = 8) -> str | None:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(url, headers={"User-Agent": PROJECT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode())
    except (OSError, ValueError):
        return None
    tag = data.get("tag_name")
    return tag if isinstance(tag, str) else None


def _version_of(tag: str) -> tuple[int, int, int] | None:
    try:
        return parse_version(tag.lstrip("v"))
    except InstallError:
        return None


def is_newer(tag: str, current: str) -> bool:
    remote, local = _version_of(tag), _version_of(current)
    return remote is not None and local is not None and remote > local


def load_prefs(codex_home: Path) -> dict:
    try:
        data = json.loads((codex_home / PREFS_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(codex_home: Path, prefs: dict) -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / PREFS_FILE).write_text(json.dumps(prefs, indent=2) + "\n")


def self_update(script_dir: Path, tag: str) -> None:
    """Update the running installation to a release tag."""
    if (script_dir / ".git").exists():
        git = require_command("git")
        run_command([git, "-C", str(script_dir), "pull", "--ff-only"], timeout=120)
        return
    url = f"https://github.com/{REPO}/archive/{tag}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "repo.tar.gz"
        try:
            urllib.request.urlretrieve(url, archive)
        except OSError as error:
            raise InstallError(f"could not download {url}: {error}") from error
        extract = Path(tmp) / "extract"
        extract.mkdir()
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(extract, filter="data")
            except TypeError:
                tar.extractall(extract)
        roots = list(extract.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise InstallError("downloaded archive had an unexpected layout")
        for entry in list(script_dir.iterdir()):
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        for entry in roots[0].iterdir():
            shutil.move(str(entry), script_dir / entry.name)


def skills_dir(repo_root: Path) -> Path:
    return repo_root / ".agents" / "skills"


def load_manifest(repo_root: Path) -> dict:
    path = skills_dir(repo_root) / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_manifest(repo_root: Path, data: dict) -> None:
    path = skills_dir(repo_root) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _download(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": PROJECT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _extract_skills(payload: bytes, staging: Path) -> list[str]:
    """Extract <top>/skills/<name>/** members into staging/<name>; returns names."""
    names: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if len(parts) < 3 or parts[1] != "skills" or ".." in parts:
                continue
            if not (member.isfile() or member.isdir()):
                continue  # skip links/devices: nothing escapes staging
            rel = Path(*parts[2:])  # <name>/...
            target = staging / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is not None:
                with extracted as src:
                    target.write_bytes(src.read())
            names.add(parts[2])
    return sorted(names)


def materialize_superpowers(repo_root: Path, ref: str | None = None) -> str:
    ref = ref or latest_release_tag(SUPERPOWERS_REPO)
    if not ref:
        raise InstallError(
            f"could not resolve latest {SUPERPOWERS_REPO} release; "
            "pass --superpowers-ref"
        )
    url = f"https://github.com/{SUPERPOWERS_REPO}/archive/{ref}.tar.gz"
    try:
        payload = _download(url)
    except (OSError, ValueError) as error:
        raise InstallError(f"failed to download {url}: {error}") from error

    dest = skills_dir(repo_root)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        names = _extract_skills(payload, staging)
        if not (staging / "using-superpowers" / "SKILL.md").is_file():
            raise InstallError(
                f"{ref} tarball did not contain using-superpowers/SKILL.md"
            )
        dest.mkdir(parents=True, exist_ok=True)
        for name in names:  # adopts pre-existing same-name dirs (same upstream skill)
            target = dest / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staging / name, target)

    previous = set(load_manifest(repo_root).get("superpowers", {}).get("skills", []))
    for stale in previous - set(names):
        target = dest / stale
        if target.is_dir():
            shutil.rmtree(target)
    manifest = load_manifest(repo_root)
    manifest["managed-by"] = PROJECT
    manifest["superpowers"] = {"ref": ref, "skills": names}
    save_manifest(repo_root, manifest)
    print(f"Superpowers {ref} materialized into {dest} ({len(names)} skills).")
    return ref


def remove_superpowers(repo_root: Path) -> bool:
    manifest = load_manifest(repo_root)
    removed = False
    for name in manifest.get("superpowers", {}).get("skills", []):
        target = skills_dir(repo_root) / name
        if target.is_dir():
            shutil.rmtree(target)
            removed = True
    manifest_path = skills_dir(repo_root) / MANIFEST_NAME
    if manifest_path.is_file():
        manifest_path.unlink()
        removed = True
    return removed


GITIGNORE_BEGIN = "# >>> codex-session-hooks >>>"
GITIGNORE_END = "# <<< codex-session-hooks <<<"


def gitignore_entries(repo_root: Path, harnesses: set[str] | None = None) -> list[str]:
    selected = {"codex"} if harnesses is None else harnesses
    entries = [
        "/.agents/session-hooks/",
        "/.agents/skills/openspec-*/",
        f"/.agents/skills/{MANIFEST_NAME}",
    ]
    if "codex" in selected:
        entries.insert(0, "/.codex/hooks.json")
    for harness in ("opencode", "omp"):
        if harness in selected:
            entries.append("/" + str(HARNESSES[harness]["adapter_rel"]))
    manifest = load_manifest(repo_root)
    entries += [
        f"/.agents/skills/{name}/"
        for name in manifest.get("superpowers", {}).get("skills", [])
    ]
    return entries


def _replace_gitignore_block(text: str, entries: list[str] | None) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    found = False
    for line in lines:
        if line.strip() == GITIGNORE_BEGIN:
            inside = True
            found = True
            if entries:
                out.append(GITIGNORE_BEGIN + "\n")
                out.extend(entry + "\n" for entry in entries)
            continue
        if inside and line.strip() == GITIGNORE_END:
            inside = False
            if entries:
                out.append(GITIGNORE_END + "\n")
            continue
        if not inside:
            out.append(line)
    if not found and entries:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if out and out[-1].strip():
            out.append("\n")
        out.append(GITIGNORE_BEGIN + "\n")
        out.extend(entry + "\n" for entry in entries)
        out.append(GITIGNORE_END + "\n")
    return "".join(out)


def ensure_gitignore(repo_root: Path, harnesses: set[str] | None = None) -> bool:
    path = repo_root / ".gitignore"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    new = _replace_gitignore_block(old, gitignore_entries(repo_root, harnesses))
    if new == old:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def strip_gitignore(repo_root: Path) -> bool:
    path = repo_root / ".gitignore"
    if not path.exists():
        return False
    old = path.read_text(encoding="utf-8")
    new = _replace_gitignore_block(old, None)
    if new == old:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def _probe_output(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def detect_openspec_manager(requested: str) -> str:
    if requested != "auto":
        return requested

    executable_name = shutil.which("openspec")
    executable = Path(executable_name) if executable_name else None
    volta = shutil.which("volta")
    if executable and volta:
        volta_home = Path(os.environ.get("VOLTA_HOME", Path.home() / ".volta"))
        if _is_under(executable, volta_home):
            return "volta"

    ownership_probes = (
        ("npm", ["npm", "prefix", "--global"]),
        ("pnpm", ["pnpm", "bin", "--global"]),
        ("bun", ["bun", "pm", "bin", "--global"]),
        ("yarn", ["yarn", "global", "bin"]),
    )
    if executable:
        for manager, probe in ownership_probes:
            if not shutil.which(manager):
                continue
            location = _probe_output(probe)
            if location and _is_under(executable, Path(location)):
                return manager

    for manager in ("npm", "pnpm", "bun", "yarn", "volta"):
        if shutil.which(manager):
            return manager
    raise InstallError(
        "no supported OpenSpec package manager found (npm, pnpm, bun, Yarn 1, or volta)"
    )


def registry_openspec_version(manager: str) -> str | None:
    commands = {
        "volta": ["npm", "view", "@fission-ai/openspec", "version"],
        "npm": ["npm", "view", "@fission-ai/openspec", "version"],
        "pnpm": ["pnpm", "view", "@fission-ai/openspec", "version"],
        "bun": ["npm", "view", "@fission-ai/openspec", "version"],
        "yarn": ["yarn", "info", "@fission-ai/openspec", "version", "--silent"],
    }
    command = commands[manager]
    if not shutil.which(command[0]):
        return None
    result = run_command(command, timeout=60)
    match = re.search(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", result.stdout)
    return match.group(0) if match else None


def update_openspec(manager: str = "auto") -> str:
    node = require_command("node")
    node_version = run_command([node, "--version"]).stdout.strip()
    if parse_version(node_version) < NODE_MINIMUM:
        minimum = ".".join(map(str, NODE_MINIMUM))
        raise InstallError(f"OpenSpec requires Node.js >= {minimum}; found {node_version}")

    manager = detect_openspec_manager(manager)
    package = "@fission-ai/openspec@latest"
    commands = {
        "volta": ["volta", "install", package],
        "npm": ["npm", "install", "--global", package],
        "pnpm": ["pnpm", "add", "--global", package],
        "bun": ["bun", "add", "--global", package],
        "yarn": ["yarn", "global", "add", package],
    }
    if manager == "yarn":
        yarn_version = run_command(["yarn", "--version"]).stdout.strip()
        if parse_version(yarn_version)[0] != 1:
            raise InstallError("OpenSpec global installation requires Yarn 1.x")

    expected = registry_openspec_version(manager)
    run_command(commands[manager], timeout=300)
    openspec = require_command("openspec")
    installed_output = run_command([openspec, "--version"]).stdout.strip()
    installed = re.search(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", installed_output)
    if not installed:
        raise InstallError(f"could not parse `openspec --version`: {installed_output!r}")
    version = installed.group(0)
    if expected and version != expected:
        raise InstallError(
            f"OpenSpec update did not reach the registry latest version: expected {expected}, "
            f"found {version}. Check PATH for a shadowed installation."
        )
    print(f"OpenSpec {version} is installed ({manager}, latest registry release).")
    return version


def openspec_tools(harnesses: set[str]) -> str:
    return ",".join(
        str(HARNESSES[harness]["openspec_tool"])
        for harness in HARNESSES
        if harness in harnesses
    )


def init_openspec_project(path: Path, tools: str = "codex") -> None:
    """Initialize (or refresh) OpenSpec inside a single project.

    Deliberately explicit per project: `openspec init` writes files into the
    working tree, so it must never run implicitly at session time.
    """
    openspec = require_command("openspec")
    target = path.expanduser().resolve()
    if not target.is_dir():
        raise InstallError(f"--openspec-init target is not a directory: {target}")
    if (target / "openspec" / "config.yaml").is_file():
        run_command([openspec, "update", "--force"], cwd=target, timeout=120)
        print(f"Refreshed OpenSpec integration in {target}")
        return
    run_command([openspec, "init", "--tools", tools], cwd=target, timeout=120)
    if not (target / "openspec" / "config.yaml").is_file():
        raise InstallError(f"openspec init did not create {target}/openspec/config.yaml")
    print(f"Initialized OpenSpec ({tools}) in {target}")


def _command_tokens(command: object) -> list[str]:
    if not isinstance(command, str):
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return []


def managed_hook_id(handler: object) -> str | None:
    if not isinstance(handler, dict):
        return None
    tokens = _command_tokens(handler.get("command"))
    if MARKER in tokens:
        for hook_id, definition in HOOK_DEFINITIONS.items():
            if any(Path(token).name == definition["script"] for token in tokens):
                return hook_id
        return None

    status_message = handler.get("statusMessage")
    for hook_id, definition in HOOK_DEFINITIONS.items():
        if status_message not in definition["legacy_statuses"]:
            continue
        for token in tokens:
            path = Path(token)
            if (
                path.name in definition["legacy_scripts"]
                and any(parent.name in LEGACY_REPO_NAMES for parent in path.parents)
            ):
                return hook_id
    return None


def hook_handler(
    repo_root: Path, hook_id: str, git_rooted: bool = True
) -> dict[str, object]:
    definition = HOOK_DEFINITIONS[hook_id]
    script_rel = f".agents/session-hooks/{definition['script']}"
    script_abs = (repo_root / script_rel).resolve()
    if git_rooted:
        command = (
            f"{shlex.quote(sys.executable)} "
            f'"$(git rev-parse --show-toplevel)/{script_rel}" {MARKER}'
        )
    else:
        command = shlex.join([sys.executable, str(script_abs), MARKER])
    return {
        "type": "command",
        "command": command,
        "commandWindows": subprocess.list2cmdline(
            [sys.executable, str(script_abs), MARKER]
        ),
        "statusMessage": definition["status"],
        "timeout": 15,
        "additionalContextLimit": 2500,
    }


def prompt_text(tty, question: str) -> str:
    tty.write(f"{_bar()} {question}: ")
    tty.flush()
    line = tty.readline()
    if not line:
        raise InstallError("input closed")
    return line.strip()


def resolve_repo_root(
    repo_arg: Path | None, tty, yes: bool
) -> tuple[Path, bool]:
    if repo_arg is not None:
        root = repo_arg.expanduser().resolve()
        if not root.is_dir():
            raise InstallError(f"--repo target is not a directory: {root}")
    else:
        probe = (
            _probe_output(["git", "rev-parse", "--show-toplevel"])
            if shutil.which("git")
            else None
        )
        if probe:
            return Path(probe.strip()).resolve(), True
        if tty is not None and not yes:
            entered = prompt_text(tty, "Repository directory to install into")
            if not entered:
                raise InstallError("no repository given")
            root = Path(entered).expanduser().resolve()
            if not root.is_dir():
                raise InstallError(f"not a directory: {root}")
        else:
            raise InstallError(
                "not inside a git repository; rerun inside one or pass --repo DIR"
            )
    inside = _probe_output(["git", "-C", str(root), "rev-parse", "--show-toplevel"])
    git_rooted = bool(inside) and Path(inside.strip()).resolve() == root
    return root, git_rooted


def session_hooks_dir(repo_root: Path) -> Path:
    return repo_root / ".agents" / "session-hooks"


def install_hook_scripts(repo_root: Path, hook_ids: set[str]) -> None:
    dest_dir = session_hooks_dir(repo_root)
    if hook_ids:
        dest_dir.mkdir(parents=True, exist_ok=True)
    source_dir = Path(__file__).resolve().parent
    for hook_id in hook_ids:
        shutil.copy2(
            source_dir / HOOK_DEFINITIONS[hook_id]["script"],
            dest_dir / HOOK_DEFINITIONS[hook_id]["script"],
        )
    remove_hook_scripts(repo_root, set(HOOK_DEFINITIONS) - set(hook_ids))


def remove_hook_scripts(repo_root: Path, remove_ids: set[str]) -> None:
    dest_dir = session_hooks_dir(repo_root)
    for hook_id in remove_ids:
        stale = dest_dir / HOOK_DEFINITIONS[hook_id]["script"]
        if stale.is_file():
            stale.unlink()
    if dest_dir.is_dir() and not any(dest_dir.iterdir()):
        dest_dir.rmdir()


def migrate_legacy_hook_dir(repo_root: Path) -> None:
    legacy = repo_root / ".codex" / "hooks"
    for definition in HOOK_DEFINITIONS.values():
        stale = legacy / definition["script"]
        if stale.is_file():
            stale.unlink()
    if legacy.is_dir() and not any(legacy.iterdir()):
        legacy.rmdir()


MARKER_TS = "// managed-by: codex-session-hooks"

OPENCODE_ADAPTER_TEMPLATE = """{marker}
import {{ Plugin }} from "@opencode/plugin"
import {{ spawnSync }} from "node:child_process"

const SCRIPTS = {scripts}

function runHandlers(dir: string): string[] {{
  const out: string[] = []
  for (const name of SCRIPTS) {{
    try {{
      const res = spawnSync(
        "python3",
        [`${{dir}}/.agents/session-hooks/${{name}}`, "--managed-by=codex-session-hooks"],
        {{ cwd: dir, encoding: "utf8", timeout: 15000 }},
      )
      const text = (res.stdout ?? "").trim()
      if (res.status === 0 && text) out.push(text)
    }} catch {{}}
  }}
  return out
}}

export default Plugin.define({{
  id: "codex-session-hooks",
  async setup(ctx) {{
    const contexts = runHandlers(ctx.location.directory)
    if (!contexts.length) return
    await ctx.session.hook("context", (event) => {{
      for (const text of contexts) event.system.push({{ type: "text", text }})
    }})
  }},
}})
"""

OMP_ADAPTER_TEMPLATE = """{marker}
import type {{ ExtensionAPI }} from "@oh-my-pi/pi-coding-agent";
import {{ spawnSync }} from "node:child_process";

const SCRIPTS = {scripts};
const CUSTOM_TYPE = "codex-session-hooks-context";
let cached: string[] | null = null;

function loadContexts(cwd: string): string[] {{
  const out: string[] = [];
  for (const name of SCRIPTS) {{
    try {{
      const res = spawnSync(
        "python3",
        [`${{cwd}}/.agents/session-hooks/${{name}}`, "--managed-by=codex-session-hooks"],
        {{ cwd, encoding: "utf8", timeout: 15000 }},
      );
      const text = (res.stdout ?? "").trim();
      if (res.status === 0 && text) out.push(text);
    }} catch {{}}
  }}
  return out;
}}

export default function (pi: ExtensionAPI) {{
  pi.on("context", async (event, ctx) => {{
    if (cached === null) cached = loadContexts(ctx.cwd);
    if (!cached.length) return;
    const present = event.messages.some(
      (m: any) => m.role === "custom" && m.customType === CUSTOM_TYPE,
    );
    if (present) return;
    return {{
      messages: [
        {{
          role: "custom",
          customType: CUSTOM_TYPE,
          content: [{{ type: "text", text: cached.join("\\n\\n") }}],
          display: false,
        }},
        ...event.messages,
      ],
    }};
  }});
}}
"""


def _adapter_scripts(hook_ids: set[str]) -> str:
    names = sorted(HOOK_DEFINITIONS[hook_id]["script"] for hook_id in hook_ids)
    return "[" + ", ".join(json.dumps(name) for name in names) + "]"


def _write_adapter(repo_root: Path, harness: str, template: str, hook_ids: set[str]) -> Path:
    path = repo_root / str(HARNESSES[harness]["adapter_rel"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        template.format(marker=MARKER_TS, scripts=_adapter_scripts(hook_ids)),
        encoding="utf-8",
    )
    return path


def write_opencode_adapter(repo_root: Path, hook_ids: set[str]) -> Path:
    return _write_adapter(repo_root, "opencode", OPENCODE_ADAPTER_TEMPLATE, hook_ids)


def write_omp_adapter(repo_root: Path, hook_ids: set[str]) -> Path:
    return _write_adapter(repo_root, "omp", OMP_ADAPTER_TEMPLATE, hook_ids)


def remove_managed_adapter(path: Path) -> bool:
    if not path.is_file():
        return False
    if MARKER_TS not in path.read_text(encoding="utf-8", errors="replace"):
        return False
    path.unlink()
    return True


def apply_harnesses(
    repo_root: Path,
    harnesses: set[str],
    hook_ids: set[str],
    remove_ids: set[str],
    git_rooted: bool,
    codex_home: Path,
) -> None:
    migrate_legacy_hook_dir(repo_root)
    install_hook_scripts(repo_root, hook_ids)
    remove_hook_scripts(repo_root, remove_ids)

    writers = {"opencode": write_opencode_adapter, "omp": write_omp_adapter}
    for harness, writer in writers.items():
        path = repo_root / str(HARNESSES[harness]["adapter_rel"])
        if harness in harnesses and hook_ids:
            writer(repo_root, hook_ids)
        else:
            remove_managed_adapter(path)

    hooks_path = repo_root / ".codex" / "hooks.json"
    if "codex" in harnesses:
        enable_codex_hooks(codex_home)
        install_ids, drop_ids = hook_ids, remove_ids
    elif hooks_path.is_file():
        # codex deselected: strip managed definitions, keep foreign ones
        install_ids, drop_ids = set(), set(HOOK_DEFINITIONS)
    else:
        return
    current = load_hooks(hooks_path)
    updated = merge_hooks(current, repo_root, install_ids, drop_ids, git_rooted)
    if current == updated:
        print(f"Hooks already current: {hooks_path}")
        return
    backup = write_hooks_atomic(hooks_path, updated)
    print(f"Updated hooks: {hooks_path}")
    if backup:
        print(f"Backup: {backup}")


def merge_hooks(
    data: dict[str, object],
    repo_root: Path,
    install_ids: set[str],
    remove_ids: set[str] | None = None,
    git_rooted: bool = True,
) -> dict[str, object]:
    result = deepcopy(data)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("hooks.json field `hooks` must be an object")
    session_start = hooks.setdefault("SessionStart", [])
    if not isinstance(session_start, list):
        raise InstallError("hooks.json field `hooks.SessionStart` must be an array")

    managed_ids = install_ids | (remove_ids or set())
    retained_groups: list[object] = []
    for group in session_start:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            retained_groups.append(group)
            continue
        retained_handlers = [
            handler
            for handler in group["hooks"]
            if managed_hook_id(handler) not in managed_ids
        ]
        if retained_handlers:
            retained_group = deepcopy(group)
            retained_group["hooks"] = retained_handlers
            retained_groups.append(retained_group)

    for hook_id in sorted(install_ids):
        retained_groups.append(
            {
                "matcher": "^(startup|clear|compact)$",
                "hooks": [hook_handler(repo_root, hook_id, git_rooted)],
            }
        )
    hooks["SessionStart"] = retained_groups
    return result


def write_hooks_atomic(path: Path, data: dict[str, object]) -> Path | None:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if old == rendered:
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = path.with_name(f"{path.name}.bak.{stamp}")
        shutil.copy2(path, backup)

    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return backup


def load_hooks(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"hooks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise InstallError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise InstallError(f"{path} must contain a JSON object")
    return data


def find_global_managed(codex_home: Path) -> set[str]:
    path = codex_home / "hooks.json"
    if not path.exists():
        return set()
    try:
        data = load_hooks(path)
    except InstallError:
        return set()
    found: set[str] = set()
    for group in data.get("hooks", {}).get("SessionStart", []):
        if not isinstance(group, dict):
            continue
        for handler in group.get("hooks", []):
            hook_id = managed_hook_id(handler)
            if hook_id:
                found.add(hook_id)
    return found


def cleanup_global_hooks(codex_home: Path, tty, yes: bool) -> bool:
    found = find_global_managed(codex_home)
    if not found:
        return False
    path = codex_home / "hooks.json"
    if not yes:
        if tty is None:
            print(f"note: managed hooks remain in {path} (rerun with -y to remove)")
            return False
        if not confirm(tty, f"Remove managed hooks from {path}?", True):
            return False
    data = load_hooks(path)
    updated = merge_hooks(data, Path("/"), set(), found)
    if updated != data:
        backup = write_hooks_atomic(path, updated)
        print(
            f"Removed managed hooks from {path}"
            + (f" (backup: {backup})" if backup else "")
        )
    return True


def enable_codex_hooks(codex_home: Path) -> None:
    codex = require_command("codex")
    codex_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    run_command([codex, "features", "enable", "hooks"], env=env)


def parse_hook_ids(value: str) -> set[str]:
    values = {item.strip() for item in value.split(",") if item.strip()}
    if values == {"all"}:
        return set(HOOK_DEFINITIONS)
    if values == {"none"}:
        return set()
    unknown = values - set(HOOK_DEFINITIONS)
    if not values or unknown:
        valid = ", ".join([*HOOK_DEFINITIONS, "all", "none"])
        raise argparse.ArgumentTypeError(f"choose a comma-separated subset of: {valid}")
    return values


def parse_harness_ids(value: str) -> set[str]:
    values = {item.strip() for item in value.split(",") if item.strip()}
    if values == {"all"}:
        return set(HARNESSES)
    if values == {"none"}:
        return set()
    unknown = values - set(HARNESSES)
    if not values or unknown:
        valid = ", ".join([*HARNESSES, "all", "none"])
        raise argparse.ArgumentTypeError(f"choose a comma-separated subset of: {valid}")
    return values


def detect_harnesses() -> set[str]:
    return {
        harness
        for harness, meta in HARNESSES.items()
        if shutil.which(str(meta["cli"]))
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install safe Codex SessionStart context hooks and update selected upstream "
            "frameworks to their latest official releases."
        )
    )
    parser.add_argument(
        "--hooks",
        type=parse_hook_ids,
        default=None,
        help="hooks to install: superpowers, openspec, all, or none (default: all; "
        "prompted interactively when a terminal is available)",
    )
    parser.add_argument(
        "--remove",
        type=parse_hook_ids,
        default=set(),
        help="managed hooks to remove without affecting unrelated hooks",
    )
    parser.add_argument(
        "--harness",
        type=parse_harness_ids,
        default=None,
        help="harnesses to install for: codex, opencode, omp, all, or none "
        "(default: harnesses detected in PATH; prompted interactively)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="non-interactive: accept defaults for every prompt",
    )
    parser.add_argument(
        "--skip-update-check",
        action="store_true",
        help="do not check GitHub for a newer installer release",
    )
    parser.add_argument(
        "--skip-framework-updates",
        action="store_true",
        help="do not install/update Superpowers or OpenSpec",
    )
    parser.add_argument(
        "--openspec-package-manager",
        choices=("auto", "volta", "npm", "pnpm", "bun", "yarn"),
        default="auto",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        metavar="DIR",
        help="target repository (default: git root of the current directory)",
    )
    parser.add_argument(
        "--superpowers-ref",
        default=None,
        metavar="REF",
        help=f"{SUPERPOWERS_REPO} git ref to materialize (default: latest release)",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=None,
        help="override CODEX_HOME (primarily for testing)",
    )
    parser.add_argument(
        "--openspec-init",
        nargs="?",
        const=Path("."),
        type=Path,
        default=None,
        metavar="PROJECT_DIR",
        help="initialize OpenSpec for Codex in PROJECT_DIR (default: the target repo); "
        "refreshes the integration when the project is already initialized",
    )
    args = parser.parse_args()

    home = Path(os.environ.get("HOME", str(Path.home())))
    codex_home = (args.codex_home or Path(os.environ.get("CODEX_HOME", home / ".codex"))).expanduser()
    script_dir = Path(__file__).resolve().parent
    tty = None if args.yes else open_tty()
    if (
        tty is not None
        and "NO_COLOR" not in os.environ
        and os.environ.get("TERM", "") != "dumb"
    ):
        global _COLOR
        _COLOR = True

    if tty is not None:
        intro(tty, f"{PROJECT} {VERSION}")
        note(
            tty,
            "What this installer does",
            [
                "Merge managed SessionStart hooks into a repo's .codex/hooks.json",
                "Materialize Superpowers skills into .agents/skills; keep the "
                "OpenSpec CLI current",
                "Preserve every hook it does not manage",
            ],
        )
    else:
        print(f"{PROJECT} {VERSION}")

    try:
        repo_root, git_rooted = resolve_repo_root(args.repo, tty, args.yes)
        if tty is not None:
            note(
                tty,
                "Target",
                [
                    f"Install SessionStart hooks and skills into {repo_root}",
                    "Nothing is written to ~/.codex/hooks.json",
                ],
            )
            if not confirm(tty, f"Install into {repo_root}?", True):
                cancel(tty, "Aborted.")
                return 0
        else:
            print(f"target repo: {repo_root}")

        prefs = load_prefs(codex_home)
        if tty is not None and "auto_update" not in prefs:
            prefs["auto_update"] = confirm(
                tty, "Check for installer updates automatically on future runs?", True
            )
            save_prefs(codex_home, prefs)

        if not args.skip_update_check:
            latest = latest_release_tag()
            if latest and is_newer(latest, VERSION):
                if prefs.get("auto_update") or (
                    tty is not None
                    and confirm(tty, f"Update the installer {VERSION} -> {latest}?", True)
                ):
                    with Spinner(f"Updating installer to {latest}", tty):
                        self_update(script_dir, latest)
                    os.execv(
                        sys.executable,
                        [sys.executable, str(script_dir / "install.py"), *sys.argv[1:]],
                    )
                else:
                    print(f"note: {latest} is available (running {VERSION})")

        hooks = args.hooks
        if hooks is None:
            if tty is not None and not _flag_passed("--remove"):
                options = [
                    ("superpowers", "superpowers", "inject the Superpowers bootstrap every session"),
                    ("openspec", "openspec", "inject OpenSpec context inside spec-driven projects"),
                ]
                hooks = multiselect(
                    tty, "SessionStart hooks to install", options, range(len(options))
                )
            elif _flag_passed("--remove"):
                hooks = set()
            else:
                hooks = set(HOOK_DEFINITIONS)

        overlap = hooks & args.remove
        if overlap:
            parser.error(
                f"cannot install and remove the same hook: {', '.join(sorted(overlap))}"
            )

        harnesses = args.harness
        if harnesses is None:
            detected = detect_harnesses()
            if tty is not None and not _flag_passed("--remove"):
                options = [
                    ("codex", "codex", "OpenAI Codex CLI (.codex/hooks.json)"),
                    ("opencode", "opencode", "OpenCode (.opencode/plugins/)"),
                    ("omp", "omp", "oh-my-pi (.omp/extensions/)"),
                ]
                preselected = [
                    index
                    for index, (harness, _, _) in enumerate(options)
                    if harness in detected
                ]
                harnesses = multiselect(
                    tty, "Harnesses to install for", options, preselected
                )
            else:
                harnesses = detected or {"codex"}
        else:
            for harness in sorted(harnesses):
                cli = str(HARNESSES[harness]["cli"])
                if not shutil.which(cli):
                    print(
                        f"warning: {harness} selected but `{cli}` is not in PATH",
                        file=sys.stderr,
                    )

        skip_updates = args.skip_framework_updates
        if not skip_updates and tty is not None and hooks:
            skip_updates = not confirm(
                tty, "Install/update the frameworks to their latest releases?", True
            )

        if not skip_updates:
            if "superpowers" in hooks:
                with Spinner("Materializing Superpowers skills", tty):
                    materialize_superpowers(repo_root, args.superpowers_ref)
            if "openspec" in hooks:
                with Spinner("Ensuring the latest OpenSpec CLI", tty):
                    update_openspec(args.openspec_package_manager)

        with Spinner("Installing session hooks", tty):
            if "superpowers" in args.remove:
                remove_superpowers(repo_root)
            apply_harnesses(
                repo_root, harnesses, hooks, args.remove, git_rooted, codex_home
            )

        if not _flag_passed("--remove"):
            keep_local = tty is None or confirm(
                tty, "Keep generated files local (add .gitignore entries)?", True
            )
            if keep_local:
                ensure_gitignore(repo_root, harnesses)
            else:
                strip_gitignore(repo_root)

        init_target = args.openspec_init
        if init_target == Path("."):
            init_target = repo_root
        if (
            init_target is None
            and tty is not None
            and "openspec" in hooks
            and not _flag_passed("--openspec-init")
        ):
            if not (repo_root / "openspec" / "config.yaml").is_file():
                if confirm(
                    tty,
                    f"Initialize OpenSpec ({openspec_tools(harnesses)}) in {repo_root}?",
                    False,
                ):
                    init_target = repo_root
        if init_target is not None:
            with Spinner(f"Initializing OpenSpec in {init_target}", tty):
                init_openspec_project(init_target, openspec_tools(harnesses))

        cleanup_global_hooks(codex_home, tty, args.yes)

        message = f"Done — session hooks installed for {', '.join(sorted(harnesses))} in {repo_root}."
        if "codex" in harnesses:
            message += (
                " Open a Codex session there, trust the project, "
                "run /hooks, and approve each hook definition."
            )
        if tty is not None:
            outro(tty, message)
        else:
            print(message)
    except KeyboardInterrupt:
        if tty is not None:
            cancel(tty, "Aborted.")
        else:
            print("\nAborted.", file=sys.stderr)
        return 130
    except InstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
