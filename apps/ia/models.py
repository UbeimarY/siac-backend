from django.conf import settings
from django.db import models


class InteraccionIA(models.Model):
    """Registro de cada uso del asistente de IA (trazabilidad y control de consumo)."""

    class Funcion(models.TextChoices):
        SUGERIR_PLAN = "SUGERIR_PLAN", "Sugerir plan de mejoramiento"
        RESUMEN = "RESUMEN", "Resumen ejecutivo"
        ASISTENTE = "ASISTENTE", "Asistente de consultas"
        REVISAR_EVIDENCIA = "REVISAR_EVIDENCIA", "Revisión de evidencia"

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
