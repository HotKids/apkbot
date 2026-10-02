FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN useradd --system --user-group --uid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin apkdl \
    && mkdir -p /data
VOLUME ["/data"]

ENTRYPOINT ["sh", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "/app/main.py"]
