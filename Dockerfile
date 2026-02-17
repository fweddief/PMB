FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=src

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Initialize SQLite schema so the container always has required tables
RUN python scripts/manage.py init

CMD ["sh", "-c", "python scripts/manage.py init && python src/services/arb_scanner_15m.py --live --budget 2"]
