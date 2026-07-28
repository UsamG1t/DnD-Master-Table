#!/usr/bin/env python3
"""Offline-проверка логики rfc_schema без pytest, fastapi и сети.

Нужна там, где нельзя поставить зависимости (песочница без сети). Мокает
sqlalchemy.orm.Session, app.models и dnd_client.fetch_first и проверяет
валидацию, резолв ссылок, статусы и build_payload. Полноценные HTTP-тесты —
в test_rfc_routes.py (pytest + TestClient).

Запуск:  python3 offline_check.py
"""
import asyncio
import os
import re
import sys
import types

# Позволяем запускать из любого каталога: ищем dnd-backend/app рядом
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.getcwd(),
              os.path.join(_HERE, "..", "..", "dnd-backend"),
              os.path.join(_HERE, "..", "..")):
    if os.path.isdir(os.path.join(_cand, "app", "services")):
        os.chdir(_cand)
        break


def install_stubs():
    sa = types.ModuleType("sqlalchemy"); orm = types.ModuleType("sqlalchemy.orm")
    orm.Session = object
    sys.modules["sqlalchemy"] = sa; sys.modules["sqlalchemy.orm"] = orm

    app = types.ModuleType("app"); app.__path__ = []
    models = types.ModuleType("app.models")

    class CommunityObject:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    models.CommunityObject = CommunityObject

    class User:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    models.User = User

    sys.modules["app"] = app; sys.modules["app.models"] = models

    services = types.ModuleType("app.services"); services.__path__ = []
    sys.modules["app.services"] = services

    dc = types.ModuleType("app.services.dnd_client")
    dc.resolve_category = lambda c: {"races": "species", "subraces": "subspecies"}.get(c, c)
    dc.api_path = lambda suffix, prefix=None: f"/api/2024/{suffix.lstrip('/')}"

    async def fetch_first(db, suffix):
        raise Exception("not found")
    dc.fetch_first = fetch_first
    services.dnd_client = dc
    sys.modules["app.services.dnd_client"] = dc
    return models, dc, services


MODELS, DC, SERVICES = install_stubs()


def load_module(modname, path, replaces=()):
    src = open(path).read()
    for a, b in replaces:
        src = src.replace(a, b)
    mod = types.ModuleType(modname)
    mod.__dict__["__name__"] = modname
    sys.modules[modname] = mod
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


rfc_schema = load_module(
    "app.services.rfc_schema", "app/services/rfc_schema.py",
    [("from .. import models", "from app import models"),
     ("from . import dnd_client", "from app.services import dnd_client")])
SERVICES.rfc_schema = rfc_schema


# ---- extract build_payload/_ref_to_apiref from rfc.py ----
import datetime as _dt  # noqa: E402
_ns = {"models": MODELS, "dnd_client": DC, "rfc_schema": rfc_schema,
       "datetime": _dt.datetime, "timezone": _dt.timezone, "Session": object}
_rfc_src = open("app/routers/rfc.py").read()
for _fn in ["_ref_to_apiref", "build_payload"]:
    _m = re.search(rf"async def {_fn}\(.*?\n(?=\n\n|\nasync def |\ndef |\n@)", _rfc_src, re.S)
    exec(compile(_m.group(0), "rfc.py", "exec"), _ns)
build_payload = _ns["build_payload"]


# ---- fakes ----
class _Q:
    def __init__(self, rows): self._rows, self._f = rows, {}
    def filter_by(self, **kw): self._f = kw; return self
    def first(self):
        return next((r for r in self._rows
                     if all(getattr(r, k) == v for k, v in self._f.items())), None)


class FakeDB:
    def __init__(self, rows=None): self.rows = rows or []
    def query(self, model): return _Q(self.rows)


def set_srd(indexes):
    async def fetch_first(db, suffix):
        parts = suffix.split("/")
        if len(parts) == 2:
            obj = db.query(MODELS.CommunityObject).filter_by(
                category=parts[0], index=parts[1], status="accepted").first()
            if obj:
                return {"index": parts[1], "name": obj.name}
        if suffix in indexes:
            return {"index": suffix.split("/")[-1], "name": suffix.split("/")[-1].title()}
        raise Exception("not found")
    DC.fetch_first = fetch_first


