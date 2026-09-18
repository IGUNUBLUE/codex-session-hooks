#!/usr/bin/env python3
"""Inject concise OpenSpec context for the nearest initialized project."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

SKILL_NAME = re.compile(r"^openspec-[a-z0-9][a-z0-9-]*$")


def ancestors(start: Path):
    current = start.resolve()
    yield current
    yield from current.parents


def find_openspec_root(start: Path) -> Path | None:
    for candidate in ancestors(start):
        if (candidate / "openspec" / "config.yaml").is_file():
            return candidate
    return None


def find_skill_names(project_root: Path, codex_home: Path | None = None) -> list[str]:
    locations: list[Path] = []
    for candidate in ancestors(project_root):
        locations.extend(
            [
                candidate / ".agents" / "skills",
                candidate / ".codex" / "skills",
            ]
        )
    if codex_home:
        locations.append(codex_home / "skills")

    names: set[str] = set()
    for location in locations:
        if not location.is_dir():
            continue
        for candidate in location.iterdir():
            if (
                candidate.is_dir()
                and SKILL_NAME.fullmatch(candidate.name)
                and (candidate / "SKILL.md").is_file()
            ):
                names.add(candidate.name)
    return sorted(names)


def render_context(project_root: Path, skill_names: list[str]) -> str:
    root = json.dumps(str(project_root))
    config = json.dumps(str(project_root / "openspec" / "config.yaml"))
    lines = [
        "<openspec-context>",
        f"The current working directory belongs to an OpenSpec project rooted at {root}.",
        f"Its project configuration is {config}.",
        "OpenSpec workflows are explicit: use one when the user requests it or repository "
        "instructions require it; do not impose OpenSpec on unrelated work.",
    ]
    if skill_names:
        invocations = ", ".join(f"${name}" for name in skill_names)
        lines.extend(
            [
                f"Available generated Codex skills: {invocations}.",
                "Invoke the exact matching skill and follow it. Let the skill and OpenSpec CLI "
                "determine the schema, context files, artifacts, and synchronization steps; do "
                "not assume a fixed artifact layout or edit main specs prematurely.",
            ]
        )
    else:
        lines.append(
            "No generated Codex OpenSpec skills were found. Do not guess invocation names; "
            "the project needs `openspec update` or `openspec init --tools codex`."
        )
    lines.extend(
        [
            "Framework precedence: when work creates or modifies specified behavior, the "
            "OpenSpec change artifacts are the system of record. Process skills from other "
            "frameworks may still apply inside a change for implementation discipline "
            "(testing, debugging, review), but documents they generate must reference the "
            "OpenSpec spec rather than restate or replace it.",
            "</openspec-context>",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--managed-by")
    parser.parse_args()

    root = find_openspec_root(Path.cwd())
    if not root:
        return 0
    home = Path(os.environ.get("HOME", str(Path.home())))
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    try:
        sys.stdout.write(render_context(root, find_skill_names(root, codex_home)))
    except OSError as error:
        print(f"codex-session-hooks: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
