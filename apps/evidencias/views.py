import mimetypes

from django.http import FileResponse, HttpResponseRedirect
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.alertas.motor import recalcular_programa_seguro
from apps.bitacora.servicio import Modulo, registrar
from apps.usuarios.permisos import NoConsulta, filtrar_por_programa, puede_editar_programa

from .models import Evidencia
from .serializers import EvidenciaSerializer, RevisionSerializer


def puede_cargar(user, indicador) -> bool:
    return puede_editar_programa(user, indicador.programa_id) or indicador.responsable_id == user.id


class EvidenciaViewSet(viewsets.ModelViewSet):
    serializer_class = EvidenciaSerializer
    permission_classes = [NoConsulta]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    search_fields = ["codigo", "nombre", "indicador__codigo", "indicador__nombre"]
    filterset_fields = ["indicador", "indicador__programa", "tipo", "periodo", "responsable"]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = Evidencia.objects.select_related(
            "indicador", "indicador__programa", "periodo", "responsable", "cargado_por", "revisado_por"
        )
        return filtrar_por_programa(qs, self.request.user, "indicador__programa_id")

    def list(self, request, *args, **kwargs):
        objs = list(self.filter_queryset(self.get_queryset()))
        estado = request.query_params.get("estado")
        if estado:
            objs = [o for o in objs if o.estado_visible == estado]
        return Response(self.get_serializer(objs, many=True).data)

    def perform_create(self, serializer):
        ind = serializer.validated_data["indicador"]
        if not puede_cargar(self.request.user, ind):
            raise PermissionDenied("Solo el responsable del indicador, el director o la coordinación pueden cargar evidencias.")
        archivo = serializer.validated_data.get("archivo")
        ev = serializer.save(
            estado=Evidencia.Estado.PENDIENTE,
            cargado_por=self.request.user,
            fecha_carga=timezone.now(),
            nombre_archivo=archivo.name[:255] if archivo else "",
            responsable=serializer.validated_data.get("responsable") or ind.responsable,
        )
        registrar(self.request, Modulo.EVIDENCIAS, f"Cargó evidencia {ev.codigo} para {ind.codigo}", objeto=ev)
        recalcular_programa_seguro(ind.programa)

    def perform_update(self, serializer):
        ev = serializer.instance
        if not puede_cargar(self.request.user, ev.indicador):
            raise PermissionDenied("No puede modificar esta evidencia.")
        if ev.estado == Evidencia.Estado.APROBADA and not self.request.user.es_global:
            raise PermissionDenied("Una evidencia aprobada solo puede modificarla la coordinación de calidad.")
        ev = serializer.save()
        registrar(self.request, Modulo.EVIDENCIAS, f"Actualizó evidencia {ev.codigo}", objeto=ev)
        recalcular_programa_seguro(ev.programa)

    def perform_destroy(self, instance):
        if not (self.request.user.es_global or (
            instance.cargado_por_id == self.request.user.id and instance.estado != Evidencia.Estado.APROBADA
        )):
            raise PermissionDenied("No puede eliminar esta evidencia.")
        prog = instance.programa
        registrar(self.request, Modulo.EVIDENCIAS, f"Eliminó evidencia {instance.codigo}", objeto=instance)
        if instance.archivo:
            instance.archivo.delete(save=False)
        instance.delete()
        recalcular_programa_seguro(prog)

    # --- Solicitud de evidencia con fecha límite --------------------------------
    @action(detail=False, methods=["post"])
    def solicitar(self, request):
        ser = self.get_serializer(data=request.data, context={**self.get_serializer_context(), "solicitud": True})
        ser.is_valid(raise_exception=True)
        ind = ser.validated_data["indicador"]
        if not puede_editar_programa(request.user, ind.programa_id):
            raise PermissionDenied("Solo el director o la coordinación pueden solicitar evidencias.")
        ev = ser.save(estado=Evidencia.Estado.POR_CARGAR, responsable=ser.validated_data.get("responsable") or ind.responsable)
        registrar(request, Modulo.EVIDENCIAS, f"Solicitó evidencia {ev.codigo} (vence {ev.fecha_limite})", objeto=ev)
        recalcular_programa_seguro(ind.programa)
        return Response(self.get_serializer(ev).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cargar(self, request, pk=None):
        """Carga el archivo o enlace de una evidencia solicitada o rechazada."""
        ev = self.get_object()
        if not puede_cargar(request.user, ev.indicador):
            raise PermissionDenied("No puede cargar esta evidencia.")
        if ev.estado == Evidencia.Estado.APROBADA:
            raise ValidationError("La evidencia ya está aprobada.")
        ser = self.get_serializer(ev, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        archivo = ser.validated_data.get("archivo")
        if not archivo and not ser.validated_data.get("enlace"):
            raise ValidationError({"archivo": "Adjunte un archivo o un enlace."})
        if archivo and ev.archivo:
            ev.archivo.delete(save=False)
        ev = ser.save(
            estado=Evidencia.Estado.PENDIENTE, cargado_por=request.user, fecha_carga=timezone.now(),
            nombre_archivo=archivo.name[:255] if archivo else ev.nombre_archivo,
            tipo=Evidencia.Tipo.ARCHIVO if archivo else Evidencia.Tipo.ENLACE,
            tipo_documento="", revision_ia=None,
        )
        registrar(request, Modulo.EVIDENCIAS, f"Cargó evidencia {ev.codigo}", objeto=ev)
        recalcular_programa_seguro(ev.programa)
        return Response(self.get_serializer(ev).data)

    # --- Validación --------------------------------------------------------------
    def _revisar(self, request, estado, verbo):
        if not request.user.es_global:
            raise PermissionDenied("Solo la coordinación de calidad valida evidencias.")
        ev = self.get_object()
        if ev.estado == Evidencia.Estado.POR_CARGAR:
            raise ValidationError("La evidencia aún no ha sido cargada.")
        ser = RevisionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        obs = ser.validated_data.get("observaciones", "")
        if estado == Evidencia.Estado.RECHAZADA and not obs.strip():
            raise ValidationError({"observaciones": "Indique el motivo del rechazo."})
        ev.estado = estado
        ev.revisado_por = request.user
        ev.fecha_revision = timezone.now()
        ev.observaciones_revision = obs
        ev.save()
        registrar(request, Modulo.EVIDENCIAS, f"{verbo} evidencia {ev.codigo}", objeto=ev, detalle={"observaciones": obs})
        recalcular_programa_seguro(ev.programa)
        return Response(self.get_serializer(ev).data)

    @action(detail=True, methods=["post"])
    def aprobar(self, request, pk=None):
        return self._revisar(request, Evidencia.Estado.APROBADA, "Aprobó")

    @action(detail=True, methods=["post"])
    def rechazar(self, request, pk=None):
        return self._revisar(request, Evidencia.Estado.RECHAZADA, "Rechazó")

    @action(detail=True, methods=["get"])
    def descargar(self, request, pk=None):
        ev = self.get_object()
        if ev.tipo == Evidencia.Tipo.ENLACE:
            return Response({"url": ev.enlace})
        if not ev.archivo:
            raise ValidationError("La evidencia no tiene archivo.")
        registrar(request, Modulo.EVIDENCIAS, f"Descargó evidencia {ev.codigo}", objeto=ev)
        try:
            ruta = ev.archivo.path
        except NotImplementedError:  # almacenamiento S3/R2: URL firmada temporal
            return HttpResponseRedirect(ev.archivo.url)
        tipo = mimetypes.guess_type(ruta)[0] or "application/octet-stream"
        return FileResponse(open(ruta, "rb"), content_type=tipo, as_attachment=True,
                            filename=ev.nombre_archivo or f"{ev.codigo}.{ev.tipo_documento.lower()}")
