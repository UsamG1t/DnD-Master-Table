# BACKEND-PATCH (v7): НОВЫЙ файл — скопируйте в app/routers/server_settings.py
# и подключите в app/main.py (см. main.py из патча).
"""Страница Settings для администратора сервера (SERVER_ADMIN).

Доступ: пользователь с ником SERVER_ADMIN или флагом is_admin.
Содержит: таблицу пользователей с профилями и удалением, сводку всех
кешей базы DnD, логи запросов и логи внутренних систем.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import get_current_user
from ..services import cache_builder, dnd_client, monitor
from .rfc import SERVER_ADMIN, is_server_admin

router = APIRouter(prefix="/settings", tags=["settings"])


def get_server_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if not is_server_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Доступно только администратору сервера")
    return user


@router.get("/me")
def settings_me(user: models.User = Depends(get_current_user)):
    """Без админ-гейта: фронтенд узнаёт, показывать ли вкладку Settings."""
    return {"is_server_admin": is_server_admin(user)}


# ---------------- Пользователи ----------------

@router.get("/users")
def users_with_profiles(
    admin: models.User = Depends(get_server_admin),
    db: Session = Depends(get_db),
):
    """Все пользователи + профиль: персонажи, созданные игры, участие в играх."""
    characters = dict(
        db.query(models.Character.owner_id, func.count())
        .group_by(models.Character.owner_id).all()
    )
    games_created = dict(
        db.query(models.Game.master_id, func.count())
        .group_by(models.Game.master_id).all()
    )
    games_playing = dict(
        db.query(models.GameMember.user_id, func.count())
        .group_by(models.GameMember.user_id).all()
    )
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
            "is_server_admin": u.username == SERVER_ADMIN,
            "created_at": u.created_at,
            "characters_count": characters.get(u.id, 0),
            "games_created": games_created.get(u.id, 0),
            "games_playing": games_playing.get(u.id, 0),
        }
        for u in db.query(models.User).order_by(models.User.id).all()
    ]


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    admin: models.User = Depends(get_server_admin),
    db: Session = Depends(get_db),
):
    """Каскадное удаление пользователя со всеми его данными."""
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(409, "Нельзя удалить самого себя")
    if user.username == SERVER_ADMIN:
        raise HTTPException(409, "Нельзя удалить администратора сервера")

    # Игры, где он мастер: освободить персонажей, снести сообщения/участников/игру
    for game in db.query(models.Game).filter_by(master_id=user.id).all():
        db.query(models.Character).filter_by(game_id=game.id).update({"game_id": None})
        db.query(models.GameMessage).filter_by(game_id=game.id).delete()
        db.query(models.GameMember).filter_by(game_id=game.id).delete()
        db.delete(game)

    # Его персонажи: убрать ссылки из членств чужих игр, журнал изменений, самих
    char_ids = [c.id for c in db.query(models.Character).filter_by(owner_id=user.id).all()]
    if char_ids:
        db.query(models.GameMember).filter(
            models.GameMember.character_id.in_(char_ids)
        ).update({"character_id": None}, synchronize_session=False)
        db.query(models.CharacterChange).filter(
            models.CharacterChange.character_id.in_(char_ids)
        ).delete(synchronize_session=False)
        db.query(models.Character).filter(
            models.Character.id.in_(char_ids)
        ).delete(synchronize_session=False)

    # Членства в чужих играх; авторство в чужих данных — обезличить
    db.query(models.GameMember).filter_by(user_id=user.id).delete()
    db.query(models.GameMessage).filter_by(author_id=user.id).update({"author_id": None})
    db.query(models.CharacterChange).filter_by(author_id=user.id).update({"author_id": None})
    db.query(models.SystemLog).filter_by(actor_id=user.id).update({"actor_id": None})

    # Объекты DnD RFC вместе с файлами community-кеша
    for obj in db.query(models.CommunityObject).filter_by(author_id=user.id).all():
        if obj.cache_key:
            dnd_client.remove_community_cache(obj.cache_key)
        db.delete(obj)

    username = user.username
    db.delete(user)
    db.commit()
    monitor.log_system(db, f"Вы удалили пользователя «{username}» со всеми данными",
                       actor=admin, kind="users")


# ---------------- Кеши ----------------

@router.get("/caches")
def caches_overview(
    admin: models.User = Depends(get_server_admin),
    db: Session = Depends(get_db),
):
    """Сводка всех кешей базы DnD + состояние сборщика."""
    community_files = (
        sum(1 for _ in dnd_client.COMMUNITY_CACHE_DIR.glob("*.json"))
        if dnd_client.COMMUNITY_CACHE_DIR.is_dir() else 0
    )
    return {
        "builder": cache_builder.status(),  # статический кеш + текущая сборка
        "community": {
            "dir": str(dnd_client.COMMUNITY_CACHE_DIR),
            "files": community_files,
        },
        "db_cache_rows": db.query(func.count(models.DndCache.key)).scalar() or 0,
        "api_prefixes": dnd_client.DND_API_PREFIXES,
    }


# ---------------- Логи ----------------

@router.get("/logs/requests")
def request_logs(
    admin: models.User = Depends(get_server_admin),
    db: Session = Depends(get_db),
):
    """Последние HTTP-запросы (кольцевой буфер в памяти)."""
    entries = monitor.recent_requests()
    user_ids = {e["user_id"] for e in entries if e.get("user_id")}
    names = {}
    if user_ids:
        names = dict(
            db.query(models.User.id, models.User.username)
            .filter(models.User.id.in_(user_ids)).all()
        )
    for e in entries:
        e["username"] = names.get(e.get("user_id"))
    return entries


@router.get("/logs/system")
def system_logs(
    admin: models.User = Depends(get_server_admin),
    db: Session = Depends(get_db),
):
    """Логи внутренних систем (сейчас — работа с объектами DnD RFC)."""
    rows = (
        db.query(models.SystemLog)
        .order_by(models.SystemLog.id.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "message": r.message,
            "actor_name": r.actor_name,
            "is_you": r.actor_id == admin.id,
            "created_at": r.created_at,
        }
        for r in rows
    ]
