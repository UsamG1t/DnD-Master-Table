# BACKEND-PATCH (v6): замените этим файлом app/routers/dnd_data.py.
# /dnd/categories теперь отдаёт явное описание категорий 2024 и алиасов;
# заклинания классов идут через fallback-версию API (в 2024 их ещё нет).
# Добавлен эндпоинт GET /dnd/classes/{index}/spells — список заклинаний,
# доступных классу (нужен вкладке «Заклинания» фронтенда).

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..services import cache_builder, dnd_client

router = APIRouter(prefix="/dnd", tags=["dnd"], dependencies=[Depends(get_current_user)])


@router.get("/categories")
def categories() -> dict:
    """Категории базы 2024 с назначением + алиасы старых имён."""
    return {
        "prefixes": dnd_client.DND_API_PREFIXES,
        "categories": dnd_client.CATEGORIES,
        "aliases": dnd_client.CATEGORY_ALIASES,
    }


@router.post("/cache/rebuild")
async def rebuild_cache():
    """Запуск полного обхода внешнего API и пересборки dnd_file_cache.

    Сборка идёт в фоне (сервер продолжает работать), готовый кеш атомарно
    подменяет старый. Повторный вызов при идущей сборке ничего не запускает.
    Куда монтировать ручку (права доступа) — решим отдельно; пока доступна
    любому аутентифицированному пользователю.
    """
    started = cache_builder.start_rebuild()
    return {"started": started, **cache_builder.status()}


@router.get("/cache/status")
def cache_status():
    """Состояние статического кеша и текущей сборки."""
    return cache_builder.status()


@router.get("/classes/{index}/spells")
async def class_spells(index: str, db: Session = Depends(get_db)):
    """Заклинания, доступные классу: [{index, name, level}].

    Источник — /api/classes/{index}/spells внешней базы; ответ кешируется
    тем же механизмом, что и остальные запросы.
    """
    payload = await dnd_client.fetch_first(db, f"classes/{index}/spells")
    return [
        {"index": i["index"], "name": i["name"], "level": i.get("level")}
        for i in payload.get("results", [])
    ]


@router.get("/{category}")
async def list_items(category: str, db: Session = Depends(get_db)):
    """Список записей категории: [{index, name}] — для всплывающих списков."""
    return await dnd_client.list_category(db, category)


@router.get("/{category}/{index}")
async def get_item(category: str, index: str, db: Session = Depends(get_db)):
    """Сжатое описание для кнопки info + source_url на полное описание."""
    raw = await dnd_client.get_raw(db, category, index)
    return dnd_client.compact(category, raw)


@router.get("/{category}/{index}/raw")
async def get_item_raw(category: str, index: str, db: Session = Depends(get_db)):
    """Полный необработанный ответ базы-источника (для сложной логики фронта,
    например списков заклинаний, доступных выбранному классу)."""
    return await dnd_client.get_raw(db, category, index)
