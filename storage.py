from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Sequence

import aiosqlite

UTC_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


@dataclass(slots=True)
class Reminder:
    id: int
    chat_id: int
    user_id: int
    text: str
    event_ts_utc: datetime
    created_utc: datetime
    archived: bool


@dataclass(slots=True)
class Alert:
    id: int
    reminder_id: int
    alert_ts_utc: datetime
    fired: bool


@dataclass(slots=True)
class Task:
    id: int
    chat_id: int
    user_id: int
    text: str
    created_utc: datetime
    archived: bool


@dataclass(slots=True)
class ShoppingItem:
    id: int
    chat_id: int
    user_id: int
    text: str
    created_utc: datetime
    archived: bool


@dataclass(slots=True)
class Ritual:
    id: int
    chat_id: int
    user_id: int
    text: str
    created_utc: datetime
    enabled: bool


class DBManager:
    """Async wrapper over SQLite storage used by the bot."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    event_ts_utc TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reminder_id INTEGER NOT NULL,
                    alert_ts_utc TEXT NOT NULL,
                    fired INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(reminder_id) REFERENCES reminders(id)
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    bucket TEXT,
                    created_utc TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS rituals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    type TEXT,
                    days_mask TEXT,
                    time_hhmm TEXT,
                    created_utc TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS shopping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_alert_ts ON alerts(alert_ts_utc, fired);
                CREATE INDEX IF NOT EXISTS idx_reminders_event_ts ON reminders(event_ts_utc, archived);
                """
            )
            await db.commit()

    async def create_reminder(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        event_ts_utc: datetime,
        created_utc: datetime,
        alert_times_utc: Sequence[datetime],
    ) -> tuple[Reminder, List[Alert]]:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute(
                    """
                    INSERT INTO reminders (chat_id, user_id, text, event_ts_utc, created_utc)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        user_id,
                        text,
                        event_ts_utc.strftime(UTC_FORMAT),
                        created_utc.strftime(UTC_FORMAT),
                    ),
                )
                reminder_id = (await db.execute("SELECT last_insert_rowid()"))
                reminder_row = await reminder_id.fetchone()
                reminder_pk = int(reminder_row[0])
                alerts: List[Alert] = []
                for alert_dt in alert_times_utc:
                    await db.execute(
                        """
                        INSERT INTO alerts (reminder_id, alert_ts_utc)
                        VALUES (?, ?)
                        """,
                        (
                            reminder_pk,
                            alert_dt.strftime(UTC_FORMAT),
                        ),
                    )
                    cursor = await db.execute("SELECT last_insert_rowid()")
                    alert_row = await cursor.fetchone()
                    alert_id = int(alert_row[0])
                    alerts.append(
                        Alert(
                            id=alert_id,
                            reminder_id=reminder_pk,
                            alert_ts_utc=alert_dt,
                            fired=False,
                        )
                    )
                await db.commit()

        reminder = Reminder(
            id=reminder_pk,
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            event_ts_utc=event_ts_utc,
            created_utc=created_utc,
            archived=False,
        )
        return reminder, alerts

    async def add_alerts(self, reminder_id: int, alert_times_utc: Sequence[datetime]) -> List[Alert]:
        new_alerts: List[Alert] = []
        if not alert_times_utc:
            return new_alerts
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("BEGIN")
                for alert_dt in alert_times_utc:
                    await db.execute(
                        "INSERT INTO alerts (reminder_id, alert_ts_utc) VALUES (?, ?)",
                        (reminder_id, alert_dt.strftime(UTC_FORMAT)),
                    )
                    cursor = await db.execute("SELECT last_insert_rowid()")
                    row = await cursor.fetchone()
                    new_alerts.append(
                        Alert(
                            id=int(row[0]),
                            reminder_id=reminder_id,
                            alert_ts_utc=alert_dt,
                            fired=False,
                        )
                    )
                await db.commit()
        return new_alerts

    async def get_alert(self, alert_id: int) -> Alert | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, reminder_id, alert_ts_utc, fired FROM alerts WHERE id = ?",
                (alert_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return Alert(
                id=row["id"],
                reminder_id=row["reminder_id"],
                alert_ts_utc=datetime.strptime(row["alert_ts_utc"], UTC_FORMAT),
                fired=bool(row["fired"]),
            )

    async def get_alert_with_reminder(self, alert_id: int) -> tuple[Alert, Reminder] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT a.id as alert_id, a.reminder_id, a.alert_ts_utc, a.fired,
                       r.chat_id, r.user_id, r.text, r.event_ts_utc, r.created_utc, r.archived
                FROM alerts a
                JOIN reminders r ON r.id = a.reminder_id
                WHERE a.id = ?
                """,
                (alert_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            alert = Alert(
                id=row["alert_id"],
                reminder_id=row["reminder_id"],
                alert_ts_utc=datetime.strptime(row["alert_ts_utc"], UTC_FORMAT),
                fired=bool(row["fired"]),
            )
            reminder = Reminder(
                id=row["reminder_id"],
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                text=row["text"],
                event_ts_utc=datetime.strptime(row["event_ts_utc"], UTC_FORMAT),
                created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                archived=bool(row["archived"]),
            )
            return alert, reminder

    async def get_pending_alerts(self, now_utc: datetime) -> List[tuple[Alert, Reminder]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT a.id as alert_id, a.reminder_id, a.alert_ts_utc, a.fired,
                       r.id as reminder_id, r.chat_id, r.user_id, r.text,
                       r.event_ts_utc, r.created_utc, r.archived
                FROM alerts a
                JOIN reminders r ON r.id = a.reminder_id
                WHERE a.fired = 0 AND r.archived = 0 AND a.alert_ts_utc > ?
                """,
                (now_utc.strftime(UTC_FORMAT),),
            )
            rows = await cursor.fetchall()
            result: List[tuple[Alert, Reminder]] = []
            for row in rows:
                alert = Alert(
                    id=row["alert_id"],
                    reminder_id=row["reminder_id"],
                    alert_ts_utc=datetime.strptime(row["alert_ts_utc"], UTC_FORMAT),
                    fired=False,
                )
                reminder = Reminder(
                    id=row["reminder_id"],
                    chat_id=row["chat_id"],
                    user_id=row["user_id"],
                    text=row["text"],
                    event_ts_utc=datetime.strptime(row["event_ts_utc"], UTC_FORMAT),
                    created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                    archived=False,
                )
                result.append((alert, reminder))
            return result

    async def mark_alert_fired(self, alert_id: int) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE alerts SET fired = 1 WHERE id = ?", (alert_id,))
                await db.commit()

    async def archive_reminder(self, reminder_id: int) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE reminders SET archived = 1 WHERE id = ?",
                    (reminder_id,),
                )
                await db.execute(
                    "UPDATE alerts SET fired = 1 WHERE reminder_id = ?",
                    (reminder_id,),
                )
                await db.commit()

    async def get_reminder(self, reminder_id: int) -> Reminder | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM reminders WHERE id = ?",
                (reminder_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return Reminder(
                id=row["id"],
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                text=row["text"],
                event_ts_utc=datetime.strptime(row["event_ts_utc"], UTC_FORMAT),
                created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                archived=bool(row["archived"]),
            )

    async def get_reminders_for_range(
        self,
        chat_id: int,
        user_id: int,
        start_utc: datetime | None,
        end_utc: datetime | None,
        archived: bool,
        limit: int = 50,
    ) -> List[Reminder]:
        conditions = ["chat_id = ?", "user_id = ?", "archived = ?"]
        params: List[object] = [chat_id, user_id, int(archived)]
        if start_utc is not None:
            conditions.append("event_ts_utc >= ?")
            params.append(start_utc.strftime(UTC_FORMAT))
        if end_utc is not None:
            conditions.append("event_ts_utc < ?")
            params.append(end_utc.strftime(UTC_FORMAT))
        where_clause = " AND ".join(conditions)
        query = (
            "SELECT * FROM reminders WHERE "
            + where_clause
            + " ORDER BY event_ts_utc ASC LIMIT ?"
        )
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
            reminders: List[Reminder] = []
            for row in rows:
                reminders.append(
                    Reminder(
                        id=row["id"],
                        chat_id=row["chat_id"],
                        user_id=row["user_id"],
                        text=row["text"],
                        event_ts_utc=datetime.strptime(row["event_ts_utc"], UTC_FORMAT),
                        created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                        archived=bool(row["archived"]),
                    )
                )
            return reminders

    async def create_task(self, chat_id: int, user_id: int, text: str, created_utc: datetime) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO tasks (chat_id, user_id, text, created_utc) VALUES (?, ?, ?, ?)",
                (chat_id, user_id, text, created_utc.strftime(UTC_FORMAT)),
            )
            await db.commit()

    async def get_task(self, task_id: int) -> Task | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, chat_id, user_id, text, created_utc, archived FROM tasks WHERE id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return Task(
                id=row["id"],
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                text=row["text"],
                created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                archived=bool(row["archived"]),
            )

    async def get_tasks(self, chat_id: int, user_id: int, limit: int = 50) -> List[Task]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, chat_id, user_id, text, created_utc, archived
                FROM tasks
                WHERE chat_id = ? AND user_id = ? AND archived = 0
                ORDER BY created_utc DESC
                LIMIT ?
                """,
                (chat_id, user_id, limit),
            )
            rows = await cursor.fetchall()
            return [
                Task(
                    id=row["id"],
                    chat_id=row["chat_id"],
                    user_id=row["user_id"],
                    text=row["text"],
                    created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                    archived=bool(row["archived"]),
                )
                for row in rows
            ]

    async def archive_task(self, task_id: int) -> bool:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "UPDATE tasks SET archived = 1 WHERE id = ? AND archived = 0",
                    (task_id,),
                )
                await db.commit()
                return cursor.rowcount > 0

    async def create_ritual(self, chat_id: int, user_id: int, text: str, created_utc: datetime) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO rituals (chat_id, user_id, type, created_utc) VALUES (?, ?, ?, ?)",
                (chat_id, user_id, text, created_utc.strftime(UTC_FORMAT)),
            )
            await db.commit()

    async def get_rituals(self, chat_id: int, user_id: int, limit: int = 50) -> List[Ritual]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, chat_id, user_id, type, created_utc, enabled, days_mask
                FROM rituals
                WHERE chat_id = ? AND user_id = ? AND enabled = 1 AND (days_mask IS NULL OR days_mask = '')
                ORDER BY created_utc DESC
                LIMIT ?
                """,
                (chat_id, user_id, limit),
            )
            rows = await cursor.fetchall()
            return [
                Ritual(
                    id=row["id"],
                    chat_id=row["chat_id"],
                    user_id=row["user_id"],
                    text=row["type"] or "",
                    created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                    enabled=bool(row["enabled"]),
                )
                for row in rows
            ]

    async def get_ritual_preset_states(self, chat_id: int, user_id: int) -> dict[str, bool]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT days_mask, enabled
                FROM rituals
                WHERE chat_id = ? AND user_id = ? AND days_mask LIKE 'preset:%'
                """,
                (chat_id, user_id),
            )
            rows = await cursor.fetchall()
            result: dict[str, bool] = {}
            for row in rows:
                slug = (row["days_mask"] or "").replace("preset:", "", 1)
                result[slug] = bool(row["enabled"])
            return result

    async def set_ritual_preset_state(
        self,
        chat_id: int,
        user_id: int,
        slug: str,
        title: str,
        time_hhmm: str,
        enabled: bool,
    ) -> None:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                existing = await db.execute(
                    """
                    SELECT id FROM rituals
                    WHERE chat_id = ? AND user_id = ? AND days_mask = ?
                    """,
                    (chat_id, user_id, f"preset:{slug}"),
                )
                row = await existing.fetchone()
                if row:
                    await db.execute(
                        "UPDATE rituals SET enabled = ?, type = ?, time_hhmm = ? WHERE id = ?",
                        (int(enabled), title, time_hhmm, row["id"]),
                    )
                else:
                    await db.execute(
                        """
                        INSERT INTO rituals (chat_id, user_id, type, days_mask, time_hhmm, created_utc, enabled)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chat_id,
                            user_id,
                            title,
                            f"preset:{slug}",
                            time_hhmm,
                            datetime.now(timezone.utc).strftime(UTC_FORMAT),
                            int(enabled),
                        ),
                    )
                await db.commit()

    async def create_shopping_item(self, chat_id: int, user_id: int, text: str, created_utc: datetime) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO shopping (chat_id, user_id, text, created_utc) VALUES (?, ?, ?, ?)",
                (chat_id, user_id, text, created_utc.strftime(UTC_FORMAT)),
            )
            await db.commit()

    async def get_shopping_item(self, item_id: int) -> ShoppingItem | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, chat_id, user_id, text, created_utc, archived FROM shopping WHERE id = ?",
                (item_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return ShoppingItem(
                id=row["id"],
                chat_id=row["chat_id"],
                user_id=row["user_id"],
                text=row["text"],
                created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                archived=bool(row["archived"]),
            )

    async def get_shopping_items(self, chat_id: int, user_id: int, limit: int = 50) -> List[ShoppingItem]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, chat_id, user_id, text, created_utc, archived
                FROM shopping
                WHERE chat_id = ? AND user_id = ? AND archived = 0
                ORDER BY created_utc DESC
                LIMIT ?
                """,
                (chat_id, user_id, limit),
            )
            rows = await cursor.fetchall()
            return [
                ShoppingItem(
                    id=row["id"],
                    chat_id=row["chat_id"],
                    user_id=row["user_id"],
                    text=row["text"],
                    created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                    archived=bool(row["archived"]),
                )
                for row in rows
            ]

    async def archive_shopping_item(self, item_id: int) -> bool:
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "UPDATE shopping SET archived = 1 WHERE id = ? AND archived = 0",
                    (item_id,),
                )
                await db.commit()
                return cursor.rowcount > 0

    async def get_shopping_archive(
        self,
        chat_id: int,
        user_id: int,
        days: int = 30,
        limit: int = 50,
    ) -> List[ShoppingItem]:
        threshold = datetime.now(timezone.utc) - timedelta(days=days)
        threshold_str = threshold.strftime(UTC_FORMAT)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, chat_id, user_id, text, created_utc, archived
                FROM shopping
                WHERE chat_id = ? AND user_id = ? AND archived = 1 AND created_utc >= ?
                ORDER BY created_utc DESC
                LIMIT ?
                """,
                (chat_id, user_id, threshold_str, limit),
            )
            rows = await cursor.fetchall()
            return [
                ShoppingItem(
                    id=row["id"],
                    chat_id=row["chat_id"],
                    user_id=row["user_id"],
                    text=row["text"],
                    created_utc=datetime.strptime(row["created_utc"], UTC_FORMAT),
                    archived=bool(row["archived"]),
                )
                for row in rows
            ]

    async def prune_shopping_archive(self, days: int = 30) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff.strftime(UTC_FORMAT)
        async with self._lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM shopping WHERE archived = 1 AND created_utc < ?",
                    (cutoff_str,),
                )
                await db.commit()
