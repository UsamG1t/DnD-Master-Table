from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user
from ..services import changelog
from ..services.game_manager import post_game_message

router = APIRouter(prefix="/characters", tags=["characters"])


def to_out(char: models.Character) -> schemas.CharacterOut:
    return schemas.CharacterOut(
        id=char.id,
        owner_id=char.owner_id,
        name=char.name,
        data=char.data or {},
        log_hidden_paths=char.log_hidden_paths or [],
        version=char.version,
        in_game=char.game_id is not None,
        game=schemas.GameRef.model_validate(char.game) if char.game else None,
        created_at=char.created_at,
        updated_at=char.updated_at,
    )


def get_own_character(
    char_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> models.Character:
    char = db.get(models.Character, char_id)
    if char is None:
        raise HTTPException(404, "Персонаж не найден")
    if char.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Это не ваш персонаж")
    return char


async def broadcast_changes_to_game(
    db: Session, char: models.Character, changes: list[models.CharacterChange], author: models.User
) -> None:
    """Транслирует применённые изменения в лог игры (с учётом скрытых путей)."""
    events = changelog.log_events_for_game(char, changes, author.username)
    if events:
        await post_game_message(db, char.game_id, "log", {"events": events}, author)


@router.post("", response_model=schemas.CharacterOut, status_code=201)
def create_character(
    body: schemas.CharacterCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    char = models.Character(owner_id=user.id, name=body.name, data=body.data)
    db.add(char)
    db.commit()
    db.refresh(char)
    return to_out(char)


@router.get("", response_model=list[schemas.CharacterOut])
def my_characters(
    user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    chars = db.query(models.Character).filter_by(owner_id=user.id).order_by(models.Character.id).all()
    return [to_out(c) for c in chars]


@router.get("/{char_id}", response_model=schemas.CharacterOut)
def get_character(char: models.Character = Depends(get_own_character)):
    return to_out(char)


@router.patch("/{char_id}", response_model=schemas.CharacterOut)
async def edit_character(
    body: schemas.CharacterPatch,
    char: models.Character = Depends(get_own_character),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Онлайн-редактирование: применяет пачку операций.

    Каждая операция попадает в журнал изменений; если персонаж в игре —
    изменения транслируются в лог игры.
    """
    changes = changelog.apply_operations(db, char, body.operations, user.id)
    if char.game_id is not None and changes:
        await broadcast_changes_to_game(db, char, changes, user)
    db.refresh(char)
    return to_out(char)


@router.delete("/{char_id}", status_code=204)
def delete_character(
    char: models.Character = Depends(get_own_character), db: Session = Depends(get_db)
):
    if char.game_id is not None:
        raise HTTPException(409, "Персонаж в игре — сначала выведите его из игры")
    db.query(models.CharacterChange).filter_by(character_id=char.id).delete()
    db.delete(char)
    db.commit()


@router.put("/{char_id}/log-visibility", response_model=schemas.CharacterOut)
def set_log_visibility(
    body: schemas.LogVisibilityUpdate,
    char: models.Character = Depends(get_own_character),
    db: Session = Depends(get_db),
):
    """Настройка скрытых из лога путей.

    Разрешена только для персонажей мастера: если персонаж выставлен игроком,
    скрыть его параметры нельзя.
    """
    if char.game_id is not None:
        game = char.game
        if game.master_id != char.owner_id:
            raise HTTPException(403, "Параметры персонажей игроков скрыть нельзя")
    char.log_hidden_paths = body.log_hidden_paths
    db.commit()
    db.refresh(char)
    return to_out(char)


@router.get("/{char_id}/changes", response_model=list[schemas.ChangeOut])
def get_changes(
    since: int = 0,
    char: models.Character = Depends(get_own_character),
    db: Session = Depends(get_db),
):
    """Журнал изменений после указанного server_seq (курсор синхронизации)."""
    rows = (
        db.query(models.CharacterChange)
        .filter(
            models.CharacterChange.character_id == char.id,
            models.CharacterChange.server_seq > since,
        )
        .order_by(models.CharacterChange.server_seq)
        .all()
    )
    return [
        schemas.ChangeOut(
            server_seq=r.server_seq, op_id=r.op_id, path=r.path,
            value=r.value.get("value"), ts=r.ts, author_id=r.author_id,
        )
        for r in rows
    ]


@router.post("/{char_id}/sync", response_model=schemas.SyncResponse)
async def sync_character(
    body: schemas.SyncRequest,
    char: models.Character = Depends(get_own_character),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Синхронизация офлайн-копии (Android).

    1. Клиент присылает журнал офлайн-операций и last_server_seq —
       версию, от которой он редактировал.
    2. Сервер дописывает новые операции в общий журнал и применяет их
       в порядке клиентского времени ts: при конфликте на одном пути
       побеждает более новая операция (last-write-wins).
    3. В ответ клиент получает актуальное состояние и все операции после
       last_server_seq — их он применяет к локальной копии тем же правилом.
    Повтор запроса безопасен: дубликаты отсекаются по op_id.
    """
    applied = changelog.apply_operations(db, char, body.operations, user.id)
    if char.game_id is not None and applied:
        await broadcast_changes_to_game(db, char, applied, user)

    all_since = (
        db.query(models.CharacterChange)
        .filter(
            models.CharacterChange.character_id == char.id,
            models.CharacterChange.server_seq > body.last_server_seq,
        )
        .order_by(models.CharacterChange.server_seq)
        .all()
    )
    db.refresh(char)
    return schemas.SyncResponse(
        version=char.version,
        data=char.data or {},
        applied_ops=[
            schemas.ChangeOut(
                server_seq=r.server_seq, op_id=r.op_id, path=r.path,
                value=r.value.get("value"), ts=r.ts, author_id=r.author_id,
            )
            for r in all_since
        ],
    )
