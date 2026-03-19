FROM python:3.11-slim

WORKDIR /app

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# Runtime dirs
RUN mkdir -p /app/data /app/logs /app/reports

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]