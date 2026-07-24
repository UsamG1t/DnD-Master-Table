#!/usr/bin/env bash
# Первичная подготовка сервера. Идемпотентен: повторный запуск ничего не ломает.
# Запускать ОДИН раз вручную на сервере перед первым деплоем:
#   OWNER=deploy bash server-init.sh
#
# Что делает:
#   1. Создаёт /etc/DnD-Master-Table (persistent: кеши + БД пользователей).
#   2. Генерирует /opt/dnd-master-table/.env с секретами (openssl rand),
#      если его ещё нет. Существующий .env НЕ трогает.
#   3. Создаёт общую сеть edge (сертификаты и домены — в стеке /opt/edge).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dnd-master-table}"
DATA_DIR="/etc/DnD-Master-Table"
# Владелец каталога приложения — пользователь деплоя (тот же, что в секрете
# VPS_USER). Задайте явно, если запускаете скрипт от root:
#   OWNER=deploy DOMAIN=... bash server-init.sh
# По умолчанию берётся из sudo-контекста, иначе — текущий пользователь.
OWNER="${OWNER:-${SUDO_USER:-$USER}}"

echo "== 1. Persistent-каталог $DATA_DIR"
sudo mkdir -p "$DATA_DIR/dnd_file_cache" "$DATA_DIR/community_dnd_file_cache"
# Владелец — UID 101 (nginx/uvicorn в наших образах пишет от своего пользователя;
# для sqlite и кешей достаточно общего доступа на запись).
sudo chmod -R 777 "$DATA_DIR"

echo "== 2. Каталог приложения $APP_DIR"
sudo mkdir -p "$APP_DIR/nginx/conf.d"
sudo chown -R "$OWNER":"$OWNER" "$APP_DIR"

ENV_FILE="$APP_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    echo "   .env уже есть — оставляю как есть"
else
    echo "   генерирую .env со свежими секретами"
    cat > "$ENV_FILE" <<EOF
# Сгенерировано server-init.sh $(date -u +%FT%TZ). НЕ коммитить.
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_REGISTRATION_TOKEN=$(openssl rand -hex 24)
ACCESS_TOKEN_EXPIRE_MINUTES=1440
DND_API_BASE=https://www.dnd5eapi.co
DND_CACHE_TTL=604800
EOF
    chmod 600 "$ENV_FILE"
fi

echo "== 3. Общая сеть с edge-прокси"
docker network create edge 2>/dev/null || echo "   сеть edge уже есть"

echo "   TLS и домены обслуживает edge-стек (/opt/edge) — здесь ничего не нужно"

echo "== Готово. Поднимите edge-стек и запушьте в master."
