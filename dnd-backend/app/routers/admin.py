from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin)])


@router.get("/users", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.query(models.User).order_by(models.User.id).all()


@router.post("/users/{user_id}/toggle-admin", response_model=schemas.UserOut)
def toggle_admin(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    user.is_admin = not user.is_admin
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    db.delete(user)
    db.commit()


@router.delete("/cache", status_code=204)
def clear_dnd_cache(db: Session = Depends(get_db)):
    """Полная очистка кеша внешней базы DnD."""
    db.query(models.DndCache).delete()
    db.commit()
