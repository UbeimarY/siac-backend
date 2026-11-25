# Recorrido del código de SIAC Monitor

Guía rápida para mostrar el código: qué archivo abrir según lo que se quiera explicar. Las rutas del backend
son de este repositorio; las del frontend, del repositorio `siac-frontend`.

## 1. Arquitectura en tres capas

| Capa | Dónde está | Qué mostrar |
| --- | --- | --- |
| Presentación | `siac-frontend/src/` | `pages/` (una pantalla por archivo) y `api/cliente.ts` (cómo habla con la API) |
| Servicios | `apps/` (9 módulos de Django) | Cada módulo tiene `models.py`, `serializers.py`, `views.py` y pruebas |
| Datos | `apps/*/models.py` | Modelos de Django que se convierten en tablas de PostgreSQL |
| Rutas de la API | `config/urls.py` | Todas las direcciones `/api/...` en un solo lugar |
| Configuración | `config/settings.py` | Seguridad (JWT, contraseñas, HTTPS) y variables de entorno |

## 2. Motor de alertas tempranas (el aporte principal)

Archivo: `apps/alertas/motor.py`

- `calcular_indicador`: el índice de riesgo de 0 a 100 como suma ponderada de cinco factores. Tiene el
  ejemplo real de IND-001 de Medicina (índice 71,8).
- `_componente_evidencia`, `_componente_vencimiento`, `_componente_visita`: cómo se calcula cada factor.
- `agregar`: índice de un programa o de una celda del mapa de calor (50 % promedio + 50 % los tres más críticos).
- `_reglas_indicador` y `_reglas_planes`: las seis reglas de alerta.
- `recalcular`: crea, actualiza y resuelve alertas sin duplicarlas (clave por condición).

Pesos y umbrales configurables: `apps/alertas/models.py`, clase `ConfiguracionRiesgo`.
Cálculo de cumplimiento y tendencia: `apps/indicadores/models.py`, métodos `cumplimiento` y `tendencia`.
Mapa de calor del tablero: `apps/reportes/datos.py`, función `tablero`.

## 3. Seguridad y permisos

- `apps/usuarios/permisos.py`: permisos por rol y `filtrar_por_programa`, que limita cada consulta a los
  programas del usuario. Se verifica en el servidor, no solo en la pantalla.
- `config/settings.py`: tokens JWT (60 minutos, lista negra al cerrar sesión) y reglas de contraseñas.
- `apps/bitacora/servicio.py`: trazabilidad; cada acción relevante queda registrada y no se puede editar.
- `siac-frontend/src/api/cliente.ts`: renovación automática del token y guardado seguro en el navegador.

## 4. Inteligencia artificial

**IA estándar (modelo de lenguaje en Groq):**
- `apps/ia/servicio.py`: llamada al proveedor configurable y registro de cada uso.
- `apps/ia/contexto.py`: los datos que recibe la IA, filtrados por los permisos del usuario.

**IA Beta (red neuronal propia, `apps/ia/beta/`):**
- `red.py`: la red neuronal desde cero en NumPy: propagación, retropropagación, Adam y parada temprana.
- `riesgo.py`: pronóstico de incumplimiento con 15 características y validación cruzada por indicador.
- `intenciones.py`: cómo la pregunta se convierte en números y la red decide entre 11 intenciones.
- `asistente.py`: cómo se arma la respuesta con datos reales de la base de datos.
- `almacen.py`: pesos guardados en la base de datos con versión.
- `vistas.py`: la API y el entrenamiento en segundo plano.

## 5. Calidad y pruebas

- Pruebas automáticas: `apps/*/tests*.py` (55 en total). Se ejecutan con `python manage.py test`.
- Pruebas de la red neuronal: `apps/ia/tests_beta.py` (gradiente numérico, XOR, permisos).
- Todas las pruebas usan datos ficticios de demostración. La prueba piloto con los datos reales de
  Odontología aún no se ha realizado.
- Análisis estático: SonarCloud, sin hallazgos abiertos en los dos repositorios.

## 6. Despliegue

- `Dockerfile` y `docker/entrypoint.sh`: imagen del backend, migraciones y entrenamiento de la IA Beta al arrancar.
- `requirements.lock`: dependencias con versión exacta y hash verificado.
- `render.yaml`: publicación en Render con la base de datos en Neon.
