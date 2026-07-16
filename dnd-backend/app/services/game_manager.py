"""Игровые комнаты: WebSocket-подключения и публикация сообщений.

Каждое сообщение (чат, бросок кубиков, лист персонажа, запись лога)
сохраняется в БД (история) и рассылается всем подключённым участникам.
"""
from collections import defaultdict

from fastapi import WebSocket
from sqlalchemy.orm import Session

from .. import models


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[int, set[WebSocket]] = defaultdict(set)

    async def connect(self, game_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms[game_id].add(ws)

    def disconnect(self, game_id: int, ws: WebSocket) -> None:
        self._rooms[game_id].discard(ws)

    async def broadcast(self, game_id: int, payload: dict) -> None:
        dead = []
        for ws in self._rooms.get(game_id, set()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(game_id, ws)


manager = ConnectionManager()


def message_to_dict(msg: models.GameMessage, author_name: str | None) -> dict:
    return {
        "id": msg.id,
        "type": msg.type,
        "author_id": msg.author_id,
        "author_name": author_name,
        "payload": msg.payload,
        "created_at": msg.created_at.isoformat(),
    }


async def post_game_message(
    db: Session,
    game_id: int,
    type_: str,
    payload: dict,
    author: models.User | None = None,
) -> models.GameMessage:
    """Сохраняет сообщение в историю игры и рассылает его по WebSocket."""
    msg = models.GameMessage(
        game_id=game_id,
        author_id=author.id if author else None,
        type=type_,
        payload=payload,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    await manager.broadcast(game_id, message_to_dict(msg, author.username if author else None))
    return msg
