from django.conf import settings
from django.db import models


class RegistroBitacora(models.Model):
    """Trazabilidad (numerales 2.4.7 y 2.5.5): quién hizo qué, cuándo y sobre qué objeto."""

    class Modulo(models.TextChoices):
        AUTENTICACION = "AUTENTICACION", "Autenticación"
        USUARIOS = "USUARIOS", "Usuarios"
        PROGRAMAS = "PROGRAMAS", "Programas"
        INDICADORES = "INDICADORES", "Indicadores"
        EVIDENCIAS = "EVIDENCIAS", "Evidencias"
        ALERTAS = "ALERTAS", "Alertas"
        PLANES = "PLANES", "Planes"
        REPORTES = "REPORTES", "Reportes"
        CONFIGURACION = "CONFIGURACION", "Configuración"
        IA = "IA", "Asistente IA"

    fecha = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="acciones"
    )
    modulo = models.CharField(max_length=20, choices=Modulo.choices, db_index=True)
    accion = models.CharField(max_length=255)
    objeto_tipo = models.CharField(max_length=50, blank=True)
    objeto_id = models.CharField(max_length=50, blank=True, db_index=True)
    programa = models.ForeignKey(
        "programas.Programa", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    detalle = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "registro de bitácora"
        verbose_name_plural = "bitácora"

    def __str__(self):
        return f"{self.fecha:%Y-%m-%d %H:%M} {self.usuario} – {self.accion}"
