# The whole app in one container: the web app is built, then served by the API at the same
# origin. Needs both frontend/ and backend/ (the main branch once both are merged).

FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist ./static
ENV LIFELINE_STATIC_DIR=/app/static \
    LIFELINE_DB=/data/lifeline.db \
    PORT=8000
RUN mkdir -p /data
EXPOSE 8000
# --proxy-headers so the join-code limits see real client addresses behind the host's proxy.
# Only proxies listed in FORWARDED_ALLOW_IPS (uvicorn reads it; default 127.0.0.1) are believed,
# and the client address is the last one they added: a client can't pick its own.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
