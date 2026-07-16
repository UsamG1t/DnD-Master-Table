from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import models
from .database import get_db
from .security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> models.User:
    user_id = decode_access_token(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Невалидный или просроченный токен")
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    return user


def get_current_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуются права администратора")
    return user


def get_game_membership(
    game_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> models.GameMember:
    """Проверяет, что пользователь состоит в игре, и возвращает членство."""
    member = (
        db.query(models.GameMember)
        .filter_by(game_id=game_id, user_id=user.id)
        .first()
    )
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Вы не участник этой игры")
    return member
