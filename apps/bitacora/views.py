import csv

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.usuarios.permisos import EsGlobal

from .models import RegistroBitacora


class RegistroBitacoraSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.SerializerMethodField()
    usuario_iniciales = serializers.SerializerMethodField()
    modulo_display = serializers.CharField(source="get_modulo_display", read_only=True)
    programa_nombre = serializers.CharField(source="programa.nombre_corto", read_only=True, default=None)

    class Meta:
        model = RegistroBitacora
        fields = ["id", "fecha", "usuario", "usuario_nombre", "usuario_iniciales", "modulo", "modulo_display",
                  "accion", "objeto_tipo", "objeto_id", "programa", "programa_nombre", "detalle", "ip"]

    def get_usuario_nombre(self, obj):
        return obj.usuario.get_full_name() if obj.usuario else "Sistema"

    def get_usuario_iniciales(self, obj):
        return obj.usuario.iniciales if obj.usuario else "SI"


class BitacoraViewSet(viewsets.ReadOnlyModelViewSet):
    """Registro de solo lectura (no hay endpoints de edición ni borrado)."""

    serializer_class = RegistroBitacoraSerializer
    permission_classes = [EsGlobal]
    filterset_fields = {"modulo": ["exact"], "usuario": ["exact"], "programa": ["exact"],
                        "fecha": ["date__gte", "date__lte"]}
    search_fields = ["accion"]

    def get_queryset(self):
        return RegistroBitacora.objects.select_related("usuario", "programa").order_by("-fecha")

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        total = qs.count()
        try:
            limite = min(int(request.query_params.get("limite", 200)), 1000)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            limite, offset = 200, 0
        datos = self.get_serializer(qs[offset: offset + limite], many=True).data
        return Response({"total": total, "resultados": datos})

    @action(detail=False, methods=["get"])
    def exportar(self, request):
        qs = self.filter_queryset(self.get_queryset())[:20000]
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="bitacora_siac.csv"'
        resp.write("﻿")
        w = csv.writer(resp)
        w.writerow(["Fecha", "Usuario", "Módulo", "Acción", "Programa", "IP"])
        for r in qs:
            w.writerow([
                timezone.localtime(r.fecha).strftime("%Y-%m-%d %H:%M:%S"),
                r.usuario.get_full_name() if r.usuario else "Sistema",
                r.get_modulo_display(), r.accion, r.programa.nombre_corto if r.programa else "", r.ip or "",
            ])
        return resp
