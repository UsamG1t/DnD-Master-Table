# BACKEND-PATCH (v5): замените этим файлом app/services/dnd_client.py.
#
# Цепочка получения данных (fetch_first) для каждого запроса:
#   1. community_dnd_file_cache — принятые объекты пользователей (DnD RFC);
#   2. dnd_file_cache — статический кеш, собранный explore_2024.py;
#   3. кеш в БД (свежий по TTL);
#   4. HTTP /api/2024;
#   5. HTTP /api/2014 (имена species/subspecies маппятся в races/subraces);
#   при недоступности сети — устаревший кеш БД, иначе понятные 404/502.
# Ключ файловых кешей = sha256(полный URL)[:24] — тот же хеш, что в сборщике.
# Принятые community-объекты добавляются в списки категорий (list_category).

"""Доступ к базе данных DnD 5e (SRD API 2024, dnd5eapi.co)."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..config import get_settings

settings = get_settings()

# ------------------------------------------------------------------
# Явное описание внешнего API
# ------------------------------------------------------------------

# Версии внешнего API в порядке приоритета запросов
DND_API_PREFIXES = [p.strip() for p in os.getenv(
    "DND_API_PREFIXES", "/api/2024,/api/2014").split(",") if p.strip()]
DND_API_PREFIX = DND_API_PREFIXES[0]
# Категории, переименованные в 2024: их имена в /api/2014
LEGACY_CATEGORY_NAMES = {"species": "races", "subspecies": "subraces"}
# Каталог статического кеша (содержимое out/cache из explore_2024.py)
FILE_CACHE_DIR = Path(os.getenv("DND_FILE_CACHE_DIR", "dnd_file_cache"))
# Каталог кеша принятых community-объектов (DnD RFC)
COMMUNITY_CACHE_DIR = Path(os.getenv("DND_COMMUNITY_CACHE_DIR", "community_dnd_file_cache"))
# База, от которой сборщик считал хеши (полный URL). Если сборщик запускался
# с другим --base, переопределите этой переменной.
FILE_CACHE_URL_BASE = os.getenv("DND_FILE_CACHE_URL_BASE", settings.DND_API_BASE)

# Категории /api/2024 (проверено обходом explore_2024.py).
# Значение — краткое назначение, отдаётся фронтенду в /dnd/categories.
CATEGORIES: dict[str, str] = {
    "ability-scores": "6 характеристик, связи с навыками",
    "alignments": "10 мировоззрений",
    "backgrounds": "предыстории: +2/+1 к характеристикам, черта происхождения, "
                   "владения, пакет снаряжения или золото (equipment_options)",
    "classes": "12 классов: кость хитов, основная характеристика, спасброски, "
               "владения и выбор навыков, стартовое снаряжение, заклинательство",
    "conditions": "15 состояний (ослеплён, схвачен...)",
    "damage-types": "13 типов урона",
    "equipment": "182 предмета: оружие (damage, mastery, properties), броня, инструменты",
    "equipment-categories": "30 категорий снаряжения",
    "feats": "черты: origin / general (с 4 ур.) / fighting-style / epic-boon (с 19 ур.)",
    "features": "умения классов по уровням (категория в API ещё наполняется)",
    "languages": "19 языков",
    "magic-items": "262 магических предмета",
    "magic-schools": "8 школ магии",
    "monsters": "монстры (в 2024 пока частично)",
    "poisons": "14 ядов",
    "proficiencies": "74 владения (навыки, инструменты, доспехи, оружие)",
    "skills": "18 навыков: характеристика (ability_score) и описание",
    "species": "9 видов (бывш. расы): размер, скорость, особенности; "
               "бонусы характеристик в 2024 переехали в предыстории",
    "spells": "заклинания (в 2024 ещё не выложены — цепочка сама уходит в /api/2014)",
    "subclasses": "12 подклассов с описаниями и списком умений",
    "subspecies": "24 подвида (бывш. подрасы)",
    "traits": "67 особенностей видов",
    "weapon-mastery-properties": "8 свойств мастерства оружия (новинка 2024)",
    "weapon-properties": "10 свойств оружия",
}

# Старые имена (2014/фронтенд) -> новые имена 2024
CATEGORY_ALIASES = {"races": "species", "subraces": "subspecies"}


def resolve_category(category: str) -> str:
    return CATEGORY_ALIASES.get(category, category)


def candidate_paths(suffix: str) -> list[str]:
    """Пути запроса по версиям API в порядке приоритета.

    Для /api/2014 переименованные категории (species/subspecies)
    маппятся обратно в races/subraces."""
    parts = suffix.lstrip("/").split("/")
    paths = []
    for prefix in DND_API_PREFIXES:
        p = parts[:]
        if prefix.endswith("2014") and p and p[0] in LEGACY_CATEGORY_NAMES:
            p[0] = LEGACY_CATEGORY_NAMES[p[0]]
        paths.append(f"{prefix}/{'/'.join(p)}")
    return paths


def api_path(suffix: str, prefix: str | None = None) -> str:
    """Первый (приоритетный) путь внешнего API для суффикса."""
    if prefix is not None:
        return f"{prefix}/{suffix.lstrip('/')}"
    return candidate_paths(suffix)[0]


# ------------------------------------------------------------------
# Кеш: файловый (приоритет) -> БД -> HTTP
# ------------------------------------------------------------------

def _file_cache_key(url: str) -> str:
    """Тот же ключ, что в explore_2024.py: sha256(полный URL)[:24]."""
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _read_cache_file(directory: Path, url: str):
    if not directory.is_dir():
        return None
    file = directory / f"{_file_cache_key(url)}.json"
    if not file.exists():
        return None
    try:
        blob = json.loads(file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if blob.get("status") == 200 and blob.get("payload") is not None:
        return blob["payload"]
    return None  # в кеше лежит 404/ошибка — идём дальше по цепочке


def _file_cache_lookup(path: str):
    """Файловые кеши: сначала community (DnD RFC), затем статический."""
    url = FILE_CACHE_URL_BASE + path
    return (_read_cache_file(COMMUNITY_CACHE_DIR, url)
            or _read_cache_file(FILE_CACHE_DIR, url))


def write_community_cache(path: str, payload: dict) -> str:
    """Пишет принятый community-объект в кеш. Возвращает ключ (хеш)."""
    COMMUNITY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _file_cache_key(FILE_CACHE_URL_BASE + path)
    (COMMUNITY_CACHE_DIR / f"{key}.json").write_text(
        json.dumps({"status": 200, "payload": payload}, ensure_ascii=False)
    )
    return key


def remove_community_cache(key: str) -> None:
    file = COMMUNITY_CACHE_DIR / f"{key}.json"
    if file.exists():
        file.unlink()


async def _http_get(path: str):
    """(payload, status) либо исключение httpx при сетевой ошибке."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(settings.DND_API_BASE + path)
    if resp.status_code == 404:
        return None, 404
    resp.raise_for_status()
    return resp.json(), resp.status_code


