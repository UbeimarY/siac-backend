from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.alertas.models import Alerta
from apps.alertas.resumen import resumen_programas
from apps.bitacora.servicio import Modulo, registrar
from apps.usuarios.permisos import LecturaOGlobal, filtrar_por_programa

from .models import Factor, Periodo, Programa
from .serializers import FactorSerializer, PeriodoSerializer, ProgramaSerializer


class ProgramaViewSet(viewsets.ModelViewSet):
    serializer_class = ProgramaSerializer
    permission_classes = [LecturaOGlobal]
    search_fields = ["nombre", "codigo"]
    filterset_fields = ["estado", "activo"]

    def get_queryset(self):
        qs = Programa.objects.select_related("director")
        return filtrar_por_programa(qs, self.request.user, "id")

    def list(self, request, *args, **kwargs):
        """Lista de programas con su índice de riesgo y alertas activas."""
        progs = list(self.filter_queryset(self.get_queryset()))
        resumen = {r["id"]: r for r in resumen_programas(progs)}
        data = []
        for p in progs:
            d = ProgramaSerializer(p).data
            d.update({k: v for k, v in resumen.get(p.id, {}).items() if k != "id"})
            data.append(d)
        return Response(data)

    def retrieve(self, request, *args, **kwargs):
        p = self.get_object()
        d = ProgramaSerializer(p).data
        r = resumen_programas([p], detalle=True)
        d.update({k: v for k, v in r[0].items() if k != "id"})
        d["alertas"] = list(
            Alerta.objects.filter(programa=p, estado=Alerta.Estado.ACTIVA)
            .order_by("severidad", "-fecha_generacion")
            .values("id", "tipo", "severidad", "titulo")[:20]
        )
        return Response(d)

    def perform_create(self, serializer):
        p = serializer.save()
        registrar(self.request, Modulo.PROGRAMAS, f"Creó programa {p.nombre}", objeto=p, programa=p)

    def perform_update(self, serializer):
        p = serializer.save()
        registrar(self.request, Modulo.PROGRAMAS, f"Actualizó programa {p.nombre}", objeto=p, programa=p)
        from apps.alertas.motor import recalcular_programa_seguro

        recalcular_programa_seguro(p)

    @action(detail=False, methods=["get"])
    def estados(self, request):
        return Response([{"valor": v, "nombre": n} for v, n in Programa.Estado.choices])


class FactorViewSet(viewsets.ModelViewSet):
    queryset = Factor.objects.all()
    serializer_class = FactorSerializer
    permission_classes = [LecturaOGlobal]

    def perform_create(self, serializer):
        f = serializer.save()
        registrar(self.request, Modulo.CONFIGURACION, f"Creó factor {f.nombre}", objeto=f)

    def perform_update(self, serializer):
        f = serializer.save()
        registrar(self.request, Modulo.CONFIGURACION, f"Actualizó factor {f.nombre}", objeto=f)


class PeriodoViewSet(viewsets.ModelViewSet):
    queryset = Periodo.objects.all()
    serializer_class = PeriodoSerializer
    permission_classes = [LecturaOGlobal]

    def perform_create(self, serializer):
        p = serializer.save()
        registrar(self.request, Modulo.CONFIGURACION, f"Creó periodo {p.nombre}", objeto=p)

    def perform_update(self, serializer):
        p = serializer.save()
        registrar(self.request, Modulo.CONFIGURACION, f"Actualizó periodo {p.nombre}", objeto=p)
