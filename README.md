# SIAC Monitor — Backend (API)

API REST de la herramienta tecnológica para el seguimiento del **Sistema Interno de Aseguramiento de la Calidad (SIAC)** de los programas académicos de la Universidad Cooperativa de Colombia, campus Pasto.

Trabajo de grado — Ingeniería de Software: Ubeimar Lizardo Yepes Portilla, Juan Pablo Lucero Morales y Sebastián José Rojas Betancourth.

## Tecnologías

| Componente | Tecnología |
|---|---|
| Lenguaje y framework | Python 3.13, Django 5.2, Django REST Framework |
| Autenticación | JWT (SimpleJWT) con rotación y lista negra (cierre de sesión) |
| Base de datos | PostgreSQL 17 (SQLite solo para desarrollo local) |
| Reportes | ReportLab (PDF) y openpyxl (Excel) |
| IA | Proveedor configurable: Groq (gratuito, por defecto), Gemini, OpenRouter, OpenAI o Anthropic; solo desde el backend |
| Despliegue | Docker, Gunicorn, Nginx + HTTPS |

## Módulos

| App | Responsabilidad | Numeral del documento |
|---|---|---|
| `usuarios` | Cinco perfiles: administrador institucional, coordinador de calidad, director de programa, responsable de indicador y usuario de consulta | 2.5.5 |
| `programas` | Programas, factores de calidad y periodos | 2.3.3, 2.4.4 |
| `indicadores` | Indicadores con meta, periodicidad, fuente de verificación, registros históricos e importación desde Excel | 2.2, 3.5.2 |
| `evidencias` | Archivos o enlaces con vigencia, solicitud con fecha límite y validación | 2.4.5 |
| `alertas` | Motor de alertas tempranas ponderado y configurable por programa | 2.4.6, 2.5.7 |
| `planes` | Planes de mejoramiento con detección de retraso | PHVA |
| `bitacora` | Trazabilidad de solo lectura y exportación CSV | 2.4.7 |
| `reportes` | Tablero de control y cuatro reportes PDF/Excel | 2.4.8 |
| `ia` | Sugerencia de planes, resumen ejecutivo, asistente de consultas y pre-revisión de evidencias | (nuevo) |

### Motor de alertas

Índice de riesgo por indicador (0–100) = Σ peso × factor, con cinco componentes: **tendencia** (mejora, estable o deterioro), **brecha frente a la meta**, **evidencia faltante**, **proximidad al vencimiento** y **cercanía a la visita de pares**. Los pesos y los umbrales del semáforo se configuran globalmente o **por programa**, sin tocar el código.

Las alertas se generan por reglas (no son predicciones estadísticas): valor bajo la meta, evidencia faltante, indicador sin actualizar, solicitud o reporte vencido, acción de mejoramiento retrasada y tendencia en deterioro. Las alertas se resuelven solas cuando desaparece la condición.

### IA

La IA **apoya, no decide**: sugiere acciones que el usuario edita antes de guardar, redacta resúmenes que el usuario revisa y pre-revisa evidencias sin aprobarlas. Recibe solo los datos que el usuario tiene permiso de ver, y cada uso queda en la bitácora. Sin `IA_API_KEY`, las funciones de IA responden 503 y el resto del sistema funciona normal.

Configuración gratuita recomendada: crear una clave en https://console.groq.com y definir `IA_PROVEEDOR=groq` e `IA_API_KEY=...` (modelo por defecto `openai/gpt-oss-120b`). Cualquier API compatible con OpenAI sirve con `IA_PROVEEDOR=compatible`, `IA_BASE_URL` e `IA_MODELO`.

## Desarrollo local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py cargar_demo --password "UnaClaveDemo2026*"
python manage.py runserver
python manage.py test apps
```

## Despliegue con Docker

```bash
cp .env.example .env    # completar valores
docker compose up -d --build
docker compose exec api python manage.py createsuperuser
```

Variables principales: ver `.env.example`. Para cargar datos de demostración al primer arranque: `CARGAR_DEMO=true` y `DEMO_PASSWORD=...`.

Tarea programada recomendada (cron diario): `python manage.py recalcular_alertas`.

## Endpoints principales

`/api/auth/login/`, `/api/auth/refresh/`, `/api/auth/logout/`, `/api/auth/me/`, `/api/tablero/`, `/api/programas/`, `/api/indicadores/`, `/api/indicadores/{id}/registros/`, `/api/indicadores/{id}/historial/`, `/api/evidencias/`, `/api/alertas/`, `/api/configuracion-riesgo/`, `/api/planes/`, `/api/bitacora/`, `/api/reportes/{tipo}/?formato=pdf|xlsx`, `/api/ia/*`, `/api/salud/`.
