# BACKEND-PATCH (v7): скопируйте в app/routers/rfc.py
# и подключите в app/main.py (см. main.py из патча).
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
from ..services import dnd_client, monitor

router = APIRouter(prefix="/rfc", tags=["rfc"])

# Ник администратора сервера (хранится в коде, как договорились).
SERVER_ADMIN = "UsamG1t"

STATUS_LABELS = {
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
    created_at: datetime
    updated_at: datetime


def to_out(obj: models.CommunityObject, user: models.User) -> RfcObjectOut:
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
        created_at=obj.created_at,
        updated_at=obj.updated_at,
    )


def get_object(obj_id: int, db: Session) -> models.CommunityObject:
    obj = db.get(models.CommunityObject, obj_id)
    if obj is None:
        raise HTTPException(404, "Объект не найден")
    return obj


def build_payload(obj: models.CommunityObject) -> dict:
    """JSON в формате базы DnD: пользовательские поля + служебные."""
    payload = dict(obj.data or {})
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
        if obj.status == "accepted" or obj.author_id == user.id or is_server_admin(user):
            objects.append(to_out(obj, user))
    return {"objects": objects, "can_review": is_server_admin(user)}


@router.post("/objects", status_code=201)
def create_object(
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

    obj = models.CommunityObject(
        author_id=user.id, category=category, index=index,
        name=body.name, data=body.data, status="pending",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    monitor.log_system(
        db, f"Пользователь {user.username} ждёт ответа по объекту «{obj.name}» ({category})",
        actor=user,
    )
    return to_out(obj, user)


@router.put("/objects/{obj_id}")
def update_object(
    obj_id: int,
    body: RfcObjectIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Правка автором. После правки объект снова уходит «На обработке»."""
    obj = get_object(obj_id, db)
    if obj.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Редактировать может только автор")
    if obj.status == "accepted":
        raise HTTPException(409, "Принятый объект не редактируется — удалите и создайте новый")

    obj.name = body.name
    obj.data = body.data
    obj.status = "pending"
    obj.review_comment = None
    db.commit()
    db.refresh(obj)
    monitor.log_system(
        db, f"Пользователь {user.username} доработал объект «{obj.name}» и ждёт ответа",
        actor=user,
    )
    return to_out(obj, user)


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


@router.post("/objects/{obj_id}/accept")
def accept_object(
    obj_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Приём объекта SERVER_ADMIN: хеш + публикация в community-кеш."""
    if not is_server_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Принимать объекты может только администратор сервера")
    obj = get_object(obj_id, db)
    if obj.status == "accepted":
        raise HTTPException(409, "Объект уже принят")

    path = dnd_client.api_path(f"{obj.category}/{obj.index}")
    obj.cache_key = dnd_client.write_community_cache(path, build_payload(obj))
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
