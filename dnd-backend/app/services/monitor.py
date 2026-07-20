# BACKEND-PATCH (v7): НОВЫЙ файл — скопируйте в app/services/monitor.py.
"""Мониторинг для страницы Settings.

Логи запросов — кольцевой буфер в памяти (высокочастотные, незачем хранить).
Логи внутренних систем (сейчас — работа с объектами DnD RFC) — таблица
system_logs в БД: переживают перезапуск, actor_name хранится снимком.
"""
import threading
from collections import deque

from sqlalchemy.orm import Session

from .. import models

_REQUEST_LOG_SIZE = 500
_requests: deque = deque(maxlen=_REQUEST_LOG_SIZE)
_lock = threading.Lock()


def add_request(entry: dict) -> None:
    with _lock:
        _requests.append(entry)


def recent_requests(limit: int = 200) -> list[dict]:
    with _lock:
        items = list(_requests)
    return list(reversed(items))[:limit]


def log_system(db: Session, message: str,
               actor: models.User | None = None, kind: str = "rfc") -> None:
    """Запись в лог внутренних систем. Ошибки лога не роняют основное действие."""
    try:
        db.add(models.SystemLog(
            kind=kind,
            message=message,
            actor_id=actor.id if actor else None,
            actor_name=actor.username if actor else None,
        ))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"[monitor] не удалось записать системный лог: {exc}", flush=True)
