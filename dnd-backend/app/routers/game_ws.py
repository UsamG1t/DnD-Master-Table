"""WebSocket игровой комнаты.

Подключение: ws://host/games/{game_id}/ws?token=<JWT>
Сервер шлёт клиенту события комнаты (chat / dice / sheet / log / system)
в том же формате, что и GET /games/{id}/messages.

Клиент может отправлять сообщения:
  {"type": "chat", "text": "..."}
  {"type": "roll", "count": 2, "sides": 6, "modifier": 0, "comment": "..."}
Остальные действия (лист персонажа, изменения) выполняются через REST
и попадают сюда автоматически.
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import models
from ..database import SessionLocal
from ..security import decode_access_token
from ..services import dice
from ..services.game_manager import manager, post_game_message

router = APIRouter(tags=["games"])


@router.websocket("/games/{game_id}/ws")
async def game_socket(ws: WebSocket, game_id: int, token: str = ""):
    user_id = decode_access_token(token)
    if user_id is None:
        await ws.close(code=4401)
        return

    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        member = (
            db.query(models.GameMember)
            .filter_by(game_id=game_id, user_id=user_id)
            .first()
        )
        if user is None or member is None:
            await ws.close(code=4403)
            return

        await manager.connect(game_id, ws)
        try:
            while True:
                msg = await ws.receive_json()
                msg_type = msg.get("type")
                if msg_type == "chat":
                    text = str(msg.get("text", ""))[:4000].strip()
                    if text:
                        await post_game_message(db, game_id, "chat", {"text": text}, user)
                elif msg_type == "roll":
                    count = max(1, min(int(msg.get("count", 1)), 100))
                    sides = max(2, min(int(msg.get("sides", 20)), 1000))
                    modifier = max(-1000, min(int(msg.get("modifier", 0)), 1000))
                    result = dice.roll(count, sides, modifier)
                    if msg.get("comment"):
                        result["comment"] = str(msg["comment"])[:200]
                    await post_game_message(db, game_id, "dice", result, user)
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(game_id, ws)
    finally:
        db.close()
