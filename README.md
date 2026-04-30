# TODO Notification Batch

## Setup

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Create `.env` from `.env.example`

```bash
copy .env.example .env
```

3. Run once

```bash
python send_todo_notifications.py
```

## Logs

- The batch writes logs to `log/todo_notifications.log`.

## Cron example

Run every minute:

```cron
* * * * * /usr/bin/python3 /path/to/m_check/send_todo_notifications.py >> /var/log/todo_notice.log 2>&1
```
