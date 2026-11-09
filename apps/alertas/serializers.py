from rest_framework import serializers

from .models import Alerta, ConfiguracionRiesgo


class AlertaSerializer(serializers.ModelSerializer):
    tipo_display = serializers.CharField(source="get_tipo_display", read_only=True)
    severidad_display = serializers.CharField(source="get_severidad_display", read_only=True)
    estado_display = serializers.CharField(source="get_estado_display", read_only=True)
    programa_nombre = serializers.CharField(source="programa.nombre_corto", read_only=True)
    indicador_codigo = serializers.CharField(source="indicador.codigo", read_only=True, default=None)
    plan_codigo = serializers.CharField(source="plan.codigo", read_only=True, default=None)
    evidencia_codigo = serializers.CharField(source="evidencia.codigo", read_only=True, default=None)
    responsable_nombre = serializers.CharField(source="responsable.get_full_name", read_only=True, default=None)
    cerrada_por_nombre = serializers.CharField(source="cerrada_por.get_full_name", read_only=True, default=None)
    planes_asociados = serializers.SerializerMethodField()

    class Meta:
        model = Alerta
        fields = [
            "id", "tipo", "tipo_display", "severidad", "severidad_display", "estado", "estado_display",
            "programa", "programa_nombre", "indicador", "indicador_codigo", "evidencia", "evidencia_codigo",
            "plan", "plan_codigo", "titulo", "detalle", "puntaje_riesgo", "responsable", "responsable_nombre",
            "fecha_generacion", "fecha_cierre", "cerrada_por_nombre", "comentario_cierre", "planes_asociados",
        ]
        read_only_fields = fields

    def get_planes_asociados(self, obj):
        return [{"id": p.id, "codigo": p.codigo} for p in obj.planes.all()]


class ConfiguracionRiesgoSerializer(serializers.ModelSerializer):
    programa_nombre = serializers.CharField(source="programa.nombre", read_only=True, default=None)
    actualizado_por_nombre = serializers.CharField(source="actualizado_por.get_full_name", read_only=True, default=None)

    class Meta:
        model = ConfiguracionRiesgo
        fields = [
            "id", "programa", "programa_nombre", "peso_tendencia", "peso_brecha", "peso_evidencia",
            "peso_vencimiento", "peso_visita", "umbral_bajo", "umbral_medio", "dias_aviso_vencimiento",
            "horizonte_visita_dias", "actualizado_por_nombre", "actualizado",
        ]
        read_only_fields = ["actualizado"]

    def validate(self, attrs):
        datos = {f: attrs.get(f, getattr(self.instance, f, None)) for f in ConfiguracionRiesgo.PESOS}
        total = sum(v or 0 for v in datos.values())
        if total != 100:
            raise serializers.ValidationError(f"Los pesos deben sumar 100 % (suman {total} %).")
        bajo = attrs.get("umbral_bajo", getattr(self.instance, "umbral_bajo", 40))
        medio = attrs.get("umbral_medio", getattr(self.instance, "umbral_medio", 65))
        if not (0 < bajo < medio < 100):
            raise serializers.ValidationError("Los umbrales deben cumplir 0 < bajo < medio < 100.")
        prog = attrs.get("programa")
        if self.instance is None and ConfiguracionRiesgo.objects.filter(programa=prog).exists():
            raise serializers.ValidationError("Ya existe una configuración para este programa.")
        return attrs
