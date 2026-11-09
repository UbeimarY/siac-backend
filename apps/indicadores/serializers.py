from rest_framework import serializers

from apps.usuarios.permisos import puede_editar_programa

from .models import Indicador, RegistroIndicador


class RegistroIndicadorSerializer(serializers.ModelSerializer):
    periodo_nombre = serializers.CharField(source="periodo.nombre", read_only=True)
    registrado_por_nombre = serializers.CharField(source="registrado_por.get_full_name", read_only=True, default=None)
    cumplimiento = serializers.SerializerMethodField()

    class Meta:
        model = RegistroIndicador
        fields = [
            "id", "indicador", "periodo", "periodo_nombre", "valor", "fecha_corte", "observaciones",
            "registrado_por", "registrado_por_nombre", "cumplimiento", "creado",
        ]
        read_only_fields = ["indicador", "registrado_por", "creado"]

    def get_cumplimiento(self, obj):
        return obj.indicador.cumplimiento(obj.valor)


class IndicadorSerializer(serializers.ModelSerializer):
    programa_nombre = serializers.CharField(source="programa.nombre_corto", read_only=True)
    factor_nombre = serializers.CharField(source="factor.nombre", read_only=True)
    factor_abreviatura = serializers.CharField(source="factor.abreviatura", read_only=True)
    responsable_nombre = serializers.CharField(source="responsable.get_full_name", read_only=True, default=None)
    tipo_meta_display = serializers.CharField(source="get_tipo_meta_display", read_only=True)
    unidad_display = serializers.CharField(source="get_unidad_display", read_only=True)
    periodicidad_display = serializers.CharField(source="get_periodicidad_display", read_only=True)
    ultimo_valor = serializers.SerializerMethodField()
    ultima_fecha = serializers.SerializerMethodField()
    cumplimiento = serializers.SerializerMethodField()
    tendencia = serializers.SerializerMethodField()
    estado = serializers.SerializerMethodField()
    proxima_actualizacion = serializers.SerializerMethodField()
    riesgo = serializers.SerializerMethodField()
    evidencias_vigentes = serializers.SerializerMethodField()

    class Meta:
        model = Indicador
        fields = [
            "id", "programa", "programa_nombre", "factor", "factor_nombre", "factor_abreviatura", "codigo",
            "nombre", "descripcion", "tipo_meta", "tipo_meta_display", "meta", "unidad", "unidad_display",
            "periodicidad", "periodicidad_display", "fuente_verificacion", "responsable", "responsable_nombre",
            "requiere_evidencia", "activo", "ultimo_valor", "ultima_fecha", "cumplimiento", "tendencia", "estado",
            "proxima_actualizacion", "riesgo", "evidencias_vigentes", "creado", "actualizado",
        ]
        read_only_fields = ["creado", "actualizado"]

    def get_ultimo_valor(self, obj):
        r = obj.ultimo_registro
        return r.valor if r else None

    def get_ultima_fecha(self, obj):
        r = obj.ultimo_registro
        return r.fecha_corte if r else None

    def get_cumplimiento(self, obj):
        return obj.cumplimiento()

    def get_tendencia(self, obj):
        return obj.tendencia()

    def get_estado(self, obj):
        return obj.estado()

    def get_proxima_actualizacion(self, obj):
        return obj.fecha_proxima_actualizacion()

    def get_riesgo(self, obj):
        r = getattr(obj, "riesgo", None)
        if r is None:
            return None
        return {"puntaje": r.puntaje, "nivel": r.nivel, "componentes": r.componentes}

    def get_evidencias_vigentes(self, obj):
        from django.utils import timezone

        evs = getattr(obj, "_evidencias_cache", None)
        if evs is None:
            evs = obj.evidencias.all()
        hoy = timezone.localdate()
        return sum(1 for e in evs if e.vigente_en(hoy))

    def validate(self, attrs):
        user = self.context["request"].user
        programa = attrs.get("programa", getattr(self.instance, "programa", None))
        if programa and not puede_editar_programa(user, programa.id):
            raise serializers.ValidationError("No tiene permisos para gestionar indicadores de este programa.")
        meta = attrs.get("meta", getattr(self.instance, "meta", None))
        if meta is not None and meta < 0:
            raise serializers.ValidationError({"meta": "La meta no puede ser negativa."})
        codigo = attrs.get("codigo", getattr(self.instance, "codigo", None))
        if programa and codigo:
            qs = Indicador.objects.filter(programa=programa, codigo__iexact=codigo)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"codigo": "Ya existe un indicador con este código en el programa."})
        return attrs
