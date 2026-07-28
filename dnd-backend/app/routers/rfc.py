# BACKEND-PATCH (v8): замените app/routers/rfc.py.
# Добавлены: схемы полей по типам объектов (rfc_schema), валидация,
# статус draft («Не готов к модерации»), проверка ссылок по запросу
# и при приёме админом.
"""DnD RFC: объекты сообщества.

Любой пользователь описывает объект любой категории базы DnD (класс, вид,
предмет, заклинание...) и отправляет его на рассмотрение. Объект висит со
статусом «На обработке», пока SERVER_ADMIN не примет или не отклонит его.
Принятый объект получает хеш и выкладывается в community_dnd_file_cache —
приоритетный кеш базы DnD, после чего появляется в списках /dnd/{category}.
Отклонённый («Отклонён с возможностью доработки») объект автор может
отредактировать и отправить снова. Удалять объект могут автор и SERVER_ADMIN.
"""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..deps import get_current_user
from ..services import dnd_client, monitor, rfc_schema

router = APIRouter(prefix="/rfc", tags=["rfc"])

# Ник администратора сервера (хранится в коде, как договорились).
SERVER_ADMIN = "UsamG1t"

STATUS_LABELS = {
    "draft": "Не готов к модерации",
    "pending": "На обработке",
    "accepted": "Принят",
    "rejected": "Отклонён с возможностью доработки",
}


def is_server_admin(user: models.User) -> bool:
    return user.username == SERVER_ADMIN or user.is_admin


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9а-яё]+", "-", name.lower()).strip("-")
    return slug or "object"


# ---------------- Схемы ----------------

class RfcObjectIn(BaseModel):
    category: str
    name: str = Field(min_length=1, max_length=128)
    data: dict = Field(default_factory=dict)


class RfcReject(BaseModel):
    comment: str = Field(default="", max_length=2000)


class RfcObjectOut(BaseModel):
    id: int
    category: str
    index: str
    name: str
    data: dict
    status: str
    status_label: str
    review_comment: str | None
    author_id: int
    author_name: str
    is_mine: bool
    ref_report: list[dict] | None = None
    created_at: datetime
    updated_at: datetime


