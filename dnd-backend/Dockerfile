FROM python:3.12-slim

WORKDIR /app

# Системные зависимости не нужны: stdlib + httpx/uvicorn ставятся из pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Кеши и БД пользователей монтируются из /etc/DnD-Master-Table (см. compose).
# Порт uvicorn внутри контейнера — 8000.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
