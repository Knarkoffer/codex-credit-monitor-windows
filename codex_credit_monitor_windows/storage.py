from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .domain import (
    Evaluation,
    Observation,
    PaceState,
    Schedule,
    Thresholds,
    evaluate,
    previous_calendar_month,
)
from .settings import app_data_home


@dataclass(frozen=True)
class Window:
    id: int
    account_key: str
    start: datetime
    end: datetime
    timezone_name: str
    schedule: Schedule


@dataclass(frozen=True)
class CommitResult:
    window: Window
    observation: Observation
    evaluation: Evaluation
    notify: bool


class HistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        database = path or app_data_home() / "history.sqlite"
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts(account_key TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS windows(id INTEGER PRIMARY KEY, account_key TEXT NOT NULL REFERENCES accounts(account_key), start_at TEXT NOT NULL, end_at TEXT NOT NULL, timezone TEXT NOT NULL, start_minutes INTEGER NOT NULL, end_minutes INTEGER NOT NULL, completed INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, window_id INTEGER NOT NULL REFERENCES windows(id), account_key TEXT NOT NULL, observed_at TEXT NOT NULL, limit_text TEXT NOT NULL, used_text TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notification_state(account_key TEXT PRIMARY KEY, armed INTEGER NOT NULL, last_state TEXT NOT NULL, window_id INTEGER NOT NULL);
            """
            )

    def close(self) -> None:
        self.connection.close()

    def commit(
        self,
        observation: Observation,
        schedule: Schedule,
        thresholds: Thresholds,
        timezone_name: str,
    ) -> CommitResult:
        zone = ZoneInfo(timezone_name)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO accounts(account_key,user_id,workspace_id) VALUES(?,?,?)",
                (
                    observation.account_key,
                    observation.user_id,
                    observation.workspace_id,
                ),
            )
            window = self._current_window(observation.account_key)
            is_new = window is None or observation.observed_at >= window.end
            if is_new:
                if window:
                    self.connection.execute(
                        "UPDATE windows SET completed=1 WHERE id=?", (window.id,)
                    )
                start = previous_calendar_month(observation.reset_at, zone)
                cursor = self.connection.execute(
                    "INSERT INTO windows(account_key,start_at,end_at,timezone,start_minutes,end_minutes,completed) VALUES(?,?,?,?,?,?,0)",
                    (
                        observation.account_key,
                        _iso(start),
                        _iso(observation.reset_at),
                        timezone_name,
                        schedule.start_minutes,
                        schedule.end_minutes,
                    ),
                )
                window = Window(
                    cursor.lastrowid,
                    observation.account_key,
                    start,
                    observation.reset_at,
                    timezone_name,
                    schedule,
                )
            elif observation.reset_at != window.end:
                self.connection.execute(
                    "UPDATE windows SET end_at=? WHERE id=?",
                    (_iso(observation.reset_at), window.id),
                )
                window = Window(
                    window.id,
                    window.account_key,
                    window.start,
                    observation.reset_at,
                    window.timezone_name,
                    window.schedule,
                )
            self.connection.execute(
                "INSERT INTO observations(window_id,account_key,observed_at,limit_text,used_text) VALUES(?,?,?,?,?)",
                (
                    window.id,
                    observation.account_key,
                    _iso(observation.observed_at),
                    str(observation.limit),
                    str(observation.used),
                ),
            )
            if window.schedule != schedule:
                self.connection.execute(
                    "UPDATE windows SET start_minutes=?,end_minutes=? WHERE id=?",
                    (schedule.start_minutes, schedule.end_minutes, window.id),
                )
                window = Window(
                    window.id,
                    window.account_key,
                    window.start,
                    window.end,
                    window.timezone_name,
                    schedule,
                )
            evaluation = evaluate(
                observation,
                observation.observed_at,
                window.start,
                schedule,
                thresholds,
                zone,
            )
            return CommitResult(
                window,
                observation,
                evaluation,
                self._update_notification(
                    observation.account_key, window.id, evaluation, is_new
                ),
            )

    def observations(self, window_id: int) -> list[Observation]:
        window = self.window(window_id)
        rows = self.connection.execute(
            "SELECT a.user_id,a.workspace_id,o.limit_text,o.used_text,o.observed_at FROM observations o JOIN accounts a ON a.account_key=o.account_key WHERE o.window_id=? ORDER BY o.observed_at",
            (window_id,),
        ).fetchall()
        return [
            Observation(
                row["user_id"],
                row["workspace_id"],
                Decimal(row["limit_text"]),
                Decimal(row["used_text"]),
                window.end,
                _datetime(row["observed_at"]),
            )
            for row in rows
        ]

    def windows(self, account_key: str) -> list[Window]:
        return [
            self._decode(row)
            for row in self.connection.execute(
                "SELECT * FROM windows WHERE account_key=? ORDER BY end_at DESC",
                (account_key,),
            )
        ]

    def window(self, window_id: int) -> Window:
        row = self.connection.execute(
            "SELECT * FROM windows WHERE id=?", (window_id,)
        ).fetchone()
        if row is None:
            raise KeyError(window_id)
        return self._decode(row)

    def delete_all(self) -> None:
        with self.connection:
            self.connection.executescript(
                "DELETE FROM notification_state; DELETE FROM observations; DELETE FROM windows; DELETE FROM accounts;"
            )

    def _current_window(self, account_key: str) -> Window | None:
        row = self.connection.execute(
            "SELECT * FROM windows WHERE account_key=? ORDER BY end_at DESC LIMIT 1",
            (account_key,),
        ).fetchone()
        return self._decode(row) if row else None

    @staticmethod
    def _decode(row: sqlite3.Row) -> Window:
        return Window(
            row["id"],
            row["account_key"],
            _datetime(row["start_at"]),
            _datetime(row["end_at"]),
            row["timezone"],
            Schedule(row["start_minutes"], row["end_minutes"]),
        )

    def _update_notification(
        self, account_key: str, window_id: int, evaluation: Evaluation, baseline: bool
    ) -> bool:
        row = self.connection.execute(
            "SELECT armed,last_state FROM notification_state WHERE account_key=?",
            (account_key,),
        ).fetchone()
        critical, notify = evaluation.state is PaceState.CRITICAL, False
        if baseline or row is None:
            armed = not critical
        else:
            armed = bool(row["armed"])
            if (
                not armed
                and evaluation.critical_threshold is not None
                and evaluation.difference <= evaluation.critical_threshold - 1
            ):
                armed = True
            elif armed and critical and row["last_state"] != PaceState.CRITICAL.value:
                armed, notify = False, True
        self.connection.execute(
            "INSERT INTO notification_state(account_key,armed,last_state,window_id) VALUES(?,?,?,?) ON CONFLICT(account_key) DO UPDATE SET armed=excluded.armed,last_state=excluded.last_state,window_id=excluded.window_id",
            (account_key, int(armed), evaluation.state.value, window_id),
        )
        return notify


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)
