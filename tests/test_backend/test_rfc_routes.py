"""Сквозные тесты RFC через HTTP (FastAPI TestClient).

Проверяют полный жизненный цикл объектов сообщества и все ветки логики
статусов draft/pending/accepted/rejected, включая:
  * создание с валидацией полей;
  * авто-draft при ссылке на непринятый объект;
  * submit (проверить и отправить): pending-ссылки, битые ссылки, успех;
  * приём админом, в т.ч. повторную проверку ссылок и откат в draft;
  * видимость draft (скрыт от чужих и от админа);
  * подключение принятого объекта в общую базу /dnd/{category};
  * права на правку/удаление/модерацию.

Требуют fastapi+sqlalchemy (иначе пропускаются в conftest).
"""
import pytest


# ---------- Хелперы ----------

def create(client, headers, category, name, data, expect=201):
    r = client.post("/rfc/objects", headers=headers,
                    json={"category": category, "name": name, "data": data})
    assert r.status_code == expect, r.text
    return r.json()


def get_list(client, headers):
    r = client.get("/rfc/objects", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- Схема ----------

def test_schema_endpoint(client, author):
    r = client.get("/rfc/schema/spells", headers=author)
    assert r.status_code == 200
    body = r.json()
    assert body["strict"] is True
    assert any(f["key"] == "classes" and f.get("ref_category") == "classes"
               for f in body["fields"])


def test_schema_unknown_category_not_strict(client, author):
    r = client.get("/rfc/schema/monsters", headers=author)
    assert r.status_code == 200
    # monsters без строгой схемы
    assert r.json()["strict"] is False


# ---------- Создание и валидация ----------

def test_create_valid_no_refs_is_pending(client, author, srd):
    obj = create(client, author, "traits", "Stone Skin",
                 {"description": "damage resistance"})
    assert obj["status"] == "pending"
    assert obj["status_label"] == "На обработке"


def test_create_invalid_fields_422(client, author, srd):
    r = client.post("/rfc/objects", headers=author, json={
        "category": "spells", "name": "Malformed",
        "data": {"level": 99, "school": "jedi"}})
    assert r.status_code == 422, r.text
    # тело содержит список ошибок полей
    detail = r.json()["detail"]
    assert "field_errors" in detail


def test_create_with_pending_ref_becomes_draft(client, author, srd):
    # ссылаемся на ещё не принятую способность -> объект уходит в draft
    trait = create(client, author, "traits", "Darkvision", {"description": "sees in the dark"})
    species = create(client, author, "species", "Dwarf-Test",
                     {"size": "Medium", "speed": 30,
                      "traits": [trait["index"]], "description": "sturdy folk"})
    assert species["status"] == "draft"
    assert species["status_label"] == "Не готов к модерации"
    # в отчёте по ссылкам видно pending
    report = species["ref_report"]
    assert report and report[0]["state"] == "pending"


def test_create_with_missing_ref_is_draft(client, author, srd):
    species = create(client, author, "species", "Elf-Test",
                     {"size": "Medium", "speed": 30,
                      "traits": ["nesuschestvuyuschaya"], "description": "x"})
    assert species["status"] == "draft"
    assert species["ref_report"][0]["state"] == "missing"


# ---------- submit: проверить и отправить ----------

def test_submit_with_pending_ref_stays_draft(client, author, srd):
    tr = create(client, author, "traits", "Stone Heart", {"description": "x"})
    sp = create(client, author, "species", "Golem-Test",
                {"size": "Large", "speed": 25,
                 "traits": [tr["index"]], "description": "x"})
    r = client.post(f"/rfc/objects/{sp['id']}/submit", headers=author)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "не все внутренние объекты приняты" in body["message"]
    assert body["object"]["status"] == "draft"


def test_submit_success_when_refs_accepted(client, author, admin, srd):
    # 1) автор создаёт способность, 2) админ принимает, 3) вид ссылается и submit
    trait = create(client, author, "traits", "Rage", {"description": "battle fury"})
    ra = client.post(f"/rfc/objects/{trait['id']}/accept", headers=admin)
    assert ra.status_code == 200, ra.text

    sp = create(client, author, "species", "Orc-Test",
                {"size": "Medium", "speed": 30,
                 "traits": [trait["index"]], "description": "warlike folk"})
    # ссылка на принятый объект -> сразу pending уже при создании
    assert sp["status"] == "pending"

    # даже если был бы draft, submit переведёт в pending
    r = client.post(f"/rfc/objects/{sp['id']}/submit", headers=author)
    # объект уже pending, submit допускается только для draft/rejected -> 409
    assert r.status_code in (200, 409)


def test_submit_after_dependency_accepted(client, author, admin, srd):
    # вид создан со ссылкой на pending -> draft; затем зависимость принимают;
    # повторный submit проходит
    trait = create(client, author, "traits", "Scales", {"description": "armor plating"})
    sp = create(client, author, "species", "Dragonborn-Test",
                {"size": "Medium", "speed": 30,
                 "traits": [trait["index"]], "description": "dragon descendants"})
    assert sp["status"] == "draft"

    client.post(f"/rfc/objects/{trait['id']}/accept", headers=admin)
    r = client.post(f"/rfc/objects/{sp['id']}/submit", headers=author)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["object"]["status"] == "pending"


def test_submit_only_author(client, author, other, srd):
    sp = create(client, author, "traits", "Private", {"description": "x"})
    r = client.post(f"/rfc/objects/{sp['id']}/submit", headers=other)
    assert r.status_code == 403


# ---------- Приём и повторная проверка ссылок ----------

def test_accept_publishes_to_base(client, author, admin, srd):
    obj = create(client, author, "traits", "Flight", {"description": "can fly"})
    r = client.post(f"/rfc/objects/{obj['id']}/accept", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    # принятый объект появляется в списке базы /dnd/traits
    lst = client.get("/dnd/traits", headers=author)
    assert lst.status_code == 200
    assert any(it["index"] == obj["index"] for it in lst.json())


def test_accept_draft_forbidden(client, author, admin, srd):
    sp = create(client, author, "species", "Draft-Obj",
                {"size": "Medium", "speed": 30,
                 "traits": ["nope"], "description": "x"})
    assert sp["status"] == "draft"
    r = client.post(f"/rfc/objects/{sp['id']}/accept", headers=admin)
    assert r.status_code == 409  # draft ещё не готов к модерации


def test_accept_reverts_to_draft_on_broken_ref(client, author, admin, srd):
    # способность принята -> вид ссылается -> pending; способность удаляют ->
    # приём вида должен отклониться и вернуть его в draft
    trait = create(client, author, "traits", "Regeneration", {"description": "self-heals"})
    client.post(f"/rfc/objects/{trait['id']}/accept", headers=admin)
    sp = create(client, author, "species", "Troll-Test",
                {"size": "Large", "speed": 30,
                 "traits": [trait["index"]], "description": "regenerates"})
    assert sp["status"] == "pending"

    # админ удаляет принятую способность (ломая ссылку вида)
    dr = client.delete(f"/rfc/objects/{trait['id']}", headers=admin)
    assert dr.status_code == 204

    r = client.post(f"/rfc/objects/{sp['id']}/accept", headers=admin)
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert "broken_refs" in body
    # объект вернулся в draft
    lst = get_list(client, author)
    troll = next(o for o in lst["objects"] if o["id"] == sp["id"])
    assert troll["status"] == "draft"


def test_accept_only_admin(client, author, other, srd):
    obj = create(client, author, "traits", "Admin Only", {"description": "x"})
    r = client.post(f"/rfc/objects/{obj['id']}/accept", headers=other)
    assert r.status_code == 403


# ---------- Видимость ----------

def test_draft_hidden_from_admin(client, author, admin, srd):
    # draft виден автору, но НЕ админу
    create(client, author, "species", "Hidden",
           {"size": "Medium", "speed": 30, "traits": ["ghost"], "description": "x"})
    mine = get_list(client, author)
    assert any(o["name"] == "Hidden" for o in mine["objects"])
    admin_view = get_list(client, admin)
    assert not any(o["name"] == "Hidden" for o in admin_view["objects"])


def test_draft_hidden_from_others(client, author, other, srd):
    create(client, author, "species", "Private-Obj",
           {"size": "Medium", "speed": 30, "traits": ["x"], "description": "x"})
    others_view = get_list(client, other)
    assert not any(o["name"] == "Private-Obj" for o in others_view["objects"])


def test_accepted_visible_to_all(client, author, other, admin, srd):
    obj = create(client, author, "traits", "Shared", {"description": "visible to all"})
    client.post(f"/rfc/objects/{obj['id']}/accept", headers=admin)
    view = get_list(client, other)
    assert any(o["name"] == "Shared" for o in view["objects"])


# ---------- Правка, удаление, отклонение ----------

def test_edit_recomputes_status(client, author, admin, srd):
    # создаём с битой ссылкой -> draft; правим на валидную (без ссылок) -> pending
    sp = create(client, author, "species", "Werewolf",
                {"size": "Medium", "speed": 30, "traits": ["nope"], "description": "x"})
    assert sp["status"] == "draft"
    r = client.put(f"/rfc/objects/{sp['id']}", headers=author, json={
        "category": "species", "name": "Werewolf",
        "data": {"size": "Medium", "speed": 40, "description": "shapeshifts"}})
    assert r.status_code == 200 and r.json()["status"] == "pending"


def test_reject_then_edit_cycle(client, author, admin, srd):
    obj = create(client, author, "traits", "Disputed", {"description": "x"})
    rj = client.post(f"/rfc/objects/{obj['id']}/reject", headers=admin,
                     json={"comment": "clarify the description"})
    assert rj.status_code == 200 and rj.json()["status"] == "rejected"
    assert rj.json()["review_comment"] == "clarify the description"
    # автор дорабатывает
    r = client.put(f"/rfc/objects/{obj['id']}", headers=author, json={
        "category": "traits", "name": "Disputed", "data": {"description": "detailed description"}})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert r.json()["review_comment"] is None


def test_delete_by_author(client, author, srd):
    obj = create(client, author, "traits", "Self Delete", {"description": "x"})
    r = client.delete(f"/rfc/objects/{obj['id']}", headers=author)
    assert r.status_code == 204


def test_delete_by_other_forbidden(client, author, other, srd):
    obj = create(client, author, "traits", "Foreign", {"description": "x"})
    r = client.delete(f"/rfc/objects/{obj['id']}", headers=other)
    assert r.status_code == 403


def test_duplicate_name_conflict(client, author, srd):
    create(client, author, "traits", "Duplicate", {"description": "x"})
    r = client.post("/rfc/objects", headers=author, json={
        "category": "traits", "name": "Duplicate", "data": {"description": "y"}})
    assert r.status_code == 409


def test_accepted_object_not_editable(client, author, admin, srd):
    obj = create(client, author, "traits", "Final", {"description": "x"})
    client.post(f"/rfc/objects/{obj['id']}/accept", headers=admin)
    r = client.put(f"/rfc/objects/{obj['id']}", headers=author, json={
        "category": "traits", "name": "Final", "data": {"description": "z"}})
    assert r.status_code == 409


# ---------- Совместимость при приёме (задача 3) ----------

def test_accept_rejects_duplicate_of_srd(client, author, admin, srd):
    # в SRD уже есть species/dwarf -> одноимённый объект принять нельзя
    srd.add("species/dwarf")
    obj = create(client, author, "species", "Dwarf",
                 {"size": "Medium", "speed": 30, "description": "stocky"})
    # объект без ссылок -> pending
    assert obj["status"] == "pending"
    r = client.post(f"/rfc/objects/{obj['id']}/accept", headers=admin)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "issues" in detail
    assert any(i["kind"] == "duplicate" for i in detail["issues"])



def test_accept_ok_when_compatible(client, author, admin, srd):
    # существующий класс, валидная школа, уникальный index -> приём проходит
    srd.add("classes/wizard")
    obj = create(client, author, "spells", "New Bolt",
                 {"level": 1, "school": "evocation", "casting_time": "1 action",
                  "range": "60", "components": ["V", "S"], "duration": "instant",
                  "classes": ["wizard"], "description": "deals lightning damage"})
    assert obj["status"] == "pending"
    r = client.post(f"/rfc/objects/{obj['id']}/accept", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
