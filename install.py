#!/usr/bin/env python3
"""Install Codex SessionStart hooks and update their upstream frameworks."""

from __future__ import annotations

import argparse
import contextlib
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
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
VERSION = "1.5.2"  # released version; bump before tagging
REPO = "IGUNUBLUE/codex-session-hooks"
PREFS_FILE = "codex-session-hooks.json"
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
    except OSError:
        if sys.stdin.isatty():
            return _StdTty()
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


def latest_release_tag(timeout: int = 8) -> str | None:
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
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


def _plugin_listing(codex: str, include_available: bool = False) -> dict[str, object]:
    args = [codex, "plugin", "list"]
    if include_available:
        args.append("--available")
    args.append("--json")
    result = run_command(args)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise InstallError("Codex returned invalid plugin-list JSON") from error
    if not isinstance(data, dict):
        raise InstallError("Codex plugin list did not return a JSON object")
    return data


def _official_superpowers_entry(data: dict[str, object]) -> dict[str, object] | None:
    installed = data.get("installed", [])
    available = data.get("available", [])
    entries = [
        *(installed if isinstance(installed, list) else []),
        *(available if isinstance(available, list) else []),
    ]
    return next(
        (
            item
            for item in entries
            if isinstance(item, dict)
            and item.get("name") == "superpowers"
            and str(item.get("marketplaceName", "")).startswith("openai-curated")
        ),
        None,
    )


def update_superpowers() -> str:
    codex = require_command("codex")
    entry = _official_superpowers_entry(_plugin_listing(codex))
    if not entry:
        entry = _official_superpowers_entry(_plugin_listing(codex, include_available=True))
    selector = entry.get("pluginId") if entry else None
    if not isinstance(selector, str) or not selector:
        raise InstallError("Superpowers was not found in an official Codex marketplace")

    run_command([codex, "plugin", "add", selector, "--json"])
    entry = _official_superpowers_entry(_plugin_listing(codex))
    if not entry or entry.get("installed") is not True or entry.get("enabled") is not True:
        raise InstallError("Superpowers is not installed and enabled after `codex plugin add`")
    version = entry.get("version")
    if not isinstance(version, str) or not version:
        raise InstallError("Codex did not report the installed Superpowers version")
    print(f"Superpowers {version} is installed and enabled (latest official marketplace release).")
    return version


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


def init_openspec_project(path: Path) -> None:
    """Initialize (or refresh) OpenSpec for Codex inside a single project.

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
    run_command([openspec, "init", "--tools", "codex"], cwd=target, timeout=120)
    if not (target / "openspec" / "config.yaml").is_file():
        raise InstallError(f"openspec init did not create {target}/openspec/config.yaml")
    print(f"Initialized OpenSpec for Codex in {target}")


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


def hook_handler(script_dir: Path, hook_id: str) -> dict[str, object]:
    definition = HOOK_DEFINITIONS[hook_id]
    script = (script_dir / str(definition["script"])).resolve()
    args = [sys.executable, str(script), MARKER]
    return {
        "type": "command",
        "command": shlex.join(args),
        "commandWindows": subprocess.list2cmdline(args),
        "statusMessage": definition["status"],
        "timeout": 15,
        "additionalContextLimit": 2500,
    }


def merge_hooks(
    data: dict[str, object],
    script_dir: Path,
    install_ids: set[str],
    remove_ids: set[str] | None = None,
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
                "hooks": [hook_handler(script_dir, hook_id)],
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
        help="initialize OpenSpec for Codex in PROJECT_DIR (default: current directory); "
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
                f"Merge managed SessionStart hooks into {codex_home / 'hooks.json'}",
                "Ensure the Superpowers plugin and OpenSpec CLI are installed and current",
                "Preserve every hook it does not manage",
            ],
        )
    else:
        print(f"{PROJECT} {VERSION}")

    try:
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

        skip_updates = args.skip_framework_updates
        if not skip_updates and tty is not None and hooks:
            skip_updates = not confirm(
                tty, "Install/update the frameworks to their latest releases?", True
            )

        if not skip_updates:
            if "superpowers" in hooks:
                with Spinner("Ensuring the latest Superpowers plugin", tty):
                    update_superpowers()
            if "openspec" in hooks:
                with Spinner("Ensuring the latest OpenSpec CLI", tty):
                    update_openspec(args.openspec_package_manager)

        with Spinner("Enabling Codex hooks and merging hook definitions", tty):
            enable_codex_hooks(codex_home)
            path = codex_home / "hooks.json"
            current = load_hooks(path)
            updated = merge_hooks(current, script_dir, hooks, args.remove)
        if current == updated:
            print(f"Hooks already current: {path}")
        else:
            backup = write_hooks_atomic(path, updated)
            print(f"Updated hooks: {path}")
            if backup:
                print(f"Backup: {backup}")

        init_target = args.openspec_init
        if (
            init_target is None
            and tty is not None
            and "openspec" in hooks
            and not _flag_passed("--openspec-init")
        ):
            cwd = Path.cwd()
            if cwd != home and not (cwd / "openspec" / "config.yaml").is_file():
                if confirm(tty, f"Initialize OpenSpec for Codex in {cwd}?", False):
                    init_target = cwd
        if init_target is not None:
            with Spinner(f"Initializing OpenSpec in {init_target}", tty):
                init_openspec_project(init_target)

        message = (
            "Done — open a new Codex session, run /hooks, "
            "and trust each changed hook definition."
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
