"""Общие фикстуры для тестов backend.

Поднимают приложение на in-memory SQLite, подменяют внешнюю сеть базы DnD
(dnd_client.fetch_first) управляемым набором «существующих» индексов и дают
готовых клиентов: обычного пользователя, второго пользователя и SERVER_ADMIN.

Запуск (из каталога dnd-backend, где лежит app/):
    pip install -r requirements.txt pytest
    PYTHONPATH=. pytest ../tests/test_backend -v

Если fastapi/sqlalchemy не установлены, тесты пропускаются со скипом, а не
падают (в песочнице без сети зависимости не ставятся).
"""
import os
import sys
import tempfile

import pytest

# Изоляция путей ДО импорта app: своя БД и каталоги кешей во временной папке
_TMP = tempfile.mkdtemp(prefix="rfc-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("DND_FILE_CACHE_DIR", f"{_TMP}/dnd_file_cache")
os.environ.setdefault("DND_COMMUNITY_CACHE_DIR", f"{_TMP}/community_cache")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_REGISTRATION_TOKEN", "test-admin-token")

# app/ должен быть импортируемым: pytest запускается с PYTHONPATH=dnd-backend
fastapi = pytest.importorskip("fastapi", reason="fastapi не установлен")
pytest.importorskip("sqlalchemy", reason="sqlalchemy не установлен")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import dnd_client  # noqa: E402


# ---------- Управляемая «база SRD» вместо сети ----------

class FakeSRD:
    """Замена dnd_client.fetch_first: индекс существует, если он добавлен.

    Учитывает и принятые community-объекты в БД (как реальная цепочка),
    чтобы принятые RFC-объекты резолвились как ok.
    """
    def __init__(self):
        self.indexes: set[str] = set()

    def add(self, *suffixes: str):
        self.indexes.update(suffixes)

    async def fetch_first(self, db, suffix):
        from app import models
        parts = suffix.split("/")
        # Запрос списка категории (без index) — отдаём пустой список SRD;
        # реальные записи подмешивает list_category из community-объектов
        if len(parts) == 1:
            return {"count": 0, "results": []}
        cat, idx = parts
        obj = (db.query(models.CommunityObject)
               .filter_by(category=cat, index=idx, status="accepted").first())
        if obj is not None:
            return {"index": idx, "name": obj.name}
        if suffix in self.indexes:
            return {"index": idx, "name": idx.replace("-", " ").title()}
        raise Exception(f"not found: {suffix}")


@pytest.fixture
def srd(monkeypatch):
    fake = FakeSRD()
    monkeypatch.setattr(dnd_client, "fetch_first", fake.fetch_first)
    return fake


# ---------- БД и клиент ----------

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield TestingSessionLocal
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    def override_get_db():
        db = db_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------- Пользователи ----------

def _register_and_login(client, username, password="pass1234", admin_token=None):
    body = {"username": username, "email": f"{username}@t.test", "password": password}
    if admin_token:
        body["admin_token"] = admin_token
    r = client.post("/auth/register", json=body)
    assert r.status_code in (200, 201), r.text
    r = client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def author(client):
    # обычный пользователь (первый — может оказаться авто-админом в некоторых
    # системах, поэтому имя не совпадает с SERVER_ADMIN)
    return _register_and_login(client, "alice")


@pytest.fixture
def other(client):
    return _register_and_login(client, "bob")


@pytest.fixture
def admin(client):
    # SERVER_ADMIN определяется ником "UsamG1t" в rfc.py
    from app.routers.rfc import SERVER_ADMIN
    return _register_and_login(client, SERVER_ADMIN)
