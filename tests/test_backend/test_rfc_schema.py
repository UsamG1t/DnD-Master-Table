"""Юнит-тесты реестра схем rfc_schema: валидация полей, резолв ссылок,
определение статуса draft/pending. Без сети и без HTTP — только логика.

Резолв ссылок использует БД (community-объекты) и dnd_client.fetch_first;
и то, и другое подменяется лёгкими фейками прямо здесь, поэтому файл
самодостаточен и не требует поднятия приложения.
"""
import asyncio

import pytest

pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")

from app.services import rfc_schema  # noqa: E402
from app.services import dnd_client  # noqa: E402
from app import models  # noqa: E402


run = asyncio.new_event_loop().run_until_complete


# ---------- Фейки для резолва ссылок ----------

class _Q:
    def __init__(self, rows):
        self._rows, self._f = rows, {}

    def filter_by(self, **kw):
        self._f = kw
        return self

    def first(self):
        return next((r for r in self._rows
                     if all(getattr(r, k) == v for k, v in self._f.items())), None)


class FakeDB:
    def __init__(self, community=None):
        self.rows = community or []

    def query(self, model):
        return _Q(self.rows)


def comm(category, index, name, status):
    o = models.CommunityObject(category=category, index=index, name=name, status=status)
    return o


@pytest.fixture
def srd(monkeypatch):
    existing = set()

    async def fetch_first(db, suffix):
        # принятые community тоже «есть»
        parts = suffix.split("/")
        if len(parts) == 2:
            obj = db.query(models.CommunityObject).filter_by(
                category=parts[0], index=parts[1], status="accepted").first()
            if obj:
                return {"index": parts[1], "name": obj.name}
        if suffix in existing:
            return {"index": suffix.split("/")[-1], "name": suffix.split("/")[-1].title()}
        raise Exception("not found")

    monkeypatch.setattr(dnd_client, "fetch_first", fetch_first)
    return existing


# ---------- Валидация полей ----------

def test_required_fields_missing():
    with pytest.raises(rfc_schema.ValidationError) as ei:
        rfc_schema.validate_fields("spells", {"school": "evocation"})
    assert len(ei.value.errors) >= 5


def test_int_out_of_range():
    with pytest.raises(rfc_schema.ValidationError) as ei:
        rfc_schema.validate_fields("spells", {
            "level": 12, "school": "evocation", "casting_time": "1 action",
            "range": "60", "components": ["V"], "duration": "1 min",
            "classes": ["wizard"], "description": "x"})
    assert any("Круг" in e for e in ei.value.errors)


def test_enum_invalid():
    with pytest.raises(rfc_schema.ValidationError) as ei:
        rfc_schema.validate_fields("spells", {
            "level": 3, "school": "jedi", "casting_time": "1 action",
            "range": "60", "components": ["V"], "duration": "1 min",
            "classes": ["wizard"], "description": "x"})
    assert any("Школа" in e for e in ei.value.errors)


def test_csv_fixed_count():
    with pytest.raises(rfc_schema.ValidationError) as ei:
        rfc_schema.validate_fields("backgrounds", {
            "ability_scores": ["int", "wis"], "description": "x"})
    assert any("ровно 3" in e for e in ei.value.errors)


def test_ability_name_check():
    with pytest.raises(rfc_schema.ValidationError) as ei:
        rfc_schema.validate_fields("classes", {
            "hit_die": "10", "primary_ability": "str",
            "saving_throws": ["str", "banana"], "description": "x"})
    assert any("banana" in e for e in ei.value.errors)


def test_valid_class_passes():
    rfc_schema.validate_fields("classes", {
        "hit_die": "8", "primary_ability": "wis",
        "saving_throws": ["wis", "cha"], "skill_count": 2,
        "spellcasting_ability": "wis", "description": "A spellcaster"})


def test_component_set_invalid():
    with pytest.raises(rfc_schema.ValidationError) as ei:
        rfc_schema.validate_fields("spells", {
            "level": 1, "school": "evocation", "casting_time": "1 action",
            "range": "60", "components": ["V", "X"], "duration": "1 min",
            "classes": ["wizard"], "description": "x"})
    assert any("Компоненты" in e for e in ei.value.errors)


def test_extra_fields_allowed():
    # свободные доп-поля не мешают
    rfc_schema.validate_fields("skills", {"ability": "dex", "description": "x", "foo": 1})


# ---------- Резолв ссылок и статус ----------

