import os

from django.conf import settings
from rest_framework import serializers

from .models import Evidencia


class EvidenciaSerializer(serializers.ModelSerializer):
    indicador_codigo = serializers.CharField(source="indicador.codigo", read_only=True)
    indicador_nombre = serializers.CharField(source="indicador.nombre", read_only=True)
    programa_id = serializers.IntegerField(source="indicador.programa_id", read_only=True)
    programa_nombre = serializers.CharField(source="indicador.programa.nombre_corto", read_only=True)
    periodo_nombre = serializers.CharField(source="periodo.nombre", read_only=True, default=None)
    responsable_nombre = serializers.CharField(source="responsable.get_full_name", read_only=True, default=None)
    cargado_por_nombre = serializers.CharField(source="cargado_por.get_full_name", read_only=True, default=None)
    revisado_por_nombre = serializers.CharField(source="revisado_por.get_full_name", read_only=True, default=None)
    estado_visible = serializers.CharField(read_only=True)
    tiene_archivo = serializers.SerializerMethodField()

    class Meta:
        model = Evidencia
        fields = [
            "id", "codigo", "indicador", "indicador_codigo", "indicador_nombre", "programa_id", "programa_nombre",
            "periodo", "periodo_nombre", "nombre", "descripcion", "tipo", "archivo", "nombre_archivo", "enlace",
            "tipo_documento", "vigencia_desde", "vigencia_hasta", "fecha_limite", "estado", "estado_visible",
            "responsable", "responsable_nombre", "cargado_por_nombre", "fecha_carga", "revisado_por_nombre",
            "fecha_revision", "observaciones_revision", "revision_ia", "tiene_archivo", "creado",
        ]
        read_only_fields = [
            "codigo", "estado", "nombre_archivo", "tipo_documento", "fecha_carga", "fecha_revision",
            "observaciones_revision", "revision_ia", "creado",
        ]
        extra_kwargs = {"archivo": {"write_only": True, "required": False}}

    def get_tiene_archivo(self, obj):
        return bool(obj.archivo)

    def validate_archivo(self, f):
        if f is None:
            return f
        ext = os.path.splitext(f.name)[1].lstrip(".").lower()
        if ext not in settings.EVIDENCIA_EXTENSIONES:
            raise serializers.ValidationError(
                f"Tipo de archivo no permitido. Use: {', '.join(settings.EVIDENCIA_EXTENSIONES)}."
            )
        if f.size > settings.EVIDENCIA_MAX_MB * 1024 * 1024:
            raise serializers.ValidationError(f"El archivo supera {settings.EVIDENCIA_MAX_MB} MB.")
        return f

    def validate(self, attrs):
        tipo = attrs.get("tipo", getattr(self.instance, "tipo", Evidencia.Tipo.ARCHIVO))
        if self.context.get("solicitud"):
            if not attrs.get("fecha_limite"):
                raise serializers.ValidationError({"fecha_limite": "Indique la fecha límite de entrega."})
            return attrs
        if tipo == Evidencia.Tipo.ARCHIVO and not attrs.get("archivo") and not getattr(self.instance, "archivo", None):
            raise serializers.ValidationError({"archivo": "Adjunte el archivo de la evidencia."})
        if tipo == Evidencia.Tipo.ENLACE and not attrs.get("enlace") and not getattr(self.instance, "enlace", None):
            raise serializers.ValidationError({"enlace": "Indique el enlace de la evidencia."})
        d, h = attrs.get("vigencia_desde"), attrs.get("vigencia_hasta")
        if d and h and h < d:
            raise serializers.ValidationError({"vigencia_hasta": "La vigencia final no puede ser anterior a la inicial."})
        return attrs


class RevisionSerializer(serializers.Serializer):
    observaciones = serializers.CharField(required=False, allow_blank=True, max_length=2000)
