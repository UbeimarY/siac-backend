from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class PlanMejoramiento(models.Model):
    """Acción de mejoramiento (fase 'actuar' del ciclo PHVA) asociada a un indicador o alerta."""

    class Estado(models.TextChoices):
        PROGRAMADO = "PROGRAMADO", "Programado"
        EN_EJECUCION = "EN_EJECUCION", "En ejecución"
        POR_CERRAR = "POR_CERRAR", "Por cerrar"
        CERRADO = "CERRADO", "Cerrado"
        CANCELADO = "CANCELADO", "Cancelado"

    codigo = models.CharField(max_length=20, unique=True, editable=False)
    programa = models.ForeignKey("programas.Programa", on_delete=models.CASCADE, related_name="planes")
    indicador = models.ForeignKey(
        "indicadores.Indicador", null=True, blank=True, on_delete=models.SET_NULL, related_name="planes"
    )
    alerta_origen = models.ForeignKey(
        "alertas.Alerta", null=True, blank=True, on_delete=models.SET_NULL, related_name="planes"
    )
    accion = models.CharField(max_length=300)
    descripcion = models.TextField(blank=True)
    meta_esperada = models.CharField(max_length=255, blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="planes_a_cargo"
    )
    fecha_inicio = models.DateField(default=timezone.localdate)
    fecha_limite = models.DateField()
    avance = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.PROGRAMADO)
    generado_con_ia = models.BooleanField(default=False)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["fecha_limite"]
        verbose_name = "plan de mejoramiento"
        verbose_name_plural = "planes de mejoramiento"

    def __str__(self):
        return f"{self.codigo} {self.accion}"

    @property
    def abierto(self) -> bool:
        return self.estado not in (self.Estado.CERRADO, self.Estado.CANCELADO)

    def avance_esperado(self, hoy=None) -> int:
        hoy = hoy or timezone.localdate()
        total = (self.fecha_limite - self.fecha_inicio).days
        if total <= 0:
            return 100
        transcurrido = (hoy - self.fecha_inicio).days
        return int(max(0, min(100, transcurrido / total * 100)))

    def retrasado(self, hoy=None) -> bool:
        """Retrasado si venció sin cerrarse o si el avance va 25 puntos por debajo de lo esperado."""
        if not self.abierto:
            return False
        hoy = hoy or timezone.localdate()
        if self.fecha_limite < hoy:
            return True
        return self.avance_esperado(hoy) - self.avance >= 25

    @property
    def estado_visible(self) -> str:
        return "RETRASADO" if self.retrasado() else self.estado

    def clean(self):
        if self.fecha_limite and self.fecha_inicio and self.fecha_limite < self.fecha_inicio:
            raise ValidationError({"fecha_limite": "La fecha límite no puede ser anterior al inicio."})
        if self.indicador_id and self.programa_id and self.indicador.programa_id != self.programa_id:
            raise ValidationError({"indicador": "El indicador no pertenece al programa seleccionado."})

    def save(self, *args, **kwargs):
        if self.avance >= 100 and self.estado in (self.Estado.PROGRAMADO, self.Estado.EN_EJECUCION):
            self.estado = self.Estado.POR_CERRAR
        elif 0 < self.avance < 100 and self.estado == self.Estado.PROGRAMADO:
            self.estado = self.Estado.EN_EJECUCION
        if not self.codigo:
            n = (PlanMejoramiento.objects.order_by("-id").values_list("id", flat=True).first() or 0) + 1
            self.codigo = f"PA-{n:04d}"
            while PlanMejoramiento.objects.filter(codigo=self.codigo).exists():
                n += 1
                self.codigo = f"PA-{n:04d}"
        super().save(*args, **kwargs)
