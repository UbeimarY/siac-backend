FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=false

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 siac

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=siac:siac . .
RUN chmod +x docker/entrypoint.sh \
    && DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput \
    && mkdir -p /app/media && chown -R siac:siac /app/media /app/staticfiles

USER siac
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS http://localhost:8000/api/salud/ || exit 1

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-"]
