FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Initialize SQLite schema so the container always has required tables
RUN python scripts/manage.py init

CMD ["sh", "-c", "python scripts/manage.py init && PYTHONPATH=src python src/services/scanner_runtime.py"]
