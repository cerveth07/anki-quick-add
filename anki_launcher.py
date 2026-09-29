# -*- coding: utf-8 -*-
"""Find and launch the local Windows Anki executable."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class AnkiLaunchError(Exception):
    """Raised when Quick Add cannot launch the selected Anki executable."""


def is_valid_anki_executable(path: str | os.PathLike[str] | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    return (
        candidate.is_file()
        and candidate.suffix.casefold() == ".exe"
        and candidate.name.casefold() == "anki.exe"
    )


def _candidate_paths() -> tuple[Path, ...]:
    candidates: list[Path] = []

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Anki" / "anki.exe")

    which = shutil.which("anki.exe") or shutil.which("anki")
    if which:
        candidates.append(Path(which))

    program_files = os.environ.get("PROGRAMFILES")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)")
    if program_files:
        candidates.append(Path(program_files) / "Anki" / "anki.exe")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Anki" / "anki.exe")

    return tuple(candidates)


def find_anki_executable(saved_path: str | None = None) -> str | None:
    if is_valid_anki_executable(saved_path):
        return str(Path(saved_path).resolve())

    seen: set[str] = set()
    for candidate in _candidate_paths():
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key in seen:
            continue
        seen.add(key)
        if is_valid_anki_executable(candidate):
            return str(candidate.resolve())
    return None


def launch_anki(path: str) -> None:
    if not is_valid_anki_executable(path):
        raise AnkiLaunchError("所选文件不是有效的 anki.exe")

    try:
        subprocess.Popen(
            [path],
            cwd=str(Path(path).parent),
            close_fds=True,
        )
    except OSError as exc:
        raise AnkiLaunchError(str(exc)) from exc
