from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.bitacora.servicio import Modulo, registrar
from apps.programas.models import Programa
from apps.usuarios.permisos import EsGlobal, NoConsulta, filtrar_por_programa, puede_editar_programa

from . import motor
from .models import Alerta, ConfiguracionRiesgo
from .resumen import contar_alertas
from .serializers import AlertaSerializer, ConfiguracionRiesgoSerializer


class AlertaViewSet(viewsets.GenericViewSet):
    serializer_class = AlertaSerializer
    permission_classes = [NoConsulta]

    def get_queryset(self):
        return filtrar_por_programa(Alerta.objects.all(), self.request.user)

    def list(self, request, *args, **kwargs):
        params = request.query_params
        motor.recalcular_si_obsoleto()
        qs = Alerta.objects.select_related(
            "programa", "indicador", "plan", "evidencia", "responsable", "cerrada_por"
        ).prefetch_related("planes")
        qs = qs.filter(estado=params.get("estado", Alerta.Estado.ACTIVA))
        for campo in ("programa", "severidad", "tipo", "indicador", "responsable"):
            if params.get(campo):
                qs = qs.filter(**{campo: params[campo]})
        if params.get("mias") == "1":
            qs = qs.filter(responsable=request.user)
        qs = filtrar_por_programa(qs, request.user)
        orden = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
        objs = sorted(qs, key=lambda a: (orden.get(a.severidad, 3), -a.fecha_generacion.timestamp()))
        limite = params.get("limite")
        if limite and limite.isdigit():
            objs = objs[: int(limite)]
        return Response(self.get_serializer(objs, many=True).data)

    def retrieve(self, request, *args, **kwargs):
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=False, methods=["get"])
    def conteo(self, request):
        qs = filtrar_por_programa(Alerta.objects.filter(estado=Alerta.Estado.ACTIVA), request.user)
        d = contar_alertas(qs)
        d["mias"] = qs.filter(responsable=request.user).count()
        return Response(d)

    def _cerrar(self, request, pk, estado, verbo):
        a = self.get_object()
        if not (puede_editar_programa(request.user, a.programa_id) or a.responsable_id == request.user.id):
            raise PermissionDenied("No puede gestionar esta alerta.")
        if a.estado != Alerta.Estado.ACTIVA:
            raise ValidationError("La alerta ya no está activa.")
        comentario = (request.data.get("comentario") or "").strip()
        if estado == Alerta.Estado.DESCARTADA and not comentario:
            raise ValidationError({"comentario": "Indique por qué se descarta la alerta."})
        a.estado = estado
        a.fecha_cierre = timezone.now()
        a.cerrada_por = request.user
        a.comentario_cierre = comentario
        a.save()
        registrar(request, Modulo.ALERTAS, f"{verbo} alerta: {a.titulo}", objeto=a, programa=a.programa)
        return Response(self.get_serializer(a).data)

    @action(detail=True, methods=["post"])
    def atender(self, request, pk=None):
        return self._cerrar(request, pk, Alerta.Estado.ATENDIDA, "Atendió")

    @action(detail=True, methods=["post"])
    def descartar(self, request, pk=None):
        return self._cerrar(request, pk, Alerta.Estado.DESCARTADA, "Descartó")

    @action(detail=False, methods=["post"], permission_classes=[EsGlobal])
    def recalcular(self, request):
        resumen = motor.recalcular()
        registrar(request, Modulo.ALERTAS, "Ejecutó el motor de alertas", detalle=resumen)
        return Response(resumen)


class ConfiguracionRiesgoViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                                 mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Pesos y umbrales del motor. La coordinación de calidad los ajusta sin modificar código."""

    serializer_class = ConfiguracionRiesgoSerializer
    permission_classes = [EsGlobal]

    def get_queryset(self):
        ConfiguracionRiesgo.global_()
        return ConfiguracionRiesgo.objects.select_related("programa", "actualizado_por").order_by("programa__nombre")

    def get_permissions(self):
        if self.action in ("list", "retrieve", "vista_previa"):
            from rest_framework.permissions import IsAuthenticated

            return [IsAuthenticated()]
        return super().get_permissions()

    def _recalcular(self, cfg):
        progs = [cfg.programa] if cfg.programa_id else list(Programa.objects.filter(activo=True))
        motor.recalcular(progs)

    def perform_create(self, serializer):
        cfg = serializer.save(actualizado_por=self.request.user)
        registrar(self.request, Modulo.CONFIGURACION, f"Creó configuración de riesgo para {cfg.programa}",
                  objeto=cfg, programa=cfg.programa, detalle=serializer.data)
        self._recalcular(cfg)

    def perform_update(self, serializer):
        cfg = serializer.save(actualizado_por=self.request.user)
        ambito = cfg.programa.nombre if cfg.programa_id else "global"
        registrar(self.request, Modulo.CONFIGURACION, f"Actualizó pesos de alertas ({ambito})",
                  objeto=cfg, programa=cfg.programa, detalle=serializer.data)
        self._recalcular(cfg)

    def perform_destroy(self, instance):
        if instance.programa_id is None:
            raise ValidationError("La configuración global no se puede eliminar.")
        prog = instance.programa
        registrar(self.request, Modulo.CONFIGURACION, f"Eliminó configuración propia de {prog}", programa=prog)
        instance.delete()
        motor.recalcular([prog])

    @action(detail=False, methods=["post"], url_path="vista-previa")
    def vista_previa(self, request):
        """Simula el índice de cada programa con pesos dados, sin guardar."""
        ser = ConfiguracionRiesgoSerializer(data={**request.data, "programa": None})
        ser.is_valid(raise_exception=True)
        cfg = ConfiguracionRiesgo(**{k: v for k, v in ser.validated_data.items() if k != "programa"})
        progs = filtrar_por_programa(Programa.objects.filter(activo=True), request.user, "id")
        out = []
        for p in progs:
            res = [motor.calcular_indicador(i, cfg) for i in motor.indicadores_con_datos([p])]
            r = motor.riesgo_programa(res)
            out.append({"programa": p.nombre_corto, "riesgo": r, "nivel": cfg.nivel(r)})
        return Response(out)
