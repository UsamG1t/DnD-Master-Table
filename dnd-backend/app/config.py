"""Настройки приложения. Значения берутся из переменных окружения / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Безопасность
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    JWT_ALGORITHM: str = "HS256"
    # Токен, позволяющий зарегистрировать администратора
    ADMIN_REGISTRATION_TOKEN: str = "change-me-admin-token"

    # База данных (для продакшена: postgresql+psycopg://user:pass@host/db)
    DATABASE_URL: str = "sqlite:///./dnd_app.db"

    # Внешняя база данных DnD 5e SRD
    DND_API_BASE: str = "https://www.dnd5eapi.co"
    # TTL кеша ответов внешнего API, секунды (SRD статичен — можно долго)
    DND_CACHE_TTL: int = 60 * 60 * 24 * 7

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
