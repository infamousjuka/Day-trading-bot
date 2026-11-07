FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Berlin

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# config.yaml, .env sowie data/ und logs/ werden beim Start eingebunden (siehe docker-compose.yml)
COPY config.yaml ./config.yaml

CMD ["python", "-m", "daytrading_bot", "run"]
