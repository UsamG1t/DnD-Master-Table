# BACKEND-PATCH (v5): замените этим файлом app/main.py (подключён роутер rfc).
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import admin, auth, characters, dnd_data, game_ws, games, rfc


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Для старта достаточно create_all; в продакшене замените на Alembic-миграции
    Base.metadata.create_all(bind=engine)
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
app.include_router(game_ws.router)


@app.get("/health")
def health():
    return {"status": "ok"}
