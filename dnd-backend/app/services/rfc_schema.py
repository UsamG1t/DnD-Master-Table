"""Реестр схем RFC-объектов + валидация + резолв ссылок.

Каждая категория DnD описывается схемой полей. Схема задаёт:
  * какие поля есть, их типы и обязательность;
  * ограничения значений (диапазоны, enum);
  * ссылочные поля (ref / ref_list) — «подключение» других карточек:
    заклинание ссылается на классы, вид — на способности (traits) и т.д.

Валидация полей (типы, диапазоны, enum) — синхронная, без обращений к базе.
Резолв ссылок (существуют ли объекты, на которые ссылаемся, и в каком они
статусе) — асинхронный, через dnd_client.fetch_first, и запускается только
по требованию (создание, «проверить и отправить», приём админом), а не
постоянно.

Статусы объекта:
  draft    — «Не готов к модерации»: есть ссылки на непринятые объекты
             или битые ссылки. Админу не виден.
  pending  — «На обработке»: все ссылки ведут на принятые (SRD/accepted).
  accepted — «Принят».
  rejected — «Отклонён с возможностью доработки».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from .. import models
from . import dnd_client

# Шесть характеристик SRD (короткие индексы)
ABILITIES = ["str", "dex", "con", "int", "wis", "cha"]
SIZES = ["Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan"]
FEAT_TYPES = ["origin", "general", "fighting-style", "epic-boon"]
MAGIC_SCHOOLS = [
    "abjuration", "conjuration", "divination", "enchantment",
    "evocation", "illusion", "necromancy", "transmutation",
]
RARITIES = ["common", "uncommon", "rare", "very-rare", "legendary", "artifact"]
POISON_TYPES = ["contact", "ingested", "inhaled", "injury"]
SPELL_COMPONENTS = ["V", "S", "M"]


@dataclass
class Field_:
    key: str
    label: str
    type: str  # str | text | int | bool | enum | csv | ref | ref_list | ability_bonus | component_set
    required: bool = False
    ref_category: str | None = None      # для ref / ref_list
    options: list[str] | None = None     # для enum / component_set
    min: int | None = None
    max: int | None = None
    count: int | None = None             # фиксированная длина списка (csv/ref_list/ability_bonus)
    help: str = ""

    def public(self) -> dict:
        """Представление поля для фронтенда (рисует форму по схеме)."""
        out: dict[str, Any] = {"key": self.key, "label": self.label, "type": self.type}
        for attr in ("required", "ref_category", "options", "min", "max", "count", "help"):
            value = getattr(self, attr)
            if value not in (None, False, ""):
                out[attr] = value
        return out


@dataclass
class Schema:
    category: str
    fields: list[Field_] = field(default_factory=list)

    def by_key(self, key: str) -> Field_ | None:
        return next((f for f in self.fields if f.key == key), None)

    def ref_fields(self) -> list[Field_]:
        return [f for f in self.fields if f.type in ("ref", "ref_list")]

    def public(self) -> dict:
        return {"category": self.category, "fields": [f.public() for f in self.fields]}


def _ability(key: str, label: str, **kw) -> Field_:
    return Field_(key, label, "enum", options=ABILITIES, **kw)


# ---------------- Реестр схем ----------------

SCHEMAS: dict[str, Schema] = {
    "species": Schema("species", [
        Field_("size", "Размер", "enum", options=SIZES, required=True),
        Field_("speed", "Скорость", "int", required=True, min=0, max=120,
               help="футов за ход, обычно 25–35"),
        Field_("traits", "Способности вида", "ref_list", ref_category="traits",
               help="карточки-особенности (тип traits)"),
        Field_("languages", "Языки", "csv"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "subspecies": Schema("subspecies", [
        Field_("species", "Вид-родитель", "ref", ref_category="species", required=True),
        Field_("traits", "Способности подвида", "ref_list", ref_category="traits"),
        Field_("damage_type", "Тип урона", "str", help="если подвид его даёт"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "traits": Schema("traits", [
        Field_("description", "Описание способности", "text", required=True),
    ]),
    "classes": Schema("classes", [
        Field_("hit_die", "Кость хитов", "enum", options=["6", "8", "10", "12"], required=True),
        _ability("primary_ability", "Основная характеристика", required=True),
        Field_("saving_throws", "Спасброски (две характеристики)", "csv", count=2, required=True,
               help="напр. str, con — из шести характеристик"),
        Field_("proficiencies", "Владения", "csv"),
        Field_("skill_count", "Сколько навыков выбирает", "int", min=0, max=8),
        _ability("spellcasting_ability", "Заклинательная характеристика",
                 help="если класс заклинатель"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "subclasses": Schema("subclasses", [
        Field_("class", "Класс-родитель", "ref", ref_category="classes", required=True),
        Field_("description", "Описание", "text", required=True),
    ]),
    "backgrounds": Schema("backgrounds", [
        Field_("ability_scores", "Три характеристики", "csv", count=3, required=True,
               help="ровно три из шести, напр. int, wis, cha"),
        Field_("origin_feat", "Черта происхождения", "ref", ref_category="feats"),
        Field_("proficiencies", "Владения (2 навыка + инструмент)", "csv"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "feats": Schema("feats", [
        Field_("type", "Тип черты", "enum", options=FEAT_TYPES, required=True),
        Field_("minimum_level", "Минимальный уровень", "int", min=1, max=20,
               help="пусто, если нет требования"),
        Field_("repeatable", "Можно брать повторно", "bool"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "spells": Schema("spells", [
        Field_("level", "Круг", "int", required=True, min=0, max=9,
               help="0 — заговор"),
        Field_("school", "Школа магии", "enum", options=MAGIC_SCHOOLS, required=True),
        Field_("casting_time", "Время накладывания", "str", required=True),
        Field_("range", "Дистанция", "str", required=True),
        Field_("components", "Компоненты", "component_set", options=SPELL_COMPONENTS,
               required=True),
        Field_("material", "Материалы", "str", help="если есть компонент M"),
        Field_("duration", "Длительность", "str", required=True),
        Field_("concentration", "Концентрация", "bool"),
        Field_("ritual", "Ритуал", "bool"),
        Field_("classes", "Классы", "ref_list", ref_category="classes", required=True,
               help="какие классы могут это заклинание"),
        Field_("description", "Описание", "text", required=True),
        Field_("higher_level", "На больших кругах", "text"),
    ]),
    "equipment": Schema("equipment", [
        Field_("category", "Категория снаряжения", "str", required=True),
        Field_("cost", "Стоимость", "str", help="напр. 15 gp"),
        Field_("weight", "Вес", "int", min=0, max=1000),
        Field_("damage", "Урон", "str", help="напр. 1d8 slashing"),
        Field_("mastery", "Свойство мастерства", "ref",
               ref_category="weapon-mastery-properties"),
        Field_("properties", "Свойства оружия", "ref_list",
               ref_category="weapon-properties"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "magic-items": Schema("magic-items", [
        Field_("category", "Категория", "str", required=True),
        Field_("rarity", "Редкость", "enum", options=RARITIES, required=True),
        Field_("description", "Описание", "text", required=True),
    ]),
    "skills": Schema("skills", [
        _ability("ability", "Характеристика", required=True),
        Field_("description", "Описание", "text", required=True),
    ]),
    "poisons": Schema("poisons", [
        Field_("poison_type", "Тип яда", "enum", options=POISON_TYPES, required=True),
        Field_("price", "Цена", "str"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "languages": Schema("languages", [
        Field_("type", "Тип", "str", help="Standard / Rare / …"),
        Field_("typical_speakers", "Носители", "csv"),
        Field_("description", "Описание", "text", required=True),
    ]),
    "conditions": Schema("conditions", [
        Field_("description", "Описание состояния", "text", required=True),
    ]),
    "damage-types": Schema("damage-types", [
        Field_("description", "Описание типа урона", "text", required=True),
    ]),
}


def has_schema(category: str) -> bool:
    return category in SCHEMAS


def get_schema(category: str) -> Schema | None:
    return SCHEMAS.get(category)


# ---------------- Валидация полей (синхронная) ----------------

class ValidationError(Exception):
    """Собирает список человекочитаемых ошибок полей."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_fields(category: str, data: dict) -> None:
    """Проверяет типы, диапазоны, обязательность и форму значений.

    Схема + свободные доп-поля: ключи вне схемы разрешены и не проверяются.
    Ссылки здесь НЕ резолвятся (только форма значения), см. resolve_references.
    """
    schema = SCHEMAS.get(category)
    if schema is None:
        return  # для категорий без схемы валидации полей нет
    errors: list[str] = []

    for f in schema.fields:
        present = f.key in data and data[f.key] not in (None, "", [])
        if f.required and not present:
            errors.append(f"Поле «{f.label}» обязательно")
            continue
        if not present:
            continue
        value = data[f.key]
        errors.extend(_validate_one(f, value))

    if errors:
        raise ValidationError(errors)


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    return [s.strip() for s in str(value).split(",") if s.strip()]


