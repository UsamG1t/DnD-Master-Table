# BACKEND-PATCH (v6): НОВЫЙ файл — скопируйте в app/services/cache_builder.py.
"""Автосборка статического кеша базы DnD (dnd_file_cache).

При старте backend: если каталога кеша нет целиком — в фоне (не мешая
запуску сервера) выполняется полный обход внешнего API. Алгоритм и формат
те же, что у explore_2024.py: ключ файла = sha256(полный URL)[:24],
содержимое {"status": ..., "payload": ...} — кеши взаимозаменяемы.

Сборка идёт во временный каталог "<dir>.building" и атомарно подменяет
целевой по завершении: недособранный кеш никогда не виден цепочке чтения,
а до окончания сборки запросы обслуживаются по остальной цепочке (БД -> HTTP).

Обходятся: корень /api/2024 со всеми категориями и записями, подресурсы
классов/подклассов, а также /api/2014/spells и /api/2014/classes/{i}/spells —
заклинания, которых в 2024 ещё нет, чтобы и они работали офлайн.
"""
import asyncio
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings
from . import dnd_client

settings = get_settings()

# Пауза между запросами к внешней базе, сек
REQUEST_DELAY = float(os.getenv("DND_CACHE_BUILD_DELAY", "0.05"))

# Подресурсы, которые кешируем вместе с записями (404 тоже кешируются —
# цепочка чтения их пропускает, зато не ходит за ними в сеть повторно)
SUBRESOURCES = {
    "classes": ["levels", "spells", "features", "proficiencies", "multi-classing"],
    "subclasses": ["levels", "features"],
}

_state = {
    "running": False,
    "phase": "idle",          # idle | <категория> | spells-2014 | swap | done | error
    "categories_done": 0,
    "categories_total": 0,
    "files_written": 0,
    "requests_made": 0,
    "warnings": [],
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_state_lock = threading.Lock()
_worker: threading.Thread | None = None


def status() -> dict:
    with _state_lock:
        snapshot = dict(_state)
        snapshot["warnings"] = list(_state["warnings"])[-10:]
    snapshot["cache_dir"] = str(dnd_client.FILE_CACHE_DIR)
    snapshot["cache_exists"] = dnd_client.FILE_CACHE_DIR.is_dir()
    if snapshot["cache_exists"]:
        snapshot["cache_files"] = sum(
            1 for _ in dnd_client.FILE_CACHE_DIR.glob("*.json")
        )
    return snapshot


def _set(**kwargs) -> None:
    with _state_lock:
        _state.update(kwargs)


def _warn(message: str) -> None:
    with _state_lock:
        _state["warnings"].append(message)
    print(f"[cache_builder] !! {message}", flush=True)


def _fetch(url: str):
    """GET внешней базы. Возвращает (payload | None, http_status | 0)."""
    _set(requests_made=_state["requests_made"] + 1)
    time.sleep(REQUEST_DELAY)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "dnd-app-cache-builder/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        _warn(f"сеть: {url}: {e}")
        return None, 0


def _store(build_dir: Path, url: str, payload, status_code: int) -> None:
    """Файл кеша в формате explore_2024.py: sha256(url)[:24].json."""
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    (build_dir / f"{key}.json").write_text(
        json.dumps({"status": status_code, "payload": payload}, ensure_ascii=False)
    )
    _set(files_written=_state["files_written"] + 1)


def _fetch_and_store(build_dir: Path, url: str):
    payload, code = _fetch(url)
    if code == 0:  # сетевые сбои не кешируем — пусть цепочка попробует ещё раз
        return None
    _store(build_dir, url, payload, code)
    return payload


def _crawl(build_dir: Path) -> None:
    host = settings.DND_API_BASE
    base_url = host + dnd_client.DND_API_PREFIX  # обычно /api/2024

    root = _fetch_and_store(build_dir, base_url)
    if not root:
        raise RuntimeError(f"корень {base_url} недоступен")

    categories = dict(sorted(root.items()))
    _set(categories_total=len(categories))

    for name, rel_url in categories.items():
        _set(phase=name)
        listing = _fetch_and_store(build_dir, host + rel_url)
        if not listing:
            _warn(f"категория {name}: список недоступен")
            _set(categories_done=_state["categories_done"] + 1)
            continue

        results = listing.get("results", [])
        for item in results:
            if _fetch_and_store(build_dir, host + item["url"]) is None:
                _warn(f"{item['url']}: запись недоступна")

        # Подресурсы (у первых записей достаточно, 404 тоже полезно закешировать)
        for sub in SUBRESOURCES.get(name, []):
            for item in results:
                _fetch_and_store(build_dir, f"{host}{item['url']}/{sub}")

        _set(categories_done=_state["categories_done"] + 1)
        print(f"[cache_builder] {name}: {len(results)} записей", flush=True)

    # Заклинания из /api/2014 — их в 2024 ещё нет, а цепочка чтения их ждёт
    _set(phase="spells-2014")
    spells_url = f"{host}/api/2014/spells"
    listing = _fetch_and_store(build_dir, spells_url)
    if listing:
        for item in listing.get("results", []):
            _fetch_and_store(build_dir, host + item["url"])
    else:
        _warn("spells (/api/2014): список недоступен")

    classes_2014 = _fetch_and_store(build_dir, f"{host}/api/2014/classes")
    if classes_2014:
        for item in classes_2014.get("results", []):
            _fetch_and_store(build_dir, f"{host}{item['url']}/spells")


def _run() -> None:
    """Тело фонового потока: сборка во временный каталог + атомарная подмена."""
    target = dnd_client.FILE_CACHE_DIR
    build_dir = target.parent / (target.name + ".building")
    try:
        if build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True)

        _crawl(build_dir)

        _set(phase="swap")
        if target.exists():
            shutil.rmtree(target)
        build_dir.rename(target)

        _set(running=False, phase="done",
             finished_at=datetime.now(timezone.utc).isoformat(), error=None)
        print(f"[cache_builder] готово: {_state['files_written']} файлов в {target}",
              flush=True)
    except Exception as exc:  # noqa: BLE001 — фоновая задача не должна ронять сервер
        shutil.rmtree(build_dir, ignore_errors=True)
        _set(running=False, phase="error",
             finished_at=datetime.now(timezone.utc).isoformat(),
             error=f"{type(exc).__name__}: {exc}")
        print(f"[cache_builder] ошибка сборки: {exc}", flush=True)


def start_rebuild(only_if_missing: bool = False) -> bool:
    """Запускает полную пересборку кеша в фоновом потоке.

    Возвращает True, если сборка запущена; False, если уже идёт или
    (при only_if_missing) кеш уже существует.
    """
    global _worker
    with _state_lock:
        if _state["running"]:
            return False
        if only_if_missing and dnd_client.FILE_CACHE_DIR.is_dir():
            return False
        _state.update(
            running=True, phase="starting",
            categories_done=0, categories_total=0,
            files_written=0, requests_made=0, warnings=[],
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None, error=None,
        )
    _worker = threading.Thread(target=_run, name="dnd-cache-builder", daemon=True)
    _worker.start()
    return True


async def startup_check() -> None:
    """Вызывается из lifespan: пересборка только при полном отсутствии каталога.

    Небольшая пауза, чтобы не конкурировать с инициализацией сервера.
    """
    await asyncio.sleep(1)
    if start_rebuild(only_if_missing=True):
        print("[cache_builder] dnd_file_cache не найден — запускаю полный обход API "
              "в фоне", flush=True)
