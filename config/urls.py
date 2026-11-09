from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.alertas.views import AlertaViewSet, ConfiguracionRiesgoViewSet
from apps.bitacora.views import BitacoraViewSet
from apps.evidencias.views import EvidenciaViewSet
from apps.ia import views as ia
from apps.indicadores.views import IndicadorViewSet
from apps.planes.views import PlanViewSet
from apps.programas.views import FactorViewSet, PeriodoViewSet, ProgramaViewSet
from apps.reportes import views as reportes
from apps.usuarios import views as usuarios

router = DefaultRouter()
router.register("usuarios", usuarios.UsuarioViewSet, basename="usuario")
router.register("programas", ProgramaViewSet, basename="programa")
router.register("factores", FactorViewSet, basename="factor")
router.register("periodos", PeriodoViewSet, basename="periodo")
router.register("indicadores", IndicadorViewSet, basename="indicador")
router.register("evidencias", EvidenciaViewSet, basename="evidencia")
router.register("alertas", AlertaViewSet, basename="alerta")
router.register("configuracion-riesgo", ConfiguracionRiesgoViewSet, basename="configuracion-riesgo")
router.register("planes", PlanViewSet, basename="plan")
router.register("bitacora", BitacoraViewSet, basename="bitacora")


def salud(request):
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        db = "ok"
    except Exception:
        db = "error"
    return JsonResponse({"estado": "ok" if db == "ok" else "degradado", "base_datos": db},
                        status=200 if db == "ok" else 503)


api = [
    path("auth/login/", usuarios.LoginView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/logout/", usuarios.logout, name="logout"),
    path("auth/me/", usuarios.me, name="me"),
    path("auth/cambiar-password/", usuarios.cambiar_password, name="cambiar_password"),
    path("tablero/", reportes.tablero_control, name="tablero"),
    path("reportes/", reportes.tipos, name="reportes_tipos"),
    path("reportes/<str:tipo>/", reportes.generar, name="reporte"),
    path("ia/estado/", ia.estado, name="ia_estado"),
    path("ia/sugerir-plan/", ia.sugerir_plan, name="ia_sugerir_plan"),
    path("ia/resumen/", ia.resumen, name="ia_resumen"),
    path("ia/asistente/", ia.asistente, name="ia_asistente"),
    path("ia/revisar-evidencia/<int:pk>/", ia.revisar_evidencia, name="ia_revisar_evidencia"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/salud/", salud, name="salud"),
    path("api/", include(api)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "SIAC Monitor — Administración"
admin.site.site_title = "SIAC Monitor"
