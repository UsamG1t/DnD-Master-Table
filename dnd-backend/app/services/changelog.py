"""Журнал операций персонажа.

Все изменения листа персонажа проходят через apply_operations:
  * операции пишутся в oplog (CharacterChange) — это и история для
    синхронизации Android-клиента, и источник событий для лога игры;
  * операции применяются к Character.data в порядке клиентского времени ts,
    что даёт last-write-wins на уровне отдельного пути;
  * дубликаты (по op_id) молча пропускаются — синхронизацию можно
    безопасно повторять.
"""
import copy
from typing import Any, Sequence

from sqlalchemy.orm import Session

from .. import models, schemas


def set_by_path(data: dict, path: str, value: Any) -> dict:
    """Устанавливает значение по точечному пути ('stats.strength', 'spells.2').

    value=None удаляет ключ. Промежуточные словари создаются автоматически.
    """
    parts = path.split(".")
    node: Any = data
    for part in parts[:-1]:
        if isinstance(node, list):
            node = node[int(part)]
        else:
            if part not in node or not isinstance(node[part], (dict, list)):
                node[part] = {}
            node = node[part]
    last = parts[-1]
    if isinstance(node, list):
        idx = int(last)
        if value is None:
            if 0 <= idx < len(node):
                node.pop(idx)
        elif idx == len(node):
            node.append(value)
        else:
            node[idx] = value
    else:
        if value is None:
            node.pop(last, None)
        else:
            node[last] = value
    return data


def apply_operations(
    db: Session,
    character: models.Character,
    operations: Sequence[schemas.Operation],
    author_id: int | None,
) -> list[models.CharacterChange]:
    """Применяет операции (в порядке ts) и возвращает записанные изменения."""
    existing_ops = {
        row[0]
        for row in db.query(models.CharacterChange.op_id)
        .filter(models.CharacterChange.character_id == character.id)
        .all()
    }
    new_ops = sorted(
        (op for op in operations if op.op_id not in existing_ops),
        key=lambda op: op.ts,
    )
    if not new_ops:
        return []

    data = copy.deepcopy(character.data or {})
    applied: list[models.CharacterChange] = []
    for op in new_ops:
        set_by_path(data, op.path, op.value)
        change = models.CharacterChange(
            op_id=op.op_id,
            character_id=character.id,
            author_id=author_id,
            path=op.path,
            value={"value": op.value},
            ts=op.ts,
        )
        db.add(change)
        applied.append(change)

    character.data = data
    db.flush()  # получаем server_seq
    character.version = applied[-1].server_seq
    db.commit()
    for change in applied:
        db.refresh(change)
    return applied


def is_path_hidden(path: str, hidden_paths: Sequence[str]) -> bool:
    """Путь скрыт, если совпадает со скрытым или лежит внутри него."""
    return any(path == h or path.startswith(h + ".") for h in hidden_paths)


def log_events_for_game(
    character: models.Character,
    changes: Sequence[models.CharacterChange],
    author_name: str | None,
) -> list[dict]:
    """Формирует записи лога игры для применённых изменений.

    Персонажи мастера: пути из log_hidden_paths не транслируются.
    Персонажи игроков: транслируется всё (log_hidden_paths принудительно
    очищается при выставлении персонажа игроком, см. games.select_character).
    """
    if character.game_id is None:
        return []
    hidden = character.log_hidden_paths or []
    events = []
    for change in changes:
        if is_path_hidden(change.path, hidden):
            continue
        events.append(
            {
                "character_id": character.id,
                "character_name": character.name,
                "changed_by": author_name,
                "path": change.path,
                "value": change.value.get("value"),
                "ts": change.ts,
            }
        )
    return events


def hide_paths_in_data(data: dict, hidden_paths: Sequence[str]) -> dict:
    """Возвращает копию data без скрытых путей (для отправки листа в чат)."""
    result = copy.deepcopy(data)
    for path in hidden_paths:
        try:
            set_by_path(result, path, None)
        except (KeyError, IndexError, ValueError):
            continue
    return result
