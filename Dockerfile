FROM python:3.13-slim AS base

# NumPy con un solo hilo: en servidores con CPU limitada, varios hilos se estorban y el entrenamiento no avanza.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=false

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 siac

# Dependencias con versión exacta y hash verificado, solo paquetes binarios (sin ejecutar scripts de instalación).
COPY requirements.lock .
RUN pip install --require-hashes --only-binary :all: -r requirements.lock

# Solo se copia lo que la aplicación necesita para ejecutarse. El código queda como propiedad de root:
# el usuario de la aplicación puede leerlo y ejecutarlo, pero no modificarlo. Solo /app/media es escribible.
COPY manage.py ./
COPY config ./config
COPY apps ./apps
COPY docker ./docker
RUN chmod 755 docker/entrypoint.sh \
    && DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput \
    && mkdir -p /app/media && chown -R siac:siac /app/media

USER siac
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS http://localhost:${PORT:-8000}/api/salud/ || exit 1

ENTRYPOINT ["docker/entrypoint.sh"]
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --timeout 120 --access-logfile -"]
