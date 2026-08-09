# NOT THE DEPLOYMENT PATH — kept for local/container experiments only.
#
# zoe-data runs HOST-NATIVE on the Jetson: `/usr/bin/python3 -m uvicorn main:app
# --port 8000` under `scripts/setup/systemd/zoe-data.service`. No compose file
# defines a zoe-data service (docker-compose.yml states this in its own header),
# so nothing builds this image.
#
# That makes the `pip install -r requirements.txt` below the ONLY automated
# consumer of that file anywhere in the repo — and because it never runs, the
# file is never installed. Audited 2026-08-06. Building this image would produce
# a DIFFERENT environment from the one actually running (different Python: 3.11
# here vs 3.10 live), so do not treat a green build as evidence about production.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

ENV ZOE_DATA_DB=/data/zoe.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
