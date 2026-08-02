FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8080

WORKDIR /app

RUN addgroup --system --gid 10001 cryptotrust \
    && adduser --system --uid 10001 --ingroup cryptotrust --home /nonexistent cryptotrust \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir fastapi==0.115.14 uvicorn==0.34.3

COPY --chown=cryptotrust:cryptotrust src ./src

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/', timeout=3).read(1)" || exit 1

# CRYPTOTRUST_ASGI_APP is intentionally required.  The deployment must not
# silently boot the local fake Demo composition as a production service.
CMD ["/bin/sh", "-c", ": \"${CRYPTOTRUST_ASGI_APP:?set CRYPTOTRUST_ASGI_APP to the approved production ASGI module}\"; exec python -B -m uvicorn \"${CRYPTOTRUST_ASGI_APP}\" --host 0.0.0.0 --port \"${PORT}\" --workers 1 --proxy-headers --forwarded-allow-ips='*'"]

