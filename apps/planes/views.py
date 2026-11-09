from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.alertas.motor import recalcular_programa_seguro
from apps.bitacora.servicio import Modulo, registrar
from apps.usuarios.permisos import NoConsulta, filtrar_por_programa, puede_editar_programa

from .models import PlanMejoramiento
from .serializers import PlanSerializer

# Campos que el responsable (sin ser director) puede actualizar en su propio plan.
CAMPOS_RESPONSABLE = {"avance", "estado", "descripcion"}


class PlanViewSet(viewsets.ModelViewSet):
    serializer_class = PlanSerializer
    permission_classes = [NoConsulta]
    filterset_fields = ["programa", "indicador", "responsable", "estado", "alerta_origen"]
    search_fields = ["codigo", "accion", "descripcion"]

    def get_queryset(self):
        qs = PlanMejoramiento.objects.select_related("programa", "indicador", "responsable")
        return filtrar_por_programa(qs, self.request.user)

    def list(self, request, *args, **kwargs):
        objs = list(self.filter_queryset(self.get_queryset()))
        if request.query_params.get("estado_visible"):
            objs = [o for o in objs if o.estado_visible == request.query_params["estado_visible"]]
        return Response(self.get_serializer(objs, many=True).data)

    def perform_create(self, serializer):
        prog = serializer.validated_data["programa"]
        if not puede_editar_programa(self.request.user, prog.id):
            raise PermissionDenied("Solo el director del programa o la coordinación pueden crear planes.")
        plan = serializer.save(creado_por=self.request.user)
        registrar(self.request, Modulo.PLANES, f"Registró plan de mejoramiento {plan.codigo}", objeto=plan)
        recalcular_programa_seguro(plan.programa)

    def perform_update(self, serializer):
        plan = serializer.instance
        user = self.request.user
        if not puede_editar_programa(user, plan.programa_id):
            if plan.responsable_id != user.id:
                raise PermissionDenied("No puede modificar este plan.")
            extra = set(serializer.validated_data) - CAMPOS_RESPONSABLE
            if extra:
                raise PermissionDenied("Como responsable solo puede actualizar el avance y el estado.")
        antes = plan.avance
        plan = serializer.save()
        msg = f"Actualizó plan {plan.codigo}"
        if plan.avance != antes:
            msg += f" (avance {antes}% → {plan.avance}%)"
        registrar(self.request, Modulo.PLANES, msg, objeto=plan)
        recalcular_programa_seguro(plan.programa)

    def perform_destroy(self, instance):
        if not puede_editar_programa(self.request.user, instance.programa_id):
            raise PermissionDenied("No puede eliminar este plan.")
        instance.estado = PlanMejoramiento.Estado.CANCELADO
        instance.save(update_fields=["estado"])
        registrar(self.request, Modulo.PLANES, f"Canceló plan {instance.codigo}", objeto=instance)
        recalcular_programa_seguro(instance.programa)
