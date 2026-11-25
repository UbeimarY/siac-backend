from django.conf import settings
from django.db import models


class InteraccionIA(models.Model):
    """Registro de cada uso del asistente de IA (trazabilidad y control de consumo)."""

    class Funcion(models.TextChoices):
        SUGERIR_PLAN = "SUGERIR_PLAN", "Sugerir plan de mejoramiento"
        RESUMEN = "RESUMEN", "Resumen ejecutivo"
        ASISTENTE = "ASISTENTE", "Asistente de consultas"
        REVISAR_EVIDENCIA = "REVISAR_EVIDENCIA", "Revisión de evidencia"
        ASISTENTE_BETA = "ASISTENTE_BETA", "Asistente IA Beta"
        ENTRENAR_BETA = "ENTRENAR_BETA", "Entrenamiento de IA Beta"

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    funcion = models.CharField(max_length=20, choices=Funcion.choices)
    programa = models.ForeignKey("programas.Programa", null=True, blank=True, on_delete=models.SET_NULL)
    entrada = models.TextField(blank=True)
    salida = models.TextField(blank=True)
    modelo = models.CharField(max_length=60, blank=True)
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_salida = models.PositiveIntegerField(default=0)
    exito = models.BooleanField(default=True)
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "interacción con IA"
        verbose_name_plural = "interacciones con IA"


class ModeloBeta(models.Model):
    """Modelo de la IA Beta (red neuronal propia) entrenado y guardado en la base de datos.

    Se guarda en la base de datos y no en disco porque el disco de los contenedores en la nube es efímero."""

    class Tipo(models.TextChoices):
        RIESGO = "RIESGO", "Predicción de riesgo de incumplimiento"
        INTENCION = "INTENCION", "Clasificador de intención del asistente"

    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    version = models.PositiveIntegerField()
    pesos = models.BinaryField()
    arquitectura = models.CharField(max_length=120)
    parametros = models.PositiveIntegerField(default=0)
    muestras_entrenamiento = models.PositiveIntegerField(default=0)
    muestras_validacion = models.PositiveIntegerField(default=0)
    metricas = models.JSONField(default=dict)
    historial = models.JSONField(default=dict)
    activo = models.BooleanField(default=True)
    entrenado = models.DateTimeField(auto_now_add=True)
    entrenado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")

    class Meta:
        ordering = ["tipo", "-version"]
        unique_together = [("tipo", "version")]
        verbose_name = "modelo de IA Beta"
        verbose_name_plural = "modelos de IA Beta"

    def __str__(self):
        return f"{self.get_tipo_display()} v{self.version}"
