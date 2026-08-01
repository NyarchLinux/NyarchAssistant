"""Fast, dependency-free filesystem search helpers."""

import fnmatch
from functools import cache
import glob
import io
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from typing import Optional, Pattern


@cache
def _ripgrep_command() -> Optional[tuple[str, ...]]:
    executable = shutil.which("rg")
    if executable is not None:
        return (executable,)

    # The Flatpak has permission to run host commands. This makes an existing
    # host ripgrep installation useful without adding a package dependency to
    # the application or changing behavior when it is unavailable.
    flatpak_spawn = shutil.which("flatpak-spawn")
    if not os.getenv("container") or flatpak_spawn is None:
        return None
    try:
        probe = subprocess.run(
            (flatpak_spawn, "--host", "which", "rg"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if probe.returncode == 0:
        return (flatpak_spawn, "--host", "rg")
    return None


def glob_files(search_dir: str, pattern: str) -> list[str]:
    """Return sorted glob matches rooted at *search_dir*.

    Recursive patterns use one ``scandir`` traversal and one precompiled
    matcher instead of recursively expanding the pattern at every directory.
    Older Python versions retain a compatible ``iglob`` fallback.
    """
    if "**" in pattern and not os.path.isabs(pattern) and hasattr(glob, "translate"):
        matcher = re.compile(
            glob.translate(pattern, recursive=True, include_hidden=False)
        )
        include_hidden = any(
            part.startswith(".")
            for part in pattern.replace(os.altsep or os.sep, os.sep).split(os.sep)
        )
        result = []
        pending = [(search_dir, "")]

        if matcher.match(""):
            result.append(search_dir + os.sep)

        while pending:
            directory, relative_dir = pending.pop()
            child_directories = []
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.name.startswith(".") and not include_hidden:
                            continue
                        relative_path = (
                            f"{relative_dir}{os.sep}{entry.name}"
                            if relative_dir
                            else entry.name
                        )
                        try:
                            is_directory = entry.is_dir(follow_symlinks=True)
                        except OSError:
                            is_directory = False

                        if matcher.match(relative_path):
                            result.append(entry.path)
                        elif is_directory and matcher.match(relative_path + os.sep):
                            result.append(entry.path + os.sep)

                        if is_directory:
                            child_directories.append((entry.path, relative_path))
            except OSError:
                continue
            pending.extend(reversed(child_directories))
    else:
        matches = glob.iglob(
            pattern,
            root_dir=search_dir,
            recursive=True,
        )
        result = [
            match if os.path.isabs(match) else os.path.join(search_dir, match)
            for match in matches
        ]
    result.sort()
    return result


def _iter_searchable_files(
    search_path: str, glob_patterns: tuple[str, ...]
) -> Iterator[str]:
    """Yield files without allocating the lists produced by ``os.walk``."""
    pending = [search_path]

    while pending:
        directory = pending.pop()
        child_directories = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not entry.name.startswith("."):
                                child_directories.append(entry.path)
                        elif entry.is_file(follow_symlinks=True) and (
                            not glob_patterns
                            or any(
                                fnmatch.fnmatch(entry.name, pattern)
                                for pattern in glob_patterns
                            )
                        ):
                            yield entry.path
                    except OSError:
                        continue
        except OSError:
            continue

        # Match os.walk's depth-first order while retaining scandir's native
        # directory order.
        pending.extend(reversed(child_directories))


def _search_file(
    file_path: str,
    regex: Pattern[str],
    glob_patterns: tuple[str, ...],
    limit: Optional[int],
) -> list[tuple[str, int, str]]:
    if glob_patterns and not any(
        fnmatch.fnmatch(os.path.basename(file_path), pattern)
        for pattern in glob_patterns
    ):
        return []

    matches = []
    try:
        # Reading and decoding ordinary files in bulk moves the hot path into
        # optimized C routines and requires only one open/read pass per file.
        with open(file_path, "rb") as file:
            content = file.read(16 * 1024 * 1024 + 1)
            if b"\0" in content[:8192]:
                return []

            if len(content) <= 16 * 1024 * 1024:
                text = content.decode("utf-8", errors="replace")
                if "\r" in text:
                    text = text.replace("\r\n", "\n").replace("\r", "\n")
                lines = text.splitlines(keepends=True)
            else:
                # Keep memory bounded for unusually large files.
                file.seek(0)
                lines = io.TextIOWrapper(
                    file, encoding="utf-8", errors="replace"
                )

            for line_number, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append((file_path, line_number, line))
                    if limit and len(matches) >= limit:
                        break
    except (OSError, UnicodeError):
        return []
    return matches


def _grep_with_ripgrep(
    search_path: str,
    regex: Pattern[str],
    glob_patterns: tuple[str, ...],
    limit: Optional[int],
) -> Optional[list[tuple[str, int, str]]]:
    """Use ripgrep when available, returning ``None`` when it cannot be used."""
    command_prefix = _ripgrep_command()
    if (
        command_prefix is None
        or not isinstance(regex.pattern, str)
        or r"\A" in regex.pattern
        or (os.path.isfile(search_path) and os.path.islink(search_path))
    ):
        return None

    command = [
        *command_prefix,
        "--null",
        "--line-number",
        "--with-filename",
        "--no-heading",
        "--color=never",
        "--no-messages",
        "--no-ignore",
        "--hidden",
        "--crlf",
    ]
    for pattern in glob_patterns:
        command.append(f"--glob={pattern}")
    # Exclusions come last because ripgrep resolves overlapping globs using
    # the later rule. Hidden files are retained, matching the Python fallback;
    # only hidden directories are pruned.
    if os.path.isdir(search_path):
        command.extend(("--glob=!.*/*", "--glob=!**/.*/**"))
    command.extend(("-e", regex.pattern, "--", search_path))

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None

    matches = []
    path_fragments = []
    stopped_at_limit = False
    try:
        assert process.stdout is not None
        for record in process.stdout:
            # --null terminates the path independently of any newlines that a
            # valid Unix filename may contain.
            if b"\0" not in record:
                path_fragments.append(record)
                continue
            raw_path, separator, remainder = record.partition(b"\0")
            if not separator:
                continue
            if path_fragments:
                raw_path = b"".join(path_fragments) + raw_path
                path_fragments.clear()
            raw_line_number, separator, raw_content = remainder.partition(b":")
            if not separator:
                continue
            try:
                line_number = int(raw_line_number)
            except ValueError:
                continue
            content = raw_content.decode("utf-8", errors="replace")
            if "\r" in content:
                content = content.replace("\r\n", "\n").replace("\r", "\n")
            matches.append((os.fsdecode(raw_path), line_number, content))
            if limit and len(matches) >= limit:
                stopped_at_limit = True
                process.terminate()
                break
    finally:
        if process.stdout is not None:
            process.stdout.close()
        process.wait()

    # Exit 2 includes regex features unsupported by ripgrep. Preserve the
    # tool's Python-regex compatibility by transparently using the fallback.
    if not stopped_at_limit and process.returncode not in (0, 1):
        return None
    return matches


def grep_files(
    search_path: str,
    regex: Pattern[str],
    glob_patterns: list[str],
    limit: Optional[int] = None,
    use_ripgrep: bool = True,
) -> list[tuple[str, int, str]]:
    """Search text files efficiently while preserving traversal ordering.

    Search work is streamed one file at a time and stops as soon as the result
    limit is reached, without queueing the entire tree in memory.
    """
    if limit is not None:
        limit = int(limit)
        if limit < 0:
            limit = 1
        elif limit == 0:
            limit = None
    patterns = tuple(glob_patterns)

    if use_ripgrep:
        ripgrep_matches = _grep_with_ripgrep(
            search_path, regex, patterns, limit
        )
        if ripgrep_matches is not None:
            return ripgrep_matches

    if os.path.isfile(search_path):
        return _search_file(search_path, regex, patterns, limit)

    matches = []
    for file_path in _iter_searchable_files(search_path, patterns):
        file_matches = _search_file(file_path, regex, (), limit)
        if limit:
            matches.extend(file_matches[: max(0, limit - len(matches))])
            if len(matches) >= limit:
                break
        else:
            matches.extend(file_matches)

    return matches