def _validate_one(f: Field_, value) -> list[str]:
    errs: list[str] = []
    if f.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return [f"«{f.label}» должно быть целым числом"]
        if f.min is not None and value < f.min:
            errs.append(f"«{f.label}» не меньше {f.min}")
        if f.max is not None and value > f.max:
            errs.append(f"«{f.label}» не больше {f.max}")
    elif f.type == "bool":
        if not isinstance(value, bool):
            errs.append(f"«{f.label}» должно быть да/нет")
    elif f.type in ("str", "text"):
        if not isinstance(value, str):
            errs.append(f"«{f.label}» должно быть строкой")
    elif f.type == "enum":
        if str(value) not in (f.options or []):
            errs.append(f"«{f.label}»: допустимо {', '.join(f.options or [])}")
    elif f.type == "component_set":
        items = _as_list(value)
        bad = [i for i in items if i not in (f.options or [])]
        if bad:
            errs.append(f"«{f.label}»: недопустимо {', '.join(bad)}")
    elif f.type == "csv":
        items = _as_list(value)
        if f.count is not None and len(items) != f.count:
            errs.append(f"«{f.label}»: нужно ровно {f.count} значений (сейчас {len(items)})")
        # характеристики
        if f.key in ("saving_throws", "ability_scores"):
            bad = [i for i in items if i.lower() not in ABILITIES]
            if bad:
                errs.append(f"«{f.label}»: не характеристики — {', '.join(bad)}")
    elif f.type == "ref":
        if not isinstance(value, str) or not value.strip():
            errs.append(f"«{f.label}»: нужен один индекс объекта")
    elif f.type == "ref_list":
        items = _as_list(value)
        if not items:
            errs.append(f"«{f.label}»: нужен хотя бы один объект")
        if f.count is not None and len(items) != f.count:
            errs.append(f"«{f.label}»: нужно ровно {f.count}")
    return errs


