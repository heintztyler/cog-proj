FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

# Persistent SQLite lives on a mounted volume.
RUN mkdir -p /data
ENV DATABASE_PATH=/data/pipeline.db

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