def _db_cache_get(db: Session, path: str, allow_stale: bool = False):
    entry = db.get(models.DndCache, path)
    if entry is None:
        return None
    if allow_stale:
        return entry.payload
    fetched = entry.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - fetched < timedelta(seconds=settings.DND_CACHE_TTL):
        return entry.payload
    return None


def _db_cache_put(db: Session, path: str, payload: dict) -> None:
    entry = db.get(models.DndCache, path)
    if entry is None:
        db.add(models.DndCache(key=path, payload=payload,
                               fetched_at=datetime.now(timezone.utc)))
    else:
        entry.payload = payload
        entry.fetched_at = datetime.now(timezone.utc)
    db.commit()


async def fetch_first(db: Session, suffix: str) -> dict:
    """Цепочка получения данных, см. шапку файла."""
    paths = candidate_paths(suffix)

    # 1–2. Файловые кеши (community, затем статический) по всем версиям
    for path in paths:
        hit = _file_cache_lookup(path)
        if hit is not None:
            return hit
    # 3. Свежий кеш БД
    for path in paths:
        hit = _db_cache_get(db, path)
        if hit is not None:
            return hit
    # 4–5. Сеть: /api/2024, затем /api/2014
    network_failed = False
    last_exc: Exception | None = None
    for path in paths:
        try:
            payload, status = await _http_get(path)
        except httpx.HTTPError as exc:
            network_failed = True
            last_exc = exc
            continue
        if status == 404:
            continue
        _db_cache_put(db, path, payload)
        return payload
    # Сеть лежит — устаревший кеш БД лучше, чем ничего (SRD статичен)
    if network_failed:
        for path in paths:
            stale = _db_cache_get(db, path, allow_stale=True)
            if stale is not None:
                return stale
        raise HTTPException(
            502, f"Внешняя база данных DnD недоступна "
                 f"({type(last_exc).__name__}): {last_exc}"
        ) from last_exc
    raise HTTPException(404, "Запись не найдена в базе данных DnD")


