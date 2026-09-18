#!/usr/bin/env python3
"""Install Codex SessionStart hooks and update their upstream frameworks."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

PROJECT = "codex-session-hooks"
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
        default=set(HOOK_DEFINITIONS),
        help="hooks to install: superpowers, openspec, all, or none (default: all)",
    )
    parser.add_argument(
        "--remove",
        type=parse_hook_ids,
        default=set(),
        help="managed hooks to remove without affecting unrelated hooks",
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
    args = parser.parse_args()

    overlap = args.hooks & args.remove
    if overlap:
        parser.error(f"cannot install and remove the same hook: {', '.join(sorted(overlap))}")

    home = Path(os.environ.get("HOME", str(Path.home())))
    codex_home = (args.codex_home or Path(os.environ.get("CODEX_HOME", home / ".codex"))).expanduser()
    script_dir = Path(__file__).resolve().parent

    try:
        if not args.skip_framework_updates:
            if "superpowers" in args.hooks:
                update_superpowers()
            if "openspec" in args.hooks:
                update_openspec(args.openspec_package_manager)

        enable_codex_hooks(codex_home)
        path = codex_home / "hooks.json"
        current = load_hooks(path)
        updated = merge_hooks(current, script_dir, args.hooks, args.remove)
        if current == updated:
            print(f"Hooks already current: {path}")
        else:
            backup = write_hooks_atomic(path, updated)
            print(f"Updated hooks: {path}")
            if backup:
                print(f"Backup: {backup}")
        print("Open a new Codex session, run /hooks, and trust each changed hook definition.")
    except InstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
