"""Bounded, non-interactive execution for assistant shell commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import signal
import subprocess
import threading
import time


DEFAULT_COMMAND_TIMEOUT = 120
MAX_COMMAND_TIMEOUT = 3600
OUTPUT_HEAD_CHARS = 2500
OUTPUT_TAIL_CHARS = 1500
READ_CHUNK_CHARS = 4096


class CommandExecutionStatus(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    STARTUP_ERROR = "startup-error"


class _BoundedTextCollector:
    """Keep a bounded head and tail while continuously draining a pipe."""

    def __init__(self, head_limit: int, tail_limit: int):
        self.head_limit = head_limit
        self.tail_limit = tail_limit
        self.head = ""
        self.tail = ""
        self.total_chars = 0

    def append(self, text: str) -> None:
        if not text:
            return

        self.total_chars += len(text)
        head_remaining = self.head_limit - len(self.head)
        if head_remaining > 0:
            self.head += text[:head_remaining]
            text = text[head_remaining:]
        if text and self.tail_limit > 0:
            self.tail = (self.tail + text)[-self.tail_limit:]

    @property
    def truncated(self) -> bool:
        return self.total_chars > len(self.head) + len(self.tail)

    def get_text(self) -> str:
        if not self.truncated:
            return self.head + self.tail

        omitted = self.total_chars - len(self.head) - len(self.tail)
        return self.head + f"\n... ({omitted} characters omitted) ...\n" + self.tail


@dataclass(frozen=True)
class CommandExecutionResult:
    status: CommandExecutionStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    timeout_seconds: int | None = None
    error: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == CommandExecutionStatus.SUCCESS

    def to_output(self) -> str:
        """Format a stable textual result for storage and the language model."""
        parts = [f"Status: {self.status.value}"]
        if self.exit_code is not None:
            parts.append(f"Exit Code: {self.exit_code}")
        if self.status == CommandExecutionStatus.TIMEOUT and self.timeout_seconds is not None:
            parts.append(f"Timed Out After: {self.timeout_seconds} seconds")
        parts.append(f"Duration: {self.duration_seconds:.2f} seconds")
        if self.error:
            parts.append(f"Error: {self.error}")

        stdout = self.stdout or "(no output)"
        stderr = self.stderr or "(no output)"
        parts.extend([f"Stdout:\n{stdout}", f"Stderr:\n{stderr}"])
        return "\n".join(parts)

    @classmethod
    def error_result(cls, error: str) -> "CommandExecutionResult":
        return cls(status=CommandExecutionStatus.STARTUP_ERROR, error=error)

    @classmethod
    def status_from_output(cls, output: str | None) -> CommandExecutionStatus | None:
        if not output:
            return None
        first_line = output.splitlines()[0].strip()
        prefix = "Status: "
        if not first_line.startswith(prefix):
            return None
        value = first_line[len(prefix):]
        value = {
            "failed": "failure",
            "timed_out": "timeout",
            "error": "startup-error",
        }.get(value, value)
        try:
            return CommandExecutionStatus(value)
        except ValueError:
            return None


class CommandRunner:
    """Execute one shell program with timeout and bounded output capture."""

    def __init__(self, timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT):
        self.timeout_seconds = max(1, min(int(timeout_seconds), MAX_COMMAND_TIMEOUT))

    @staticmethod
    def _drain_pipe(pipe, collector: _BoundedTextCollector) -> None:
        try:
            try:
                while True:
                    chunk = pipe.read(READ_CHUNK_CHARS)
                    if not chunk:
                        break
                    collector.append(chunk)
            except (OSError, ValueError):
                pass
        finally:
            pipe.close()

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass

        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    def run(self, command: str, working_dir: str, host_prefix: list[str] | None = None) -> CommandExecutionResult:
        started_at = time.monotonic()
        cwd = os.path.abspath(os.path.expanduser(working_dir))
        if not os.path.isdir(cwd):
            return CommandExecutionResult(
                status=CommandExecutionStatus.STARTUP_ERROR,
                duration_seconds=time.monotonic() - started_at,
                error=f"Working directory does not exist or is not a directory: {cwd}",
            )

        cmd = [*(host_prefix or []), "bash", "--noprofile", "--norc", "-c", command]
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("BASH_FUNC_")
        }

        try:
            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=(os.name != "nt"),
            )
        except (OSError, ValueError) as error:
            return CommandExecutionResult(
                status=CommandExecutionStatus.STARTUP_ERROR,
                duration_seconds=time.monotonic() - started_at,
                error=str(error),
            )

        stdout_collector = _BoundedTextCollector(OUTPUT_HEAD_CHARS, OUTPUT_TAIL_CHARS)
        stderr_collector = _BoundedTextCollector(OUTPUT_HEAD_CHARS, OUTPUT_TAIL_CHARS)
        reader_threads = [
            threading.Thread(
                target=self._drain_pipe,
                args=(process.stdout, stdout_collector),
                daemon=True,
            ),
            threading.Thread(
                target=self._drain_pipe,
                args=(process.stderr, stderr_collector),
                daemon=True,
            ),
        ]
        for thread in reader_threads:
            thread.start()

        timed_out = False
        try:
            process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process_group(process)

        for thread in reader_threads:
            thread.join(timeout=2)

        if timed_out:
            status = CommandExecutionStatus.TIMEOUT
        elif process.returncode == 0:
            status = CommandExecutionStatus.SUCCESS
        else:
            status = CommandExecutionStatus.FAILURE

        return CommandExecutionResult(
            status=status,
            stdout=stdout_collector.get_text(),
            stderr=stderr_collector.get_text(),
            exit_code=process.returncode,
            duration_seconds=time.monotonic() - started_at,
            timeout_seconds=self.timeout_seconds if timed_out else None,
            stdout_truncated=stdout_collector.truncated,
            stderr_truncated=stderr_collector.truncated,
        )
