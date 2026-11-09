from rest_framework import serializers

from .models import Factor, Periodo, Programa


class FactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Factor
        fields = ["id", "codigo", "nombre", "abreviatura", "descripcion", "orden", "activo"]


class PeriodoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Periodo
        fields = ["id", "nombre", "fecha_inicio", "fecha_fin", "activo"]

    def validate(self, attrs):
        ini = attrs.get("fecha_inicio", getattr(self.instance, "fecha_inicio", None))
        fin = attrs.get("fecha_fin", getattr(self.instance, "fecha_fin", None))
        if ini and fin and fin < ini:
            raise serializers.ValidationError({"fecha_fin": "La fecha final no puede ser anterior a la inicial."})
        return attrs


class ProgramaSerializer(serializers.ModelSerializer):
    estado_display = serializers.CharField(source="get_estado_display", read_only=True)
    director_nombre = serializers.CharField(source="director.get_full_name", read_only=True, default=None)
    dias_para_visita = serializers.IntegerField(read_only=True)

    class Meta:
        model = Programa
        fields = [
            "id", "codigo", "nombre", "nombre_corto", "facultad", "estado", "estado_display", "detalle_estado",
            "fecha_visita_pares", "dias_para_visita", "director", "director_nombre", "activo",
        ]
