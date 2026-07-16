# BACKEND-PATCH (v3): замените этим файлом app/routers/games.py.
# Добавлены: GET /games/{game_id}/characters — список персонажей игры,
# POST /games/{game_id}/skill-roll — бросок по навыку
# (1d20 + бонус мастерства + модификатор характеристики + бонус навыка).

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, get_game_membership
from ..security import generate_game_credentials
from ..services import dice, dnd_client
from ..services.changelog import hide_paths_in_data
from ..services.game_manager import message_to_dict, post_game_message

router = APIRouter(prefix="/games", tags=["games"])


def game_to_out(game: models.Game, user_id: int) -> schemas.GameOut:
    is_master = game.master_id == user_id
    return schemas.GameOut(
        id=game.id,
        name=game.name,
        master_id=game.master_id,
        is_master=is_master,
        created_at=game.created_at,
        # Логин-пароль игры доступны только мастеру в параметрах игры
        credentials=schemas.GameCredentials(login=game.login, password=game.password)
        if is_master else None,
    )


@router.post("", response_model=schemas.GameOut, status_code=201)
def create_game(
    body: schemas.GameCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создание игры. Создатель получает пометку мастера,
    логин-пароль игры генерируются автоматически."""
    login, password = generate_game_credentials()
    game = models.Game(name=body.name, master_id=user.id, login=login, password=password)
    db.add(game)
    db.flush()
    db.add(models.GameMember(game_id=game.id, user_id=user.id, is_master=True))
    db.commit()
    db.refresh(game)
    return game_to_out(game, user.id)


@router.get("", response_model=list[schemas.GameOut])
def my_games(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = db.query(models.GameMember).filter_by(user_id=user.id).all()
    return [game_to_out(m.game, user.id) for m in memberships]


@router.post("/join", response_model=schemas.GameOut)
def join_game(
    body: schemas.GameJoin,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Вход в игру по её логину-паролю."""
    game = db.query(models.Game).filter_by(login=body.login).first()
    if game is None or game.password != body.password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверные логин или пароль игры")
    existing = db.query(models.GameMember).filter_by(game_id=game.id, user_id=user.id).first()
    if existing is None:
        db.add(models.GameMember(game_id=game.id, user_id=user.id, is_master=False))
        db.commit()
    return game_to_out(game, user.id)


@router.get("/{game_id}", response_model=schemas.GameOut)
def get_game(
    game_id: int,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
):
    return game_to_out(member.game, user.id)


@router.get("/{game_id}/members", response_model=list[schemas.MemberOut])
def members(
    game_id: int,
    member: models.GameMember = Depends(get_game_membership),
):
    return [
        schemas.MemberOut(
            user_id=m.user_id,
            username=m.user.username,
            character_id=m.character_id,
            is_master=m.is_master,
        )
        for m in member.game.members
    ]


@router.get("/{game_id}/characters")
def game_characters(
    game_id: int,
    member: models.GameMember = Depends(get_game_membership),
    db: Session = Depends(get_db),
):
    """Все персонажи игры (и игроков, и мастера)."""
    chars = (
        db.query(models.Character)
        .filter_by(game_id=game_id)
        .order_by(models.Character.id)
        .all()
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "owner_id": c.owner_id,
            "owner_name": c.owner.username,
            "is_master_character": c.owner_id == member.game.master_id,
        }
        for c in chars
    ]


