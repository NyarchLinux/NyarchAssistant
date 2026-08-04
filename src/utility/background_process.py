"""Robust lifecycle management for long-running local subprocesses.

Used by handlers that spawn a local server/daemon (`llama-server`,
`sd-server`, ...) so they don't each reinvent the same spawn/kill code.

A single `BackgroundProcess` guarantees:

* The child runs in its **own session/process-group**, so the whole tree
  (the server plus any helper processes it forks) can be torn down together
  without affecting the host process tree.
* `BackgroundProcess.stop` escalates `SIGTERM` -> `SIGKILL` against
  the entire group and *always* reaps the process via `wait()`, so no zombies
  are left behind.
* It is **thread-safe**: concurrent `start`/`stop` calls are serialized.
* An optional crash monitor reaps the process and calls back if it dies on
  its own (i.e. not via `stop`).

Simple usage:

    from .utility.background_process import BackgroundProcess

    self.server = BackgroundProcess("llama-server")
    self.server.register_atexit()
    ...
    self.server.start(
        cmd,
        env=env, cwd=bin_dir,
        on_crash=lambda: GLib.idle_add(self.throw, "crashed", ErrorSeverity.ERROR),
    )
    ...
    self.server.stop()

Note: when the command is launched through `flatpak-spawn --host`, the actual
server runs on the host as a separate session and is not reachable via the
local process group; terminating the `flatpak-spawn` wrapper is still the
best-effort action taken here.
"""

import atexit
import os
import signal
import subprocess
import threading


