from rest_framework import serializers

from .models import PlanMejoramiento


class PlanSerializer(serializers.ModelSerializer):
    programa_nombre = serializers.CharField(source="programa.nombre_corto", read_only=True)
    indicador_codigo = serializers.CharField(source="indicador.codigo", read_only=True, default=None)
    responsable_nombre = serializers.CharField(source="responsable.get_full_name", read_only=True, default=None)
    estado_visible = serializers.CharField(read_only=True)
    avance_esperado = serializers.SerializerMethodField()

    class Meta:
        model = PlanMejoramiento
        fields = [
            "id", "codigo", "programa", "programa_nombre", "indicador", "indicador_codigo", "alerta_origen",
            "accion", "descripcion", "meta_esperada", "responsable", "responsable_nombre", "fecha_inicio",
            "fecha_limite", "avance", "avance_esperado", "estado", "estado_visible", "generado_con_ia",
            "creado", "actualizado",
        ]
        read_only_fields = ["codigo", "creado", "actualizado"]

    def get_avance_esperado(self, obj):
        return obj.avance_esperado()

    def validate(self, attrs):
        ini = attrs.get("fecha_inicio", getattr(self.instance, "fecha_inicio", None))
        fin = attrs.get("fecha_limite", getattr(self.instance, "fecha_limite", None))
        if ini and fin and fin < ini:
            raise serializers.ValidationError({"fecha_limite": "La fecha límite no puede ser anterior al inicio."})
        prog = attrs.get("programa", getattr(self.instance, "programa", None))
        ind = attrs.get("indicador", getattr(self.instance, "indicador", None))
        if ind and prog and ind.programa_id != prog.id:
            raise serializers.ValidationError({"indicador": "El indicador no pertenece al programa."})
        alerta = attrs.get("alerta_origen")
        if alerta and prog and alerta.programa_id != prog.id:
            raise serializers.ValidationError({"alerta_origen": "La alerta no pertenece al programa."})
        return attrs
