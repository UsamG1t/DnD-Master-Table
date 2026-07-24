# ---- build ----
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
# Фронтенд ходит на /api того же origin; wsUrl() выводит wss:// сам.
ENV VITE_API_BASE=/api
RUN npm run build

# ---- serve ----
FROM nginx:alpine
# Конфиг сайта подставляется из nginx/ (см. docker-compose)
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80 443
