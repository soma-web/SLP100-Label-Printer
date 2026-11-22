FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      fonts-liberation fonts-dejavu-core fonts-open-sans fonts-comic-neue \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY slplabel ./slplabel
COPY LICENSE NOTICE ./

# Unprivileged user; group lp owns USB printer nodes on Debian-based hosts.
RUN useradd --uid 1000 --groups lp --no-create-home slp \
 && mkdir /data && chown slp:slp /data \
 && python -m compileall -q slplabel
USER slp

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 SLP_DATA=/data
VOLUME /data
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=4)"]
CMD ["python", "-m", "slplabel"]
