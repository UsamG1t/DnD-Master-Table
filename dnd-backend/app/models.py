# BACKEND-PATCH (v5): замените этим файлом app/models.py.
# Добавлена модель CommunityObject для подсистемы DnD RFC.

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    characters: Mapped[list["Character"]] = relationship(back_populates="owner")


class Character(Base):
    """Карточка персонажа.

    data — произвольная JSON-структура листа персонажа (класс, раса, статы,
    заклинания, инвентарь и т.д.). Изменяется только через журнал операций
    (CharacterChange), чтобы работала синхронизация и трансляция в лог игры.
    """
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    # Скрытые из лога игры пути (только для персонажей мастера).
    # Пример: ["stats.hp", "inventory"]
    log_hidden_paths: Mapped[list] = mapped_column(JSON, default=list)
    # Персонаж принадлежит не более чем одной игре
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"), nullable=True, index=True)
    # Монотонная версия: номер последней применённой операции (server_seq)
    version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    owner: Mapped["User"] = relationship(back_populates="characters")
    game: Mapped["Game | None"] = relationship(back_populates="characters")
    changes: Mapped[list["CharacterChange"]] = relationship(
        back_populates="character", order_by="CharacterChange.server_seq"
    )


class CharacterChange(Base):
    """Журнал операций (oplog) над персонажем.

    Каждая операция — установка значения по пути внутри Character.data.
    ts — клиентское время операции в миллисекундах Unix (по нему решаются
    конфликты по принципу last-write-wins). server_seq — серверный порядок,
    используется как курсор синхронизации.
    """
    __tablename__ = "character_changes"
    __table_args__ = (UniqueConstraint("op_id", name="uq_change_op_id"),)

    server_seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    op_id: Mapped[str] = mapped_column(String(64), index=True)  # UUID для идемпотентности
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    path: Mapped[str] = mapped_column(String(255))  # напр. "stats.strength"
    value: Mapped[dict] = mapped_column(JSON)        # {"value": <новое значение>}
    ts: Mapped[int] = mapped_column(Integer)         # клиентский timestamp, мс
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    character: Mapped["Character"] = relationship(back_populates="changes")


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    master_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Учётные данные игры генерируются при создании и видны мастеру
    # в параметрах игры, поэтому хранятся в открытом виде.
    login: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    master: Mapped["User"] = relationship()
    characters: Mapped[list["Character"]] = relationship(back_populates="game")
    members: Mapped[list["GameMember"]] = relationship(back_populates="game")
    messages: Mapped[list["GameMessage"]] = relationship(back_populates="game")


class GameMember(Base):
    __tablename__ = "game_members"
    __table_args__ = (UniqueConstraint("game_id", "user_id", name="uq_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Персонаж, которым играет участник (у мастера может быть пусто)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    game: Mapped["Game"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()


class GameMessage(Base):
    """Сообщение игрового пространства.

    type: chat | dice | sheet | log | system
    payload — содержимое, зависящее от типа.
    """
    __tablename__ = "game_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    game: Mapped["Game"] = relationship(back_populates="messages")
    author: Mapped["User | None"] = relationship()


class CommunityObject(Base):
    """Объект сообщества (DnD RFC): классы, виды, предметы, заклинания и т.д.

    status: pending («На обработке») | accepted («Принят») |
            rejected («Отклонён с возможностью доработки»).
    После приёма админом объект получает cache_key (sha256-хеш URL) и
    выкладывается в community_dnd_file_cache — приоритетный кеш базы DnD.
    """
    __tablename__ = "community_objects"
    __table_args__ = (UniqueConstraint("category", "index", name="uq_comm_cat_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    index: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(128))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    author: Mapped["User"] = relationship()


class DndCache(Base):
    """Кеш ответов внешней базы данных DnD."""
    __tablename__ = "dnd_cache"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