def comm(**kw): return MODELS.CommunityObject(**kw)
run = asyncio.new_event_loop().run_until_complete

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  ok " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def main():
    S = rfc_schema

    # --- валидация ---
    try:
        S.validate_fields("spells", {"school": "evocation"}); check("required missing", False)
    except S.ValidationError as e:
        check("required missing -> error", len(e.errors) >= 5)

    try:
        S.validate_fields("spells", {"level": 12, "school": "evocation",
            "casting_time": "a", "range": "60", "components": ["V"],
            "duration": "1m", "classes": ["wizard"], "description": "x"})
        check("int range", False)
    except S.ValidationError as e:
        check("int out of range (level 12)", any("Круг" in x for x in e.errors))

    try:
        S.validate_fields("backgrounds", {"ability_scores": ["int", "wis"], "description": "x"})
        check("csv count", False)
    except S.ValidationError as e:
        check("csv count (3 abilities)", any("ровно 3" in x for x in e.errors))

    S.validate_fields("skills", {"ability": "dex", "description": "x", "extra": 1})
    check("extra fields allowed", True)

    # --- резолв ссылок ---
    set_srd({"classes/wizard", "classes/sorcerer"})
    refs = run(S.resolve_references(FakeDB(), "spells", {
        "level": 3, "school": "evocation", "casting_time": "a", "range": "60",
        "components": ["V"], "duration": "1m", "classes": ["wizard", "sorcerer"],
        "description": "x"}))
    check("all refs ok -> pending", S.status_from_refs(refs) == "pending")

    set_srd({"classes/wizard"})
    refs = run(S.resolve_references(FakeDB(), "spells", {
        "level": 3, "school": "evocation", "casting_time": "a", "range": "60",
        "components": ["V"], "duration": "1m", "classes": ["wizard", "jedi"],
        "description": "x"}))
    check("missing ref -> draft", S.status_from_refs(refs) == "draft")

    db = FakeDB([comm(category="traits", index="ss", name="SS", status="pending")])
    refs = run(S.resolve_references(db, "species", {
        "size": "Medium", "speed": 30, "traits": ["ss"], "description": "x"}))
    check("pending ref -> draft", S.status_from_refs(refs) == "draft")

    db = FakeDB([comm(category="traits", index="ss", name="SS", status="accepted")])
    set_srd({"traits/ss"})
    refs = run(S.resolve_references(db, "species", {
        "size": "Medium", "speed": 30, "traits": ["ss"], "description": "x"}))
    check("accepted community ref -> pending", S.status_from_refs(refs) == "pending")

    # --- build_payload ---
    set_srd({"classes/wizard"})
    author = types.SimpleNamespace(username="alice")
    db = FakeDB([comm(category="classes", index="my-mage", name="Мой маг", status="accepted")])
    obj = comm(category="spells", index="fb", name="FB", author=author,
               data={"level": 1, "school": "evocation", "classes": ["wizard", "my-mage"],
                     "components": ["V"], "description": "x"})
    payload = run(build_payload(db, obj))
    check("build_payload refs -> APIReference",
          isinstance(payload["classes"][0], dict) and payload["classes"][0]["name"] == "Wizard")
    check("build_payload community name resolved", payload["classes"][1]["name"] == "Мой маг")
    check("build_payload service fields", payload["community"] is True and payload["index"] == "fb")

    # --- совместимость при приёме ---
    set_srd({"species/dwarf"})
    iss = run(S.check_compatibility(FakeDB(), "species", "dwarf",
        {"size": "Medium", "speed": 30, "description": "x"}, self_id=99))
    check("compat: дубликат index с SRD", any(i.kind == "duplicate" for i in iss))

    set_srd(set())
    db = FakeDB([comm(id=5, category="traits", index="stone", name="Камень", status="accepted")])
    iss = run(S.check_compatibility(db, "traits", "stone", {"description": "x"}, self_id=99))
    check("compat: дубликат с принятым community", any(i.kind == "duplicate" for i in iss))

    set_srd({"classes/wizard"})
    iss = run(S.check_compatibility(FakeDB(), "spells", "chaos-bolt",
        {"level": 1, "school": "chaos", "classes": ["wizard"], "description": "x"}, self_id=1))
    check("compat: неизвестная школа -> unknown_value",
          any(i.kind == "unknown_value" and i.field == "school" for i in iss))

    set_srd({"classes/wizard"})
    iss = run(S.check_compatibility(FakeDB(), "spells", "new-bolt",
        {"level": 1, "school": "evocation", "classes": ["wizard"], "description": "x"}, self_id=1))
    check("compat: валидное заклинание -> нет проблем", iss == [])

    set_srd(set())
    iss = run(S.check_compatibility(FakeDB(), "subspecies", "hill",
        {"species": "nonexist", "description": "x"}, self_id=1))
    check("compat: отсутствующий родитель -> parent_type",
          any(i.kind == "parent_type" for i in iss))

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} проверок прошло")
    return passed == len(RESULTS)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
