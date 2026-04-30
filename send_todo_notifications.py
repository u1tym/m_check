from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor

import os


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_name: str
    db_port: int
    db_user: str
    db_password: str
    notice_api_url: str
    notice_username: str = "y-toyama"
    schedule_url_base: str = "https://tym-portal.net/mobile/schedule/?a="
    request_timeout_seconds: int = 10


@dataclass(frozen=True)
class Schedule:
    id: int
    title: str
    start_datetime: datetime
    duration: int


def setup_logging() -> None:
    base_dir: Path = Path(__file__).resolve().parent
    log_dir: Path = base_dir / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path: Path = log_dir / "todo_notifications.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def load_settings() -> Settings:
    load_dotenv()

    db_port_raw: str = os.getenv("DB_PORT", "5432")
    try:
        db_port: int = int(db_port_raw)
    except ValueError as exc:
        raise ValueError(f"DB_PORT must be integer: {db_port_raw}") from exc

    return Settings(
        db_host=os.getenv("DB_HOST", "localhost"),
        db_name=os.getenv("DB_NAME", "tamtdb"),
        db_port=db_port,
        db_user=os.getenv("DB_USER", "tamtuser"),
        db_password=os.getenv("DB_PASSWORD", "TAMTTAMT"),
        notice_api_url=os.getenv("NOTICE_API_URL", "http://localhost:8999/auth/notice"),
    )


def connect_db(settings: Settings) -> PgConnection:
    return psycopg2.connect(
        host=settings.db_host,
        dbname=settings.db_name,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
    )


def fetch_target_schedules(conn: PgConnection) -> list[Schedule]:
    query: str = """
        SELECT id, title, start_datetime, duration
        FROM public.schedules
        WHERE schedule_type = 'TODO'
          AND is_todo_completed = false
          AND is_deleted = false
          AND notified = false
          AND is_all_day = false
          AND start_datetime < (CURRENT_TIMESTAMP + interval '3 minutes')
        ORDER BY start_datetime ASC
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rows: list[dict[str, Any]] = cur.fetchall()

    schedules: list[Schedule] = [
        Schedule(
            id=int(row["id"]),
            title=str(row["title"]),
            start_datetime=row["start_datetime"],
            duration=int(row["duration"]),
        )
        for row in rows
    ]
    return schedules


def build_notice_message(start_datetime: datetime, duration_minutes: int) -> str:
    end_datetime: datetime = start_datetime + timedelta(minutes=duration_minutes)
    return f"{start_datetime:%H:%M}～{end_datetime:%H:%M}"


def build_notice_url(base_url: str) -> str:
    random_value: int = secrets.randbelow(1_000_000_000)
    return f"{base_url}{random_value}"


def send_notice(settings: Settings, schedule: Schedule) -> None:
    payload: dict[str, str] = {
        "username": settings.notice_username,
        "title": schedule.title,
        "message": build_notice_message(schedule.start_datetime, schedule.duration),
        "url": build_notice_url(settings.schedule_url_base),
    }
    response: requests.Response = requests.post(
        settings.notice_api_url,
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()


def mark_schedule_notified(conn: PgConnection, schedule_id: int) -> bool:
    update_query: str = """
        UPDATE public.schedules
        SET notified = true,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
          AND notified = false
    """
    with conn.cursor() as cur:
        cur.execute(update_query, (schedule_id,))
        return cur.rowcount == 1


def process_once(settings: Settings) -> int:
    sent_count: int = 0
    with connect_db(settings) as conn:
        targets: list[Schedule] = fetch_target_schedules(conn)
        logging.info("found %d schedules to notify", len(targets))

        for schedule in targets:
            try:
                send_notice(settings, schedule)
                updated: bool = mark_schedule_notified(conn, schedule.id)
                conn.commit()
                if updated:
                    sent_count += 1
                    logging.info("notified and updated schedule_id=%d", schedule.id)
                else:
                    logging.warning(
                        "notice sent but schedule was already notified schedule_id=%d",
                        schedule.id,
                    )
            except Exception:
                conn.rollback()
                logging.exception("failed to process schedule_id=%d", schedule.id)

    return sent_count


def main() -> None:
    setup_logging()
    settings: Settings = load_settings()
    sent_count: int = process_once(settings)
    logging.info("done. sent_count=%d", sent_count)


if __name__ == "__main__":
    main()
