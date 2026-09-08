FROM node:22.12-bookworm-slim AS web-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BALLPARK_STATE_DIR=/var/lib/ballpark/state BALLPARK_CACHE_DIR=/var/lib/ballpark/cache BALLPARK_PUBLICATION_DIR=/var/lib/ballpark/publication
WORKDIR /app
COPY requirements.lock requirements.in pyproject.toml ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src/ ./src/
COPY assets/ ./assets/
COPY schemas/ ./schemas/
COPY docker-entrypoint.sh ./
COPY --from=web-build /build/web/dist ./web/dist
RUN pip install --no-deps . && chmod 755 docker-entrypoint.sh && mkdir -p /var/lib/ballpark
VOLUME ["/var/lib/ballpark"]
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["service"]
