from __future__ import annotations

import logging
import secrets
from logging.handlers import TimedRotatingFileHandler
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

from send_mail import send_mail


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_name: str
    db_port: int
    db_user: str
    db_password: str
    notice_api_url: str
    schedule_url_base: str = "https://tym-portal.net/mobile/schedule/?a="
    request_timeout_seconds: int = 10


@dataclass(frozen=True)
class NotifyTarget:
    id: int
    title: str
    start_datetime: datetime
    duration: int
    username: str


@dataclass(frozen=True)
class EmailTarget:
    id: int
    title: str
    start_datetime: datetime
    duration: int
    email: str


def setup_logging() -> None:
    base_dir: Path = Path(__file__).resolve().parent
    log_dir: Path = base_dir / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file_path: Path = log_dir / "todo_notifications.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            TimedRotatingFileHandler(
                filename=str(log_file_path),
                when="midnight",
                interval=1,
                backupCount=30,
                encoding="utf-8",
            ),
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
        notice_api_url=os.getenv("NOTICE_API_URL", "http://localhost:8999/notice"),
    )


def connect_db(settings: Settings) -> PgConnection:
    return psycopg2.connect(
        host=settings.db_host,
        dbname=settings.db_name,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
    )


def fetch_notify_targets(conn: PgConnection) -> list[NotifyTarget]:
    query: str = """
        SELECT s.id, s.title, s.start_datetime, s.duration, a.username
        FROM public.schedules s
        INNER JOIN public.accounts a ON s.aid = a.id
        WHERE a.is_deleted = false
          AND s.is_todo_completed = false
          AND s.is_deleted = false
          AND s.notified = false
          AND s.is_all_day = false
          AND s.start_datetime < (CURRENT_TIMESTAMP + interval '3 minutes')
        ORDER BY s.start_datetime ASC
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rows: list[dict[str, Any]] = cur.fetchall()

    return [
        NotifyTarget(
            id=int(row["id"]),
            title=str(row["title"]),
            start_datetime=row["start_datetime"],
            duration=int(row["duration"]),
            username=str(row["username"]),
        )
        for row in rows
    ]


def fetch_email_targets(conn: PgConnection) -> list[EmailTarget]:
    query: str = """
        SELECT s.id, s.title, s.start_datetime, s.duration, a.email
        FROM public.schedules s
        INNER JOIN public.accounts a ON s.aid = a.id
        WHERE a.is_deleted = false
          AND s.is_todo_completed = false
          AND s.is_deleted = false
          AND s.emailed = false
          AND s.is_all_day = false
          AND s.start_datetime < (CURRENT_TIMESTAMP + interval '5 minutes')
          AND a.email IS NOT NULL
          AND btrim(a.email::text) <> ''
        ORDER BY s.start_datetime ASC
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query)
        rows: list[dict[str, Any]] = cur.fetchall()

    return [
        EmailTarget(
            id=int(row["id"]),
            title=str(row["title"]),
            start_datetime=row["start_datetime"],
            duration=int(row["duration"]),
            email=str(row["email"]).strip(),
        )
        for row in rows
    ]


def build_notice_message(start_datetime: datetime, duration_minutes: int) -> str:
    end_datetime: datetime = start_datetime + timedelta(minutes=duration_minutes)
    return f"{start_datetime:%H:%M}～{end_datetime:%H:%M}"


def build_notice_url(base_url: str) -> str:
    random_value: int = secrets.randbelow(1_000_000_000)
    return f"{base_url}{random_value}"


def send_notice(settings: Settings, target: NotifyTarget) -> None:
    payload: dict[str, str] = {
        "username": target.username,
        "title": target.title,
        "message": build_notice_message(target.start_datetime, target.duration),
        "url": build_notice_url(settings.schedule_url_base),
    }
    response: requests.Response = requests.post(
        settings.notice_api_url,
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()


def build_email_body(title: str, start_datetime: datetime, duration_minutes: int) -> str:
    end_datetime: datetime = start_datetime + timedelta(minutes=duration_minutes)
    time_range: str = f"{start_datetime:%H:%M}～{end_datetime:%H:%M}"
    return f"{title}\n\n{start_datetime:%Y-%m-%d %H:%M}\n{time_range}"


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


def mark_schedule_emailed(conn: PgConnection, schedule_id: int) -> bool:
    update_query: str = """
        UPDATE public.schedules
        SET emailed = true,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
          AND emailed = false
    """
    with conn.cursor() as cur:
        cur.execute(update_query, (schedule_id,))
        return cur.rowcount == 1


def process_once(settings: Settings) -> tuple[int, int]:
    notified_count: int = 0
    emailed_count: int = 0
    with connect_db(settings) as conn:
        notify_targets: list[NotifyTarget] = fetch_notify_targets(conn)
        logging.info("found %d schedules to notify", len(notify_targets))

        for target in notify_targets:
            try:
                send_notice(settings, target)
                updated: bool = mark_schedule_notified(conn, target.id)
                conn.commit()
                if updated:
                    notified_count += 1
                    logging.info("notified and updated schedule_id=%d", target.id)
                else:
                    logging.warning(
                        "notice sent but schedule was already notified schedule_id=%d",
                        target.id,
                    )
            except Exception:
                conn.rollback()
                logging.exception("failed to process schedule_id=%d", target.id)

        email_targets: list[EmailTarget] = fetch_email_targets(conn)
        logging.info("found %d schedules to email", len(email_targets))

        for target in email_targets:
            try:
                send_mail(
                    to_address=target.email,
                    subject=target.title,
                    body=build_email_body(
                        target.title, target.start_datetime, target.duration
                    ),
                )
                updated_email: bool = mark_schedule_emailed(conn, target.id)
                conn.commit()
                if updated_email:
                    emailed_count += 1
                    logging.info("emailed and updated schedule_id=%d", target.id)
                else:
                    logging.warning(
                        "mail sent but schedule was already emailed schedule_id=%d",
                        target.id,
                    )
            except Exception:
                conn.rollback()
                logging.exception("failed to email schedule_id=%d", target.id)

    return notified_count, emailed_count


def main() -> None:
    setup_logging()
    settings: Settings = load_settings()
    notified_count, emailed_count = process_once(settings)
    logging.info("done. notified_count=%d emailed_count=%d", notified_count, emailed_count)


if __name__ == "__main__":
    main()