def test_refs_all_ok(srd):
    srd.update({"classes/wizard", "classes/sorcerer"})
    data = {"level": 3, "school": "evocation", "casting_time": "1 action",
            "range": "60", "components": ["V", "S"], "duration": "1 min",
            "classes": ["wizard", "sorcerer"], "description": "x"}
    refs = run(rfc_schema.resolve_references(FakeDB(), "spells", data))
    assert all(r.state == "ok" for r in refs)
    assert rfc_schema.status_from_refs(refs) == "pending"


def test_ref_missing(srd):
    srd.update({"classes/wizard"})
    data = {"level": 3, "school": "evocation", "casting_time": "1 action",
            "range": "60", "components": ["V"], "duration": "1 min",
            "classes": ["wizard", "jedi"], "description": "x"}
    refs = run(rfc_schema.resolve_references(FakeDB(), "spells", data))
    jedi = next(r for r in refs if r.index == "jedi")
    assert jedi.state == "missing"
    assert rfc_schema.status_from_refs(refs) == "draft"


def test_ref_pending(srd):
    db = FakeDB([comm("traits", "stone-skin", "Stone Skin", "pending")])
    data = {"size": "Medium", "speed": 30, "traits": ["stone-skin"], "description": "x"}
    refs = run(rfc_schema.resolve_references(db, "species", data))
    assert refs[0].state == "pending"
    assert rfc_schema.status_from_refs(refs) == "draft"


def test_ref_accepted_community(srd):
    db = FakeDB([comm("traits", "stone-skin", "Stone Skin", "accepted")])
    data = {"size": "Medium", "speed": 30, "traits": ["stone-skin"], "description": "x"}
    refs = run(rfc_schema.resolve_references(db, "species", data))
    assert refs[0].state == "ok"
    assert rfc_schema.status_from_refs(refs) == "pending"


def test_ref_draft_dependency(srd):
    db = FakeDB([comm("traits", "wip", "Draft Trait", "draft")])
    data = {"size": "Small", "speed": 25, "traits": ["wip"], "description": "x"}
    refs = run(rfc_schema.resolve_references(db, "species", data))
    assert refs[0].state == "pending"
    assert rfc_schema.status_from_refs(refs) == "draft"


def test_no_refs_is_pending(srd):
    refs = run(rfc_schema.resolve_references(FakeDB(), "traits", {"description": "x"}))
    assert rfc_schema.status_from_refs(refs) == "pending"


def test_schema_public_shape():
    pub = rfc_schema.get_schema("spells").public()
    classes = next(f for f in pub["fields"] if f["key"] == "classes")
    assert classes["ref_category"] == "classes"
    assert any(f["key"] == "level" for f in pub["fields"])


# ---------- Совместимость при приёме ----------

def test_compat_duplicate_srd(srd):
    srd.add("species/dwarf")
    iss = run(rfc_schema.check_compatibility(
        FakeDB(), "species", "dwarf",
        {"size": "Medium", "speed": 30, "description": "x"}, self_id=99))
    assert any(i.kind == "duplicate" for i in iss)


def test_compat_duplicate_community(srd):
    db = FakeDB([comm("traits", "stone", "Stone", "accepted")])
    db.rows[0].id = 5
    iss = run(rfc_schema.check_compatibility(db, "traits", "stone", {"description": "x"}, self_id=99))
    assert any(i.kind == "duplicate" for i in iss)


def test_compat_self_not_duplicate(srd):
    db = FakeDB([comm("traits", "stone", "Stone", "accepted")])
    db.rows[0].id = 7
    iss = run(rfc_schema.check_compatibility(db, "traits", "stone", {"description": "x"}, self_id=7))
    assert not any(i.kind == "duplicate" for i in iss)


def test_compat_unknown_school(srd):
    srd.add("classes/wizard")
    iss = run(rfc_schema.check_compatibility(
        FakeDB(), "spells", "chaos-bolt",
        {"level": 1, "school": "chaos", "classes": ["wizard"], "description": "x"}, self_id=1))
    assert any(i.kind == "unknown_value" and i.field == "school" for i in iss)


def test_compat_valid_spell(srd):
    srd.add("classes/wizard")
    iss = run(rfc_schema.check_compatibility(
        FakeDB(), "spells", "new-bolt",
        {"level": 1, "school": "evocation", "classes": ["wizard"], "description": "x"}, self_id=1))
    assert iss == []


def test_compat_missing_parent(srd):
    iss = run(rfc_schema.check_compatibility(
        FakeDB(), "subspecies", "hill", {"species": "nonexist", "description": "x"}, self_id=1))
    assert any(i.kind == "parent_type" for i in iss)
