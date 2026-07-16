from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ---------- Auth ----------

class UserRegister(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8)
    # Если передан верный ADMIN_REGISTRATION_TOKEN — аккаунт станет админом
    admin_token: str | None = None


class UserLogin(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Персонажи и oplog ----------

class Operation(BaseModel):
    """Одна операция изменения: установить value по пути path внутри data."""
    op_id: str = Field(description="UUID операции, для идемпотентности")
    path: str = Field(description="Точечный путь, напр. 'stats.strength'")
    value: Any = None
    ts: int = Field(description="Клиентский Unix-timestamp в мс (для LWW)")


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    data: dict = Field(default_factory=dict)


class CharacterPatch(BaseModel):
    """Онлайн-редактирование: пачка операций."""
    operations: list[Operation]


class LogVisibilityUpdate(BaseModel):
    log_hidden_paths: list[str]


class GameRef(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True


class CharacterOut(BaseModel):
    id: int
    owner_id: int
    name: str
    data: dict
    log_hidden_paths: list
    version: int
    in_game: bool
    game: GameRef | None
    created_at: datetime
    updated_at: datetime


class ChangeOut(BaseModel):
    server_seq: int
    op_id: str
    path: str
    value: Any
    ts: int
    author_id: int | None

    class Config:
        from_attributes = True


class SyncRequest(BaseModel):
    """Синхронизация офлайн-копии (Android).

    last_server_seq — версия, от которой клиент делал офлайн-правки.
    operations — журнал офлайн-операций в порядке времени применения.
    """
    last_server_seq: int = 0
    operations: list[Operation] = Field(default_factory=list)


class SyncResponse(BaseModel):
    version: int
    data: dict
    applied_ops: list[ChangeOut]  # все операции после last_server_seq (серверные + принятые клиентские)


# ---------- Игры ----------

class GameCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class GameCredentials(BaseModel):
    login: str
    password: str


class GameOut(BaseModel):
    id: int
    name: str
    master_id: int
    is_master: bool
    created_at: datetime
    credentials: GameCredentials | None = None  # только для мастера


class GameJoin(BaseModel):
    login: str
    password: str


class MemberOut(BaseModel):
    user_id: int
    username: str
    character_id: int | None
    is_master: bool


class SelectCharacter(BaseModel):
    character_id: int


class ChatMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DiceRollIn(BaseModel):
    count: int = Field(ge=1, le=100)
    sides: int = Field(ge=2, le=1000)
    modifier: int = Field(default=0, ge=-1000, le=1000)
    comment: str | None = Field(default=None, max_length=200)


class SheetShareIn(BaseModel):
    character_id: int
    # Пути, которые мастер решил скрыть при отправке в чат.
    # Для персонажей игроков игнорируется (скрыть параметры игроков нельзя).
    hidden_paths: list[str] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: int
    type: str
    author_id: int | None
    author_name: str | None
    payload: dict
    created_at: datetime