def to_out(obj: models.CommunityObject, user: models.User,
           ref_report: list[dict] | None = None) -> RfcObjectOut:
    return RfcObjectOut(
        id=obj.id,
        category=obj.category,
        index=obj.index,
        name=obj.name,
        data=obj.data or {},
        status=obj.status,
        status_label=STATUS_LABELS.get(obj.status, obj.status),
        review_comment=obj.review_comment,
        author_id=obj.author_id,
        author_name=obj.author.username,
        is_mine=obj.author_id == user.id,
        ref_report=ref_report,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def _refs_to_dicts(ref_statuses) -> list[dict]:
    return [
        {
            "field_key": r.field_key,
            "field_label": r.field_label,
            "ref_category": r.ref_category,
            "index": r.index,
            "state": r.state,
            "detail": r.detail,
        }
        for r in ref_statuses
    ]


async def _validate_and_status(db: Session, category: str, data: dict) -> tuple[str, list]:
    """Валидирует поля (иначе 422) и по ссылкам определяет статус.

    Возвращает (status, ref_statuses): pending если все ссылки готовы,
    иначе draft. Поля-ошибки бросают 422 ДО резолва ссылок.
    """
    try:
        rfc_schema.validate_fields(category, data)
    except rfc_schema.ValidationError as e:
        raise HTTPException(422, {"field_errors": e.errors})
    refs = await rfc_schema.resolve_references(db, category, data)
    return rfc_schema.status_from_refs(refs), refs


def get_object(obj_id: int, db: Session) -> models.CommunityObject:
    obj = db.get(models.CommunityObject, obj_id)
    if obj is None:
        raise HTTPException(404, "Объект не найден")
    return obj


async def _ref_to_apiref(db: Session, ref_category: str, index: str) -> dict:
    """Ссылка index -> APIReference {index, name, url}, как в SRD.

    Имя берём из community-объекта или из базы; если не нашли — оставляем
    index как name (объект всё равно уже прошёл проверку ссылок)."""
    ref_category = dnd_client.resolve_category(ref_category)
    name = index
    comm = db.query(models.CommunityObject).filter_by(
        category=ref_category, index=index).first()
    if comm is not None:
        name = comm.name
    else:
        try:
            raw = await dnd_client.fetch_first(db, f"{ref_category}/{index}")
            name = raw.get("name", index)
        except Exception:
            pass
    return {"index": index, "name": name,
            "url": "/" + dnd_client.api_path(f"{ref_category}/{index}").lstrip("/")}


async def build_payload(db: Session, obj: models.CommunityObject) -> dict:
    """JSON в формате базы DnD: ссылки -> APIReference, служебные поля.

    Совместим с dnd_client.compact(): ссылочные поля становятся списками/
    объектами {index, name, url}, поэтому принятый объект корректно
    отображается в info-карточках и списках редактора.
    """
    payload = dict(obj.data or {})
    schema = rfc_schema.get_schema(obj.category)
    if schema is not None:
        for f in schema.ref_fields():
            raw = payload.get(f.key)
            if raw in (None, "", []):
                continue
            indexes = (raw if isinstance(raw, list)
                       else [s.strip() for s in str(raw).split(",") if s.strip()])
            refs = [await _ref_to_apiref(db, f.ref_category, i) for i in indexes]
            payload[f.key] = refs if f.type == "ref_list" else refs[0]

    payload["index"] = obj.index
    payload["name"] = obj.name
    payload["url"] = "/" + dnd_client.api_path(f"{obj.category}/{obj.index}").lstrip("/")
    payload["community"] = True
    payload["author"] = obj.author.username
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


# ---------------- Эндпоинты ----------------

@router.get("/categories")
def rfc_categories() -> dict:
    """Категории, доступные для описания объектов (всё известное API)."""
    return dnd_client.CATEGORIES


@router.get("/objects")
def list_objects(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Принятые объекты видны всем; свои — в любом статусе;
    SERVER_ADMIN видит всё (в т.ч. чужие «На обработке»)."""
    q = db.query(models.CommunityObject).order_by(models.CommunityObject.updated_at.desc())
    objects = []
    for obj in q.all():
        mine = obj.author_id == user.id
        # draft («Не готов к модерации») виден только автору;
        # админу draft чужих не показывается — он ещё не на модерации
        if obj.status == "draft" and not mine:
            continue
        if obj.status == "accepted" or mine or is_server_admin(user):
            objects.append(to_out(obj, user))
    return {"objects": objects, "can_review": is_server_admin(user)}


@router.get("/schema/{category}")
def rfc_schema_for(category: str):
    """Схема полей категории — фронтенд рисует форму по ней."""
    category = dnd_client.resolve_category(category)
    schema = rfc_schema.get_schema(category)
    if schema is None:
        # категория без строгой схемы — только имя + описание + свободный JSON
        return {"category": category, "fields": [], "strict": False}
    return {**schema.public(), "strict": True}


@router.post("/objects", status_code=201)
async def create_object(
    body: RfcObjectIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    category = dnd_client.resolve_category(body.category)
    if category not in dnd_client.CATEGORIES:
        raise HTTPException(404, f"Неизвестная категория: {body.category}")

    index = slugify(body.name)
    if db.query(models.CommunityObject).filter_by(category=category, index=index).first():
        raise HTTPException(409, "Объект с таким именем в этой категории уже есть")

    obj_status, refs = await _validate_and_status(db, category, body.data)

    obj = models.CommunityObject(
        author_id=user.id, category=category, index=index,
        name=body.name, data=body.data, status=obj_status,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    if obj_status == "pending":
        monitor.log_system(
            db, f"Пользователь {user.username} ждёт ответа по объекту «{obj.name}» ({category})",
            actor=user,
        )
    return to_out(obj, user, _refs_to_dicts(refs))


@router.put("/objects/{obj_id}")
async def update_object(
    obj_id: int,
    body: RfcObjectIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Правка автором. Статус пересчитывается по ссылкам (draft/pending)."""
    obj = get_object(obj_id, db)
    if obj.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Редактировать может только автор")
    if obj.status == "accepted":
        raise HTTPException(409, "Принятый объект не редактируется — удалите и создайте новый")

    obj_status, refs = await _validate_and_status(db, obj.category, body.data)

    obj.name = body.name
    obj.data = body.data
    obj.status = obj_status
    obj.review_comment = None
    db.commit()
    db.refresh(obj)
    if obj_status == "pending":
        monitor.log_system(
            db, f"Пользователь {user.username} доработал объект «{obj.name}» и ждёт ответа",
            actor=user,
        )
    return to_out(obj, user, _refs_to_dicts(refs))


@router.delete("/objects/{obj_id}", status_code=204)
def delete_object(
    obj_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Удаление: автор или SERVER_ADMIN. Принятый объект убирается и из кеша."""
    obj = get_object(obj_id, db)
    if obj.author_id != user.id and not is_server_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Удалять может автор или администратор сервера")
    if obj.cache_key:
        dnd_client.remove_community_cache(obj.cache_key)
    name, author_name = obj.name, obj.author.username
    db.delete(obj)
    db.commit()
    if author_name == user.username:
        monitor.log_system(db, f"Пользователь {user.username} удалил объект «{name}»", actor=user)
    else:
        monitor.log_system(db, f"Вы удалили объект «{name}» (автор: {author_name})", actor=user)


@router.post("/objects/{obj_id}/submit")
async def submit_object(
    obj_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """«Проверить и отправить»: перевод draft -> pending по запросу автора.

    Проверяет поля и все ссылки. Если каждая ссылка ведёт на принятый
    объект — статус становится pending (виден админу). Если хоть одна
    ссылка на непринятый объект — остаётся draft с пояснением. Битые
    ссылки (объект удалён/отклонён) — тоже draft, автор правит сам.
    """
    obj = get_object(obj_id, db)
    if obj.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Отправлять может только автор")
    if obj.status not in ("draft", "rejected"):
        raise HTTPException(409, "Объект не в состоянии черновика")

    try:
        rfc_schema.validate_fields(obj.category, obj.data or {})
    except rfc_schema.ValidationError as e:
        raise HTTPException(422, {"field_errors": e.errors})

    refs = await rfc_schema.resolve_references(db, obj.category, obj.data or {})
    report = _refs_to_dicts(refs)
    not_ready = [r for r in refs if r.state != "ok"]

    if not_ready:
        pending = [r for r in not_ready if r.state == "pending"]
        missing = [r for r in not_ready if r.state == "missing"]
        parts = []
        if pending:
            parts.append("не все внутренние объекты приняты")
        if missing:
            parts.append("есть битые ссылки — отредактируйте объект")
        obj.status = "draft"
        db.commit()
        db.refresh(obj)
        return {
            "ok": False,
            "message": "; ".join(parts),
            "object": to_out(obj, user, report),
        }

    obj.status = "pending"
    obj.review_comment = None
    db.commit()
    db.refresh(obj)
    monitor.log_system(
        db, f"Пользователь {user.username} отправил объект «{obj.name}» на модерацию",
        actor=user,
    )
    return {"ok": True, "message": "Отправлено на модерацию",
            "object": to_out(obj, user, report)}


@router.post("/objects/{obj_id}/accept")
async def accept_object(
    obj_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Приём объекта SERVER_ADMIN: проверка ссылок, хеш, публикация в кеш.

    Повторная проверка ссылок в момент приёма (сервер не мониторит
    консистентность постоянно). Если ссылка сломалась после постановки
    в pending — приём отклоняется с ворнингом в лог администратора.
    """
    if not is_server_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Принимать объекты может только администратор сервера")
    obj = get_object(obj_id, db)
    if obj.status == "accepted":
        raise HTTPException(409, "Объект уже принят")
    if obj.status == "draft":
        raise HTTPException(409, "Объект ещё не готов к модерации")

    refs = await rfc_schema.resolve_references(db, obj.category, obj.data or {})
    broken = [r for r in refs if r.state != "ok"]
    if broken:
        # Что-то удалили/отклонили между постановкой в pending и приёмом
        obj.status = "draft"
        db.commit()
        detail = ", ".join(f"{r.field_label}: {r.index} ({r.state})" for r in broken)
        monitor.log_system(
            db,
            f"Что-то не так с объектом «{obj.name}»: ссылки недоступны ({detail}). "
            f"Мы уже разбираемся с проблемой. Объект возвращён в черновики.",
            actor=user,
        )
        raise HTTPException(409, {
            "message": "Ссылки объекта больше не действительны, приём отменён",
            "broken_refs": _refs_to_dicts(broken),
        })

    # Совместимость с существующими объектами того же типа: дубликат index,
    # неизвестные enum-значения, тип родителя. Несовместимость -> отказ.
    issues = await rfc_schema.check_compatibility(
        db, obj.category, obj.index, obj.data or {}, self_id=obj.id)
    if issues:
        detail = ", ".join(f"{i.field}: {i.detail}" for i in issues)
        monitor.log_system(
            db, f"Приём объекта «{obj.name}» отклонён — несовместимость: {detail}",
            actor=user,
        )
        raise HTTPException(409, {
            "message": "Объект несовместим с базой, приём отменён",
            "issues": [{"kind": i.kind, "field": i.field, "detail": i.detail}
                       for i in issues],
        })

    path = dnd_client.api_path(f"{obj.category}/{obj.index}")
    payload = await build_payload(db, obj)
    obj.cache_key = dnd_client.write_community_cache(path, payload)
    obj.status = "accepted"
    obj.review_comment = None
    db.commit()
    db.refresh(obj)
    monitor.log_system(
        db, f"Вы приняли объект «{obj.name}» (автор: {obj.author.username})", actor=user,
    )
    return to_out(obj, user)


@router.post("/objects/{obj_id}/reject")
def reject_object(
    obj_id: int,
    body: RfcReject,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отклонение с возможностью доработки (комментарий — автору)."""
    if not is_server_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Отклонять объекты может только администратор сервера")
    obj = get_object(obj_id, db)
    if obj.status == "accepted":
        raise HTTPException(409, "Объект уже принят — сначала удалите его")
    obj.status = "rejected"
    obj.review_comment = body.comment or None
    db.commit()
    db.refresh(obj)
    monitor.log_system(
        db, f"Вы отправили на доработку объект «{obj.name}» (автор: {obj.author.username})",
        actor=user,
    )
    return to_out(obj, user)
