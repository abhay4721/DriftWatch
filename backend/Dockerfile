ARG OPENTOFU_VERSION=1.13.0
FROM ghcr.io/opentofu/opentofu:${OPENTOFU_VERSION}-minimal AS tofu

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY --from=tofu /usr/local/bin/tofu /usr/local/bin/tofu
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
RUN tofu version

EXPOSE 8000
CMD ["uvicorn", "driftwatch.main:app", "--host", "0.0.0.0", "--port", "8000"]