# ---------------- Резолв ссылок (асинхронный, по запросу) ----------------

@dataclass
class RefStatus:
    field_key: str
    field_label: str
    ref_category: str
    index: str
    state: str  # ok | pending | missing
    detail: str = ""


def _ref_indexes(f: Field_, data: dict) -> list[str]:
    value = data.get(f.key)
    if value in (None, "", []):
        return []
    return _as_list(value) if f.type == "ref_list" else [str(value).strip()]


async def resolve_references(db: Session, category: str, data: dict) -> list[RefStatus]:
    """Проверяет каждую ссылку объекта. Возвращает статусы по всем ссылкам.

    state:
      ok      — объект существует и «готов» (SRD или принятый community);
      pending — существует как community-объект, но ещё не принят (или draft);
      missing — не найден нигде (битая ссылка).
    Ничего не мутирует и не пишет в лог — чистая проверка.
    """
    schema = SCHEMAS.get(category)
    if schema is None:
        return []
    statuses: list[RefStatus] = []

    for f in schema.ref_fields():
        for index in _ref_indexes(f, data):
            state, detail = await _resolve_one(db, f.ref_category, index)
            statuses.append(RefStatus(
                field_key=f.key, field_label=f.label,
                ref_category=f.ref_category, index=index,
                state=state, detail=detail,
            ))
    return statuses


async def _resolve_one(db: Session, ref_category: str, index: str) -> tuple[str, str]:
    ref_category = dnd_client.resolve_category(ref_category)

    # 1. Непринятый community-объект (pending/draft/rejected) — «не готов»
    comm = (
        db.query(models.CommunityObject)
        .filter_by(category=ref_category, index=index)
        .first()
    )
    if comm is not None and comm.status != "accepted":
        return "pending", f"«{comm.name}» ещё {comm.status}"

    # 2. Есть в базе (SRD или принятый community) — «готов»
    try:
        await dnd_client.fetch_first(db, f"{ref_category}/{index}")
        return "ok", ""
    except Exception:
        # 3. Нигде нет — битая ссылка
        return "missing", f"{ref_category}/{index} не найден"


def status_from_refs(ref_statuses: list[RefStatus]) -> str:
    """Итоговый статус объекта по результату резолва ссылок."""
    if any(r.state != "ok" for r in ref_statuses):
        return "draft"
    return "pending"
