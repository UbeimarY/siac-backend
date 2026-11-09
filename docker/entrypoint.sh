#!/bin/sh
set -e

echo "Aplicando migraciones..."
python manage.py migrate --noinput

if [ "${CARGAR_DEMO:-false}" = "true" ]; then
  if [ -z "$DEMO_PASSWORD" ]; then
    echo "CARGAR_DEMO=true requiere DEMO_PASSWORD" >&2
    exit 1
  fi
  python manage.py cargar_demo --password "$DEMO_PASSWORD" || echo "Datos de demostración ya cargados; se omite."
fi

python manage.py recalcular_alertas || true

exec "$@"