@router.get("/{game_id}/free-characters", response_model=list[dict])
def free_characters(
    game_id: int,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Список свободных (не занятых играми) персонажей пользователя —
    показывается игроку при входе в игру для выбора."""
    chars = (
        db.query(models.Character)
        .filter_by(owner_id=user.id, game_id=None)
        .order_by(models.Character.id)
        .all()
    )
    return [{"id": c.id, "name": c.name} for c in chars]


@router.post("/{game_id}/select-character", status_code=204)
async def select_character(
    game_id: int,
    body: schemas.SelectCharacter,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Игрок выставляет своего свободного персонажа в игру."""
    char = db.get(models.Character, body.character_id)
    if char is None or char.owner_id != user.id:
        raise HTTPException(404, "Персонаж не найден среди ваших")
    if char.game_id is not None:
        raise HTTPException(409, "Персонаж уже участвует в игре")
    if member.character_id is not None:
        raise HTTPException(409, "Вы уже выбрали персонажа в этой игре")

    char.game_id = game_id
    if not member.is_master:
        # Скрыть параметры персонажей игроков нельзя
        char.log_hidden_paths = []
    member.character_id = char.id
    db.commit()
    await post_game_message(
        db, game_id, "system",
        {"event": "character_joined", "character_id": char.id, "character_name": char.name},
        user,
    )


@router.post("/{game_id}/characters", status_code=204)
async def master_add_character(
    game_id: int,
    body: schemas.SelectCharacter,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Мастер добавляет в игру игрового персонажа (NPC) из своих."""
    if not member.is_master:
        raise HTTPException(403, "Добавлять игровых персонажей может только мастер")
    char = db.get(models.Character, body.character_id)
    if char is None or char.owner_id != user.id:
        raise HTTPException(404, "Персонаж не найден среди ваших")
    if char.game_id is not None:
        raise HTTPException(409, "Персонаж уже участвует в игре")
    char.game_id = game_id
    db.commit()
    await post_game_message(
        db, game_id, "system",
        {"event": "npc_added", "character_id": char.id, "character_name": char.name},
        user,
    )


@router.post("/{game_id}/characters/{char_id}/remove", status_code=204)
async def remove_character(
    game_id: int,
    char_id: int,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Вывод персонажа из игры: мастер — любого, игрок — своего."""
    char = db.get(models.Character, char_id)
    if char is None or char.game_id != game_id:
        raise HTTPException(404, "Персонаж не найден в этой игре")
    if not member.is_master and char.owner_id != user.id:
        raise HTTPException(403, "Можно выводить только своего персонажа")
    char.game_id = None
    for m in member.game.members:
        if m.character_id == char.id:
            m.character_id = None
    db.commit()
    await post_game_message(
        db, game_id, "system",
        {"event": "character_left", "character_id": char.id, "character_name": char.name},
        user,
    )


# ---------- Чат, кубики, лист персонажа ----------

@router.get("/{game_id}/messages", response_model=list[schemas.MessageOut])
def message_history(
    game_id: int,
    before_id: int | None = None,
    limit: int = 100,
    member: models.GameMember = Depends(get_game_membership),
    db: Session = Depends(get_db),
):
    q = db.query(models.GameMessage).filter_by(game_id=game_id)
    if before_id is not None:
        q = q.filter(models.GameMessage.id < before_id)
    rows = q.order_by(models.GameMessage.id.desc()).limit(min(limit, 200)).all()
    return [
        schemas.MessageOut(**message_to_dict(m, m.author.username if m.author else None))
        for m in reversed(rows)
    ]


@router.post("/{game_id}/chat", status_code=204)
async def send_chat(
    game_id: int,
    body: schemas.ChatMessageIn,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await post_game_message(db, game_id, "chat", {"text": body.text}, user)


@router.post("/{game_id}/roll", status_code=204)
async def roll_dice(
    game_id: int,
    body: schemas.DiceRollIn,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Бросок указанного числа кубиков. Результат уходит в чат и в лог игры."""
    result = dice.roll(body.count, body.sides, body.modifier)
    if body.comment:
        result["comment"] = body.comment
    await post_game_message(db, game_id, "dice", result, user)


# ---------- Бросок по навыку ----------

class SkillRollIn(BaseModel):
    skill: str                      # индекс навыка из базы DnD, напр. "intimidation"
    character_id: int | None = None  # необязательно: свой персонаж в этой игре


_ABILITY_KEY = {
    "str": "strength", "dex": "dexterity", "con": "constitution",
    "int": "intelligence", "wis": "wisdom", "cha": "charisma",
}
SKILL_PROFICIENCY_BONUS = 2


@router.post("/{game_id}/skill-roll", status_code=204)
async def skill_roll(
    game_id: int,
    body: SkillRollIn,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Бросок по навыку: 1d20 + бонус мастерства + модификатор
    характеристики навыка + бонус навыка (+2, если навык есть у персонажа).

    Бросок делается от лица персонажа: указанного в character_id или
    выставленного участником в игру. Считается на сервере по сохранённому
    листу — подделать модификаторы на клиенте нельзя.
    """
    char_id = body.character_id or member.character_id
    if char_id is None:
        raise HTTPException(409, "Сначала выберите персонажа в этой игре")
    char = db.get(models.Character, char_id)
    if char is None or char.game_id != game_id:
        raise HTTPException(404, "Персонаж не найден в этой игре")
    if char.owner_id != user.id:
        raise HTTPException(403, "Бросать по навыку можно только своим персонажем")

    skill_raw = await dnd_client.get_raw(db, "skills", body.skill)
    ability_short = (skill_raw.get("ability_score") or {}).get("index", "str")
    ability_key = _ABILITY_KEY.get(ability_short, "strength")

    data = char.data or {}
    level = int(data.get("level") or 1)
    proficiency = 2 + (max(1, level) - 1) // 4
    base = int((data.get("stats") or {}).get(ability_key) or 10)
    asi = int(((data.get("asi") or {}).get("bonuses") or {}).get(ability_key) or 0)
    background = int((data.get("background_bonuses") or {}).get(ability_key) or 0)
    ability_mod = (base + asi + background - 10) // 2  # floor и для отрицательных
    skill_bonus = SKILL_PROFICIENCY_BONUS if body.skill in (data.get("skills") or []) else 0

    result = dice.roll(1, 20, proficiency + ability_mod + skill_bonus)
    result["skill"] = {
        "index": body.skill,
        "name": skill_raw.get("name", body.skill),
        "ability": ability_short,
    }
    result["breakdown"] = {
        "character": char.name,
        "proficiency": proficiency,
        "ability_mod": ability_mod,
        "skill_bonus": skill_bonus,
    }
    await post_game_message(db, game_id, "dice", result, user)


@router.post("/{game_id}/share-sheet", status_code=204)
async def share_sheet(
    game_id: int,
    body: schemas.SheetShareIn,
    member: models.GameMember = Depends(get_game_membership),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отправка листа персонажа в чат.

    Мастер может прислать лист своего персонажа, скрыв выбранные
    характеристики. Игрок отправляет свой лист целиком — скрыть
    параметры игроков нельзя.
    """
    char = db.get(models.Character, body.character_id)
    if char is None or char.game_id != game_id:
        raise HTTPException(404, "Персонаж не найден в этой игре")
    if char.owner_id != user.id:
        raise HTTPException(403, "Можно отправлять только лист своего персонажа")

    hidden = body.hidden_paths if member.is_master else []
    data = hide_paths_in_data(char.data or {}, hidden)
    await post_game_message(
        db, game_id, "sheet",
        {
            "character_id": char.id,
            "character_name": char.name,
            "data": data,
            "hidden_paths": hidden,
        },
        user,
    )
