from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ConfiguracionRiesgo(models.Model):
    """Pesos e umbrales del motor de alertas (numeral 2.5.7).

    Existe una configuración global (programa = NULL) y, opcionalmente, una por programa,
    porque cada programa requiere criterios diferenciados según su momento institucional."""

    programa = models.OneToOneField(
        "programas.Programa", null=True, blank=True, on_delete=models.CASCADE, related_name="config_riesgo"
    )
    # Pesos (deben sumar 100)
    peso_tendencia = models.PositiveSmallIntegerField(default=25)
    peso_brecha = models.PositiveSmallIntegerField(default=30)
    peso_evidencia = models.PositiveSmallIntegerField(default=20)
    peso_vencimiento = models.PositiveSmallIntegerField(default=15)
    peso_visita = models.PositiveSmallIntegerField(default=10)
    # Semáforo: 0..umbral_bajo = BAJO; ..umbral_medio = MEDIO; > umbral_medio = ALTO
    umbral_bajo = models.PositiveSmallIntegerField(default=40, validators=[MaxValueValidator(100)])
    umbral_medio = models.PositiveSmallIntegerField(default=65, validators=[MaxValueValidator(100)])
    # Parámetros de reglas
    dias_aviso_vencimiento = models.PositiveSmallIntegerField(
        default=30, help_text="Días antes de un vencimiento en que empieza a sumar riesgo"
    )
    horizonte_visita_dias = models.PositiveSmallIntegerField(
        default=365, help_text="Ventana en la que la cercanía a la visita de pares suma riesgo"
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actualizado = models.DateTimeField(auto_now=True)

    PESOS = ("peso_tendencia", "peso_brecha", "peso_evidencia", "peso_vencimiento", "peso_visita")

    class Meta:
        verbose_name = "configuración de riesgo"
        verbose_name_plural = "configuraciones de riesgo"

    def __str__(self):
        return f"Configuración {'global' if not self.programa_id else self.programa}"

    def clean(self):
        total = sum(getattr(self, p) for p in self.PESOS)
        if total != 100:
            raise ValidationError(f"Los pesos deben sumar 100 % (suman {total} %).")
        if not (0 < self.umbral_bajo < self.umbral_medio < 100):
            raise ValidationError("Los umbrales deben cumplir 0 < bajo < medio < 100.")

    @classmethod
    def global_(cls):
        obj, _ = cls.objects.get_or_create(programa=None)
        return obj

    @classmethod
    def para_programa(cls, programa):
        return cls.objects.filter(programa=programa).first() or cls.global_()

    def nivel(self, puntaje: float) -> str:
        if puntaje <= self.umbral_bajo:
            return "BAJO"
        if puntaje <= self.umbral_medio:
            return "MEDIO"
        return "ALTO"


class Alerta(models.Model):
    """Alerta temprana generada por el motor de reglas (numeral 2.4.6)."""

    class Tipo(models.TextChoices):
        BAJO_META = "BAJO_META", "Valor bajo la meta"
        EVIDENCIA_FALTANTE = "EVIDENCIA_FALTANTE", "Evidencia faltante"
        DESACTUALIZADO = "DESACTUALIZADO", "Indicador sin actualizar"
        VENCIMIENTO = "VENCIMIENTO", "Reporte o evidencia vencida"
        PLAN_RETRASADO = "PLAN_RETRASADO", "Acción de mejoramiento retrasada"
        TENDENCIA = "TENDENCIA", "Tendencia en deterioro"

    class Severidad(models.TextChoices):
        ALTA = "ALTA", "Alta"
        MEDIA = "MEDIA", "Media"
        BAJA = "BAJA", "Baja"

    class Estado(models.TextChoices):
        ACTIVA = "ACTIVA", "Activa"
        ATENDIDA = "ATENDIDA", "Atendida"
        DESCARTADA = "DESCARTADA", "Descartada"
        RESUELTA = "RESUELTA", "Resuelta automáticamente"

    clave = models.CharField(max_length=120, db_index=True, help_text="Identifica la condición para no duplicar")
    tipo = models.CharField(max_length=25, choices=Tipo.choices)
    severidad = models.CharField(max_length=10, choices=Severidad.choices)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.ACTIVA, db_index=True)
    programa = models.ForeignKey("programas.Programa", on_delete=models.CASCADE, related_name="alertas")
    indicador = models.ForeignKey(
        "indicadores.Indicador", null=True, blank=True, on_delete=models.CASCADE, related_name="alertas"
    )
    evidencia = models.ForeignKey(
        "evidencias.Evidencia", null=True, blank=True, on_delete=models.CASCADE, related_name="alertas"
    )
    plan = models.ForeignKey(
        "planes.PlanMejoramiento", null=True, blank=True, on_delete=models.CASCADE, related_name="alertas"
    )
    titulo = models.CharField(max_length=200)
    detalle = models.TextField()
    puntaje_riesgo = models.FloatField(default=0)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="alertas_asignadas"
    )
    fecha_generacion = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    comentario_cierre = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha_generacion"]
        constraints = [
            models.UniqueConstraint(
                fields=["clave"], condition=models.Q(estado="ACTIVA"), name="alerta_activa_unica"
            )
        ]

    def __str__(self):
        return f"[{self.severidad}] {self.titulo}"


class PuntajeRiesgo(models.Model):
    """Último índice de riesgo calculado por indicador (con su desglose por factor)."""

    indicador = models.OneToOneField("indicadores.Indicador", on_delete=models.CASCADE, related_name="riesgo")
    puntaje = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(100)])
    nivel = models.CharField(max_length=10)
    componentes = models.JSONField(default=dict)
    calculado = models.DateTimeField(auto_now=True)
