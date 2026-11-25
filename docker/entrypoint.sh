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
# La IA Beta se entrena en segundo plano: el servidor abre el puerto de inmediato y Render
# no descarta el despliegue por demorar el arranque. Mientras termina, la página IA Beta indica
# que el modelo aún no está entrenado.
(python manage.py entrenar_ia_beta --si-falta || echo "No se pudo entrenar la IA Beta; se omite.") &

exec "$@"
