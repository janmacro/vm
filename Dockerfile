# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY app ./app

# Install dependencies from pyproject.toml
RUN pip install --upgrade pip && \
    pip install .

# Prepare instance folder for SQLite (mount this as a volume in prod)
RUN mkdir -p /app/instance

# Non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Always run idempotent DB init before starting Gunicorn
CMD ["sh", "-c", "flask --app app:create_app init-db && exec gunicorn -w 2 -k gthread -b 0.0.0.0:8000 'app:create_app()'"]
