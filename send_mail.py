from __future__ import annotations

import argparse
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise ValueError(f"環境変数 {name} が設定されていません。")
    return value


def send_mail(to_address: str, subject: str, body: str) -> None:
    load_env_file(Path(".env"))

    host: str = require_env("SMTP_HOST")
    port: int = int(require_env("SMTP_PORT"))
    username: str = require_env("SMTP_USERNAME")
    password: str = require_env("SMTP_PASSWORD")
    sender: str = os.getenv("SMTP_FROM", username)
    recipient: str = to_address

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(username, password)
        server.send_message(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SMTPサーバへメールを送信します。")
    parser.add_argument("--to", required=True, help="宛先メールアドレス")
    parser.add_argument("--subject", required=True, help="メール件名")
    parser.add_argument("--body", required=True, help="メール本文")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    send_mail(to_address=args.to, subject=args.subject, body=args.body)
    print("メール送信に成功しました。")


if __name__ == "__main__":
    main()
