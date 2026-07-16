from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post("/register", response_model=schemas.UserOut, status_code=201)
def register(body: schemas.UserRegister, db: Session = Depends(get_db)):
    if db.query(models.User).filter_by(username=body.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Имя пользователя занято")
    if db.query(models.User).filter_by(email=body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован")

    is_admin = False
    if body.admin_token is not None:
        if body.admin_token != settings.ADMIN_REGISTRATION_TOKEN:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный админ-токен")
        is_admin = True

    user = models.User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=schemas.TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter_by(username=form.username).first()
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    return schemas.TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user
