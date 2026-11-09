from django.conf import settings
from django.db import models
from django.utils import timezone


class Factor(models.Model):
    """Factor de calidad (Acuerdo CESU 01 de 2025 / lineamientos CNA). Parametrizable."""

    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=120)
    abreviatura = models.CharField(max_length=8)
    descripcion = models.TextField(blank=True)
    orden = models.PositiveSmallIntegerField(default=0)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["orden", "nombre"]
        verbose_name_plural = "factores"

    def __str__(self):
        return self.nombre


class Periodo(models.Model):
    """Periodo académico de seguimiento, p. ej. 2026-1."""

    nombre = models.CharField(max_length=20, unique=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    activo = models.BooleanField(default=False, help_text="Periodo vigente para el seguimiento")

    class Meta:
        ordering = ["-fecha_inicio"]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.activo:
            Periodo.objects.exclude(pk=self.pk).update(activo=False)

    @classmethod
    def vigente(cls):
        return cls.objects.filter(activo=True).first() or cls.objects.first()


class Programa(models.Model):
    """Programa académico del campus. Cada uno tiene su momento institucional."""

    class Estado(models.TextChoices):
        CONSOLIDACION = "CONSOLIDACION", "En consolidación"
        RUTA_ACREDITACION = "RUTA_ACREDITACION", "En ruta de acreditación"
        ACREDITADO = "ACREDITADO", "Acreditado en alta calidad"
        REACREDITACION = "REACREDITACION", "En reacreditación"

    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=150)
    nombre_corto = models.CharField(max_length=40)
    facultad = models.CharField(max_length=150, blank=True)
    estado = models.CharField(max_length=30, choices=Estado.choices, default=Estado.CONSOLIDACION)
    detalle_estado = models.CharField(
        max_length=200, blank=True, help_text="Ej. 'Acreditación renovada, Resolución 004640 de 2023'"
    )
    fecha_visita_pares = models.DateField(null=True, blank=True)
    director = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="programas_dirigidos",
    )
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    @property
    def dias_para_visita(self):
        if not self.fecha_visita_pares:
            return None
        return (self.fecha_visita_pares - timezone.localdate()).days