class BackgroundProcess:
    """Manages a single long-running local subprocess.

    Only one process is alive per instance: `start` stops any previous
    one first. Create one instance per logical server (e.g. one per handler).

    Captured pipes (`stdout`/`stderr`) are drained in the background so a
    chatty server can never deadlock on a full pipe — which is the classic cause
    of a process that won't die on `SIGTERM` and lingers as a zombie. The
    drained text is available via `get_output`.
    """

    def __init__(self, name: str = "background-process"):
        self.name = name
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._on_crash = None
        self._monitor_thread: threading.Thread | None = None
        self._drainers = []  # (thread, fileobj)
        self._stdout_buf = []
        self._stderr_buf = []
        self._stopping = False
        self._atexit_registered = False

    # Read-only state
    @property
    def process(self) -> subprocess.Popen | None:
        """The underlying `Popen` object, or `None` if not running.

        Useful for advanced needs (e.g. reading a captured stderr pipe). For
        plain lifecycle checks prefer `is_running`.
        """
        return self._proc

    @property
    def is_running(self) -> bool:
        """`True` if a process exists and has not yet exited."""
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def pid(self) -> int | None:
        proc = self._proc
        return proc.pid if proc is not None else None

    # Lifecycle
    def start(
        self,
        cmd,
        *,
        env=None,
        cwd=None,
        stdout=None,
        stderr=None,
        text=False,
        bufsize=None,
        on_crash=None,
    ) -> subprocess.Popen:
        """Spawn `cmd` as a background process.

        If a process is already running it is stopped first.

        Args:
            cmd: Argument list to execute.
            env: Full environment mapping for the child (defaults inherited).
            cwd: Working directory for the child.
            stdout: stdout handle (defaults to inherit).
            stderr: stderr handle (defaults to inherit).
            text: If True, open stdout/stderr pipes in text mode (so reads
                return `str` rather than `bytes`). Matches `subprocess.Popen`.
            bufsize: Buffer size for the pipes (forwarded to `Popen`).
            on_crash: Optional zero-arg callback invoked from a daemon thread
                if the process exits on its own (not via `stop`). Wrap UI
                access in `GLib.idle_add`.

        Returns:
            The `Popen` object for the started process.
        """
        with self._lock:
            self._stop_locked()
            # Reset output buffers for the new process (stop_locked cleared drainers).
            self._stdout_buf = []
            self._stderr_buf = []
            self._on_crash = on_crash
            self._stopping = False

            kwargs = {"start_new_session": True, "text": text}
            if env is not None:
                kwargs["env"] = env
            if cwd is not None:
                kwargs["cwd"] = cwd
            if stdout is not None:
                kwargs["stdout"] = stdout
            if stderr is not None:
                kwargs["stderr"] = stderr
            if bufsize is not None:
                kwargs["bufsize"] = bufsize

            self._proc = subprocess.Popen(cmd, **kwargs)

            # If the caller asked to capture stdout/stderr, drain the pipes in
            # the background. Without this, a server that logs continuously
            # fills the 64KB pipe buffer and blocks in write(); its own SIGTERM
            # handler then re-blocks trying to flush, so the process never exits
            # and cannot be reaped -> zombie. Draining keeps the pipe empty.
            self._start_drainer(self._proc.stdout, self._stdout_buf)
            self._start_drainer(self._proc.stderr, self._stderr_buf)

            self._monitor_thread = threading.Thread(
                target=self._monitor,
                name=f"{self.name}-monitor",
                daemon=True,
            )
            self._monitor_thread.start()
            return self._proc

    def stop(self, *, timeout: float = 5.0) -> None:
        """Terminate the process and any children, then reap it.

        Sends `SIGTERM` to the whole process group, waits up to `timeout`
        seconds, then escalates to `SIGKILL`. Always calls `wait()` so the
        process cannot linger as a zombie. Safe to call when nothing is running.
        """
        with self._lock:
            self._stop_locked(timeout=timeout)

    def _stop_locked(self, *, timeout: float = 5.0) -> None:
        proc = self._proc
        if proc is None:
            return
        self._stopping = True
        self._proc = None

        self._signal_group(proc, signal.SIGTERM)
        if not self._wait(proc, timeout):
            self._signal_group(proc, signal.SIGKILL)
            self._wait(proc, timeout)

        # Close any captured pipes and join the drainer threads. Closing the
        # write-end after the child has exited lets the readers hit EOF; this
        # finalizes the buffers and frees the file descriptors.
        self._close_drainers()

        self._on_crash = None

    # pipe draining 
    def _start_drainer(self, fileobj, buf) -> None:
        """Spawn a daemon thread that continuously reads `fileobj` into `buf`.

        Keeps the pipe empty so a chatty child can't block on a full buffer.
        Only started for pipes the caller actually captured (not inherited).
        """
        if fileobj is None:
            return
        thread = threading.Thread(
            target=self._drain,
            args=(fileobj, buf),
            name=f"{self.name}-drain",
            daemon=True,
        )
        self._drainers.append((thread, fileobj))
        thread.start()

    @staticmethod
    def _drain(fileobj, buf):
        try:
            for line in fileobj:
                # Normalize to str so get_output() has a consistent contract
                # regardless of whether the caller opened the pipe in text mode.
                if isinstance(line, (bytes, bytearray)):
                    line = line.decode("utf-8", errors="replace")
                buf.append(line)
        except (ValueError, OSError):
            # fileobj was closed / process gone
            pass

    def _close_drainers(self) -> None:
        """Close captured pipe fds and join drainer threads (best effort)."""
        drainers = self._drainers
        self._drainers = []
        for _, fileobj in drainers:
            try:
                fileobj.close()
            except (ValueError, OSError):
                pass
        for thread, _ in drainers:
            try:
                thread.join(timeout=1.0)
            except (RuntimeError, OSError):
                pass

    # Output access
    def get_output(self, *, which: str = "stderr") -> str:
        """Return captured pipe output so far.

        Args:
            which: `"stdout"` or `"stderr"`.

        Useful for surfacing startup errors (e.g. why sd-server exited) without
        the handler having to manage the pipe itself.
        """
        buf = self._stdout_buf if which == "stdout" else self._stderr_buf
        return "".join(buf)

    # Internals
    def _signal_group(self, proc: subprocess.Popen, sig) -> None:
        """Send `sig` to the process and its whole process group."""
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = None
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, ValueError, OSError):
            pass

    def _wait(self, proc: subprocess.Popen, timeout: float) -> bool:
        """`wait()` on `proc`; return True if it was reaped within timeout."""
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False
        except (ProcessLookupError, OSError):
            return True

    def _monitor(self) -> None:
        """Daemon thread: block until the process exits, then reap/callback.

        If the process died on its own (`stop()` was not the cause) the
        optional `on_crash` callback is invoked.
        """
        proc = self._proc
        if proc is None:
            return
        try:
            proc.wait()
        except (ProcessLookupError, OSError):
            return

        with self._lock:
            # If stop() cleared/replaced _proc, this was an intentional kill.
            crashed = (not self._stopping) and (self._proc is proc)
            callback = self._on_crash if crashed else None
            if crashed:
                self._proc = None

        if callback is not None:
            try:
                callback()
            except Exception as e:  # never let the callback kill the thread silently
                print(f"[{self.name}] crash callback raised: {e}")

    # Convenience
    def register_atexit(self) -> None:
        """Register `stop` to run at interpreter exit (idempotent).

        Ensures the server is killed even if the application is closed without
        an explicit `stop` call.
        """
        if self._atexit_registered:
            return
        self._atexit_registered = True
        atexit.register(self.stop)