async def _fetch_cached(db: Session, path: str) -> dict:
    """Совместимость: запрос конкретного пути через ту же цепочку кешей."""
    hit = _file_cache_lookup(path)
    if hit is not None:
        return hit
    hit = _db_cache_get(db, path)
    if hit is not None:
        return hit
    try:
        payload, status = await _http_get(path)
    except httpx.HTTPError as exc:
        stale = _db_cache_get(db, path, allow_stale=True)
        if stale is not None:
            return stale
        raise HTTPException(
            502, f"Внешняя база данных DnD недоступна ({type(exc).__name__}): {exc}"
        ) from exc
    if status == 404:
        raise HTTPException(404, "Запись не найдена в базе данных DnD")
    _db_cache_put(db, path, payload)
    return payload


async def list_category(db: Session, category: str) -> list[dict]:
    category = resolve_category(category)
    if category not in CATEGORIES:
        raise HTTPException(404, f"Неизвестная категория: {category}")
    payload = await fetch_first(db, category)
    items = [
        {"index": item["index"], "name": item["name"]}
        for item in payload.get("results", [])
    ]
    # Принятые объекты сообщества (DnD RFC) вливаются в списки
    community = (
        db.query(models.CommunityObject)
        .filter_by(category=category, status="accepted")
        .order_by(models.CommunityObject.name)
        .all()
    )
    items += [{"index": o.index, "name": o.name, "community": True} for o in community]
    return items


async def get_raw(db: Session, category: str, index: str) -> dict:
    category = resolve_category(category)
    if category not in CATEGORIES:
        raise HTTPException(404, f"Неизвестная категория: {category}")
    return await fetch_first(db, f"{category}/{index}")


# ------------------------------------------------------------------
# Сжатые описания (кнопка info)
# ------------------------------------------------------------------

def _desc(d: dict) -> str:
    """В 2024 описание — строка `description`, в 2014 — список `desc`."""
    value = d.get("description") or d.get("desc")
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return str(value or "")


def _names(items) -> list[str]:
    return [i.get("name", "") for i in (items or [])]


def _choice_descs(choices) -> list[str]:
    """Человекочитаемые desc из структур Choice (выбор владений/снаряжения)."""
    return [c.get("desc", "") for c in (choices or []) if c.get("desc")]


def _compact_spell(d: dict) -> dict:
    return {
        "name": d.get("name"),
        "level": d.get("level"),
        "school": (d.get("school") or {}).get("name"),
        "casting_time": d.get("casting_time"),
        "range": d.get("range"),
        "components": ", ".join(d.get("components", [])),
        "material": d.get("material"),
        "duration": d.get("duration"),
        "concentration": d.get("concentration"),
        "ritual": d.get("ritual"),
        "classes": _names(d.get("classes")),
        "description": _desc(d),
        "higher_level": "\n".join(d.get("higher_level", [])) or None,
    }


def _compact_class(d: dict) -> dict:
    # primary_ability: {"desc": "Wisdom", "ability_scores": [ref, ...]}
    primary = d.get("primary_ability") or {}
    return {
        "name": d.get("name"),
        "hit_die": f"d{d.get('hit_die')}" if d.get("hit_die") else None,
        "primary_ability": primary.get("desc") or ", ".join(_names(primary.get("ability_scores"))),
        "saving_throws": _names(d.get("saving_throws")),
        "proficiencies": _names(d.get("proficiencies")),
        "skill_choices": _choice_descs(d.get("proficiency_choices")),
        "starting_equipment": _choice_descs(d.get("starting_equipment_options")),
        "spellcasting_ability": ((d.get("spellcasting") or {}).get("spellcasting_ability") or {}).get("name"),
        "subclasses": _names(d.get("subclasses")),
    }


def _compact_species(d: dict) -> dict:
    return {
        "name": d.get("name"),
        "type": d.get("type"),
        "size": d.get("size"),
        "speed": d.get("speed"),
        "traits": _names(d.get("traits")),
        "subspecies": _names(d.get("subspecies")),
        # На случай данных 2014 (алиас races)
        "ability_bonuses": [
            f"{(b.get('ability_score') or {}).get('name')} +{b.get('bonus')}"
            for b in d.get("ability_bonuses", [])
        ],
    }


def _compact_subspecies(d: dict) -> dict:
    return {
        "name": d.get("name"),
        "species": (d.get("species") or {}).get("name"),
        "damage_type": (d.get("damage_type") or {}).get("name"),
        "traits": _names(d.get("traits")),
        "description": _desc(d),
    }


