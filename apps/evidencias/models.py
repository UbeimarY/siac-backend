import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def ruta_evidencia(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    prog = instance.indicador.programa.codigo if instance.indicador_id else "general"
    return f"evidencias/{prog}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


class Evidencia(models.Model):
    """Evidencia documental (numeral 2.4.5): archivo o enlace, responsable de carga, fecha,
    período de vigencia, tipo de documento y estado de validación.

    Una evidencia puede crearse primero como *solicitud* (estado POR_CARGAR con fecha límite);
    si vence sin cargarse, el motor genera una alerta de vencimiento."""

    class Estado(models.TextChoices):
        POR_CARGAR = "POR_CARGAR", "Por cargar"
        PENDIENTE = "PENDIENTE", "Pendiente de revisión"
        APROBADA = "APROBADA", "Aprobada"
        RECHAZADA = "RECHAZADA", "Rechazada"

    class Tipo(models.TextChoices):
        ARCHIVO = "ARCHIVO", "Archivo"
        ENLACE = "ENLACE", "Enlace"

    indicador = models.ForeignKey("indicadores.Indicador", on_delete=models.CASCADE, related_name="evidencias")
    periodo = models.ForeignKey("programas.Periodo", null=True, blank=True, on_delete=models.SET_NULL)
    codigo = models.CharField(max_length=20, unique=True, editable=False)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.ARCHIVO)
    archivo = models.FileField(upload_to=ruta_evidencia, null=True, blank=True, max_length=255)
    nombre_archivo = models.CharField(max_length=255, blank=True)
    enlace = models.URLField(blank=True, max_length=500)
    tipo_documento = models.CharField(max_length=10, blank=True)
    vigencia_desde = models.DateField(null=True, blank=True)
    vigencia_hasta = models.DateField(null=True, blank=True)
    fecha_limite = models.DateField(null=True, blank=True, help_text="Fecha límite de carga (solicitudes)")
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PENDIENTE)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="evidencias_asignadas"
    )
    cargado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="evidencias_cargadas"
    )
    fecha_carga = models.DateTimeField(null=True, blank=True)
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="evidencias_revisadas"
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)
    observaciones_revision = models.TextField(blank=True)
    revision_ia = models.JSONField(null=True, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado"]
        verbose_name_plural = "evidencias"

    def __str__(self):
        return f"{self.codigo} {self.nombre}"

    @property
    def programa(self):
        return self.indicador.programa

    @property
    def vencida(self) -> bool:
        hoy = timezone.localdate()
        if self.estado == self.Estado.POR_CARGAR:
            return bool(self.fecha_limite and self.fecha_limite < hoy)
        return bool(self.vigencia_hasta and self.vigencia_hasta < hoy)

    @property
    def estado_visible(self) -> str:
        return "VENCIDA" if self.vencida else self.estado

    def vigente_en(self, fecha) -> bool:
        if self.estado not in (self.Estado.APROBADA, self.Estado.PENDIENTE):
            return False
        if self.vigencia_desde and self.vigencia_desde > fecha:
            return False
        if self.vigencia_hasta and self.vigencia_hasta < fecha:
            return False
        return True

    def clean(self):
        if self.estado != self.Estado.POR_CARGAR:
            if self.tipo == self.Tipo.ARCHIVO and not self.archivo:
                raise ValidationError({"archivo": "Adjunte el archivo de la evidencia."})
            if self.tipo == self.Tipo.ENLACE and not self.enlace:
                raise ValidationError({"enlace": "Indique el enlace de la evidencia."})
        if self.vigencia_desde and self.vigencia_hasta and self.vigencia_hasta < self.vigencia_desde:
            raise ValidationError({"vigencia_hasta": "La vigencia final no puede ser anterior a la inicial."})

    def save(self, *args, **kwargs):
        if not self.codigo:
            ultimo = Evidencia.objects.order_by("-id").values_list("id", flat=True).first() or 0
            self.codigo = f"EV-{ultimo + 1:04d}"
            while Evidencia.objects.filter(codigo=self.codigo).exists():
                ultimo += 1
                self.codigo = f"EV-{ultimo + 1:04d}"
        if self.archivo and not self.tipo_documento:
            self.tipo_documento = os.path.splitext(self.archivo.name)[1].lstrip(".").upper()[:10]
        if self.tipo == self.Tipo.ENLACE and not self.tipo_documento:
            self.tipo_documento = "URL"
        super().save(*args, **kwargs)
