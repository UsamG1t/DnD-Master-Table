# BACKEND-PATCH (v7): замените этим файлом app/main.py (settings, лог запросов).
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import (admin, auth, characters, dnd_data, game_ws, games, rfc,
                      server_settings)
from .security import decode_access_token
from .services import cache_builder, monitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Для старта достаточно create_all; в продакшене замените на Alembic-миграции
    Base.metadata.create_all(bind=engine)
    # Если dnd_file_cache отсутствует целиком — собираем его полным обходом
    # внешнего API в фоне; сервер стартует и работает, не дожидаясь сборки
    asyncio.create_task(cache_builder.startup_check())
    yield


app = FastAPI(
    title="DnD Characters Backend",
    description="Персонажи, игры и данные DnD 5e для веб- и Android-клиентов",
    version="0.1.0",
    lifespan=lifespan,
)

# В продакшене сузьте до доменов вашего frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(dnd_data.router)
app.include_router(characters.router)
app.include_router(games.router)
app.include_router(rfc.router)
app.include_router(server_settings.router)
app.include_router(game_ws.router)


@app.middleware("http")
async def request_logger(request: Request, call_next):
    """Лог запросов для страницы Settings (кольцевой буфер в памяти)."""
    start = time.perf_counter()
    response = await call_next(request)
    user_id = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        user_id = decode_access_token(auth_header[7:])
    monitor.add_request({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "ms": round((time.perf_counter() - start) * 1000, 1),
        "user_id": user_id,
    })
    return response


@app.get("/health")
def health():
    return {"status": "ok"}