def _compact_background(d: dict) -> dict:
    feat = d.get("feat") or {}
    feat_name = feat.get("name")
    if feat_name and feat.get("note"):
        feat_name = f"{feat_name} ({feat['note']})"
    return {
        "name": d.get("name"),
        # В 2024 бонусы характеристик даёт предыстория: +2/+1 или +1/+1/+1
        # к трём указанным характеристикам
        "ability_scores": _names(d.get("ability_scores")),
        "origin_feat": feat_name,
        "proficiencies": _names(d.get("proficiencies")),
        # Пакет снаряжения или золото — текстом, как источник для инвентаря
        "equipment_options": _choice_descs(d.get("equipment_options")),
        "description": _desc(d),
    }


def _compact_feat(d: dict) -> dict:
    prereq = d.get("prerequisites") or {}
    prereqs = []
    if isinstance(prereq, dict):
        if prereq.get("minimum_level"):
            prereqs.append(f"уровень {prereq['minimum_level']}+")
        if prereq.get("feature_named"):
            prereqs.append(f"умение «{prereq['feature_named']}»")
    elif isinstance(prereq, list):  # формат 2014
        for p in prereq:
            ability = (p.get("ability_score") or {}).get("name")
            if ability:
                prereqs.append(f"{ability} >= {p.get('minimum_score')}")
    return {
        "name": d.get("name"),
        "type": d.get("type"),  # origin / general / fighting-style / epic-boon
        "prerequisites": prereqs,
        "repeatable": d.get("repeatable"),
        "description": _desc(d),
    }


def _compact_equipment(d: dict) -> dict:
    cost = d.get("cost") or {}
    damage = d.get("damage") or {}
    two_handed = d.get("two_handed_damage") or {}
    return {
        "name": d.get("name"),
        "category": ", ".join(_names(d.get("equipment_categories")))
                    or (d.get("equipment_category") or {}).get("name"),
        "cost": f"{cost.get('quantity')} {cost.get('unit')}" if cost else None,
        "weight": d.get("weight"),
        "damage": (
            f"{damage.get('damage_dice')} ({(damage.get('damage_type') or {}).get('name')})"
            if damage else None
        ),
        "two_handed_damage": (
            f"{two_handed.get('damage_dice')} ({(two_handed.get('damage_type') or {}).get('name')})"
            if two_handed else None
        ),
        "mastery": (d.get("mastery") or {}).get("name"),  # новинка 2024
        "properties": _names(d.get("properties")),
        "range": (
            f"{d['range'].get('normal')}/{d['range'].get('long')}"
            if isinstance(d.get("range"), dict) and d["range"].get("long")
            else (d.get("range") or {}).get("normal") if isinstance(d.get("range"), dict) else None
        ),
        "armor_class": (d.get("armor_class") or {}).get("base"),
        "notes": d.get("notes"),
        "description": _desc(d),
    }


def _compact_skill(d: dict) -> dict:
    return {
        "name": d.get("name"),
        "ability": (d.get("ability_score") or {}).get("name"),
        "description": _desc(d),
    }


def _compact_trait(d: dict) -> dict:
    return {
        "name": d.get("name"),
        "species": _names(d.get("species")) or (d.get("species") or {}).get("name")
                   if isinstance(d.get("species"), (list, dict)) else None,
        "description": _desc(d),
    }


def _compact_subclass(d: dict) -> dict:
    return {
        "name": d.get("name"),
        "class": (d.get("class") or {}).get("name"),
        "summary": d.get("summary"),
        "features": _names(d.get("features")),
        "description": _desc(d),
    }


def _compact_generic(d: dict) -> dict:
    return {
        "name": d.get("name"),
        "description": _desc(d),
    }


_TRANSFORMERS = {
    "spells": _compact_spell,
    "classes": _compact_class,
    "species": _compact_species,
    "subspecies": _compact_subspecies,
    "backgrounds": _compact_background,
    "feats": _compact_feat,
    "equipment": _compact_equipment,
    "magic-items": _compact_equipment,
    "skills": _compact_skill,
    "traits": _compact_trait,
    "subclasses": _compact_subclass,
}


def compact(category: str, raw: dict) -> dict:
    """Сжатое описание + ссылка на полное описание в базе-источнике."""
    category = resolve_category(category)
    transformer = _TRANSFORMERS.get(category, _compact_generic)
    result = {k: v for k, v in transformer(raw).items() if v not in (None, "", [])}
    result["source_url"] = settings.DND_API_BASE + raw.get(
        "url", api_path(f"{category}/{raw.get('index', '')}")
    )
    if raw.get("community"):
        result["community"] = True
    return result
