"""Тест build_payload из rfc.py: при приёме ссылочные поля превращаются в
APIReference {index, name, url}, иначе dnd_client.compact() покажет пустые
классы/traits и объект не будет работать в базе.

build_payload — async и требует db + obj; поднимаем реальную in-memory БД
(через фикстуру db_session) и управляемый SRD.
"""
import asyncio
import types

import pytest

pytest.importorskip("sqlalchemy", reason="sqlalchemy не установлен")

from app import models  # noqa: E402
from app.routers import rfc  # noqa: E402
from app.services import dnd_client  # noqa: E402


run = asyncio.get_event_loop().run_until_complete


def test_build_payload_refs_to_apiref(db_session, monkeypatch):
    db = db_session()

    # SRD знает wizard; my-mage будет принятым community-объектом
    async def fetch_first(d, suffix):
        idx = suffix.split("/")[-1]
        if suffix == "classes/wizard":
            return {"index": "wizard", "name": "Wizard"}
        obj = d.query(models.CommunityObject).filter_by(
            category="classes", index="my-mage", status="accepted").first()
        if obj and suffix == "classes/my-mage":
            return {"index": "my-mage", "name": obj.name}
        raise Exception("not found")

    monkeypatch.setattr(dnd_client, "fetch_first", fetch_first)

    # автор
    user = models.User(username="alice", email="[email protected]", password_hash="x")
    db.add(user)
    db.commit()

    # принятый community-класс
    mage = models.CommunityObject(
        author_id=user.id, category="classes", index="my-mage",
        name="Мой маг", data={}, status="accepted")
    db.add(mage)
    db.commit()

    # объект-заклинание со ссылками на классы
    spell = models.CommunityObject(
        author_id=user.id, category="spells", index="firebolt-plus", name="Firebolt+",
        data={"level": 1, "school": "evocation", "casting_time": "1 action",
              "range": "120", "components": ["V", "S"], "duration": "мгновенная",
              "classes": ["wizard", "my-mage"], "description": "x"},
        status="pending")
    db.add(spell)
    db.commit()
    db.refresh(spell)

    payload = run(rfc.build_payload(db, spell))

    # classes стали списком APIReference
    assert isinstance(payload["classes"], list)
    assert payload["classes"][0] == {
        "index": "wizard", "name": "Wizard", "url": "/api/2024/classes/wizard"}
    assert payload["classes"][1]["name"] == "Мой маг"
    # служебные поля
    assert payload["index"] == "firebolt-plus"
    assert payload["community"] is True
    assert payload["author"] == "alice"

    # совместимость с compact: _names(classes) даёт имена
    names = [c["name"] for c in payload["classes"]]
    assert names == ["Wizard", "Мой маг"]

    db.close()


def test_build_payload_single_ref(db_session, monkeypatch):
    """ref (не список) — subspecies.species — тоже становится одним APIReference."""
    db = db_session()

    async def fetch_first(d, suffix):
        if suffix == "species/dwarf":
            return {"index": "dwarf", "name": "Dwarf"}
        raise Exception("not found")

    monkeypatch.setattr(dnd_client, "fetch_first", fetch_first)

    user = models.User(username="bob", email="[email protected]", password_hash="x")
    db.add(user)
    db.commit()

    sub = models.CommunityObject(
        author_id=user.id, category="subspecies", index="hill-dwarf", name="Hill Dwarf",
        data={"species": "dwarf", "description": "x"}, status="pending")
    db.add(sub)
    db.commit()
    db.refresh(sub)

    payload = run(rfc.build_payload(db, sub))
    assert payload["species"] == {
        "index": "dwarf", "name": "Dwarf", "url": "/api/2024/species/dwarf"}
    db.close()
