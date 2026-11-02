FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DATA_DIR=/data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# MathLive (the math editor). Downloaded at build time so the repo stays small.
ARG MATHLIVE_VERSION=0.101.0
RUN mkdir -p /app/public/vendor/mathlive /tmp/ml \
 && curl -fsSL https://registry.npmjs.org/mathlive/-/mathlive-${MATHLIVE_VERSION}.tgz | tar xz -C /tmp/ml \
 && cp -r /tmp/ml/package/dist/mathlive.min.mjs /tmp/ml/package/dist/mathlive-static.css \
          /tmp/ml/package/dist/fonts /app/public/vendor/mathlive/ \
 && rm -rf /tmp/ml

COPY server ./server
COPY public ./public

VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD curl -fs http://localhost:8080/manifest.webmanifest || exit 1
WORKDIR /app/server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
