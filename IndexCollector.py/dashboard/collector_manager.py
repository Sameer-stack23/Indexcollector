"""Starts, stops, and reports on the IndexCollector process."""

from __future__ import annotations

from collections import deque
from datetime import date, datetime
from pathlib import Path
import os
import subprocess
import sys
import threading


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_LOG_LINES = 100


class CollectorManager:
    """Manage one collector process and keep its most recent console output."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._mode: str | None = None
        self._started_at: datetime | None = None
        # Keep dashboard memory bounded even if the collector runs for days.
        self._logs: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._lock = threading.Lock()

    def start(self, mode: str, from_date: str | None = None, to_date: str | None = None) -> tuple[bool, str]:
        """Start the collector in the selected mode, if it is not already running."""
        with self._lock:
            if self._is_running_locked():
                return False, "Collector is already running."

            mode_arguments = {
                "historical": [],
                "live": ["--live"],
                "mock": ["--mock"],
            }
            if mode not in mode_arguments:
                return False, "Invalid collector mode."

            date_arguments: list[str] = []
            if mode == "historical":
                try:
                    parsed_from = date.fromisoformat(from_date) if from_date else None
                    parsed_to = date.fromisoformat(to_date) if to_date else None
                except ValueError:
                    return False, "Dates must use YYYY-MM-DD."
                if parsed_from and parsed_to and parsed_from > parsed_to:
                    return False, "Start date must not be after end date."
                if parsed_to and parsed_to > date.today():
                    return False, "End date cannot be in the future."
                if parsed_from:
                    date_arguments.extend(["--from-date", parsed_from.isoformat()])
                if parsed_to:
                    date_arguments.extend(["--to-date", parsed_to.isoformat()])

            command = [sys.executable, "-u", "main.py", *mode_arguments[mode], *date_arguments]
            creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self._process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creation_flags,
            )
            self._mode = mode
            self._started_at = datetime.now()
            self._logs.clear()
            self._logs.append(self._timestamped("Collector started."))
            threading.Thread(target=self._read_output, daemon=True).start()
            return True, "Collector started."

    def stop(self) -> tuple[bool, str]:
        """Stop the currently running collector process."""
        with self._lock:
            if not self._is_running_locked():
                return False, "Collector is not running."

            assert self._process is not None
            self._process.terminate()
            self._logs.append(self._timestamped("Stop requested."))
            return True, "Stop requested."

    def status(self) -> dict:
        """Return serializable status data for the browser."""
        with self._lock:
            running = self._is_running_locked()
            return {
                "running": running,
                "pid": self._process.pid if running and self._process else None,
                "mode": self._mode if running else None,
                "started_at": self._started_at.strftime("%Y-%m-%d %H:%M:%S") if running and self._started_at else None,
                "logs": list(self._logs),
            }

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return

        for line in process.stdout:
            cleaned = line.rstrip()
            if cleaned:
                with self._lock:
                    self._logs.append(self._timestamped(cleaned))

        exit_code = process.wait()
        with self._lock:
            self._logs.append(self._timestamped(f"Collector stopped (exit code {exit_code})."))

    def _is_running_locked(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @staticmethod
    def _timestamped(message: str) -> str:
        return f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
