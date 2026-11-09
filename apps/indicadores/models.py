from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Indicador(models.Model):
    """Indicador de calidad (numerales 2.2 y 3.5.2): código, nombre, descripción, meta,
    periodicidad, responsable, factor, fuente de verificación y estado de cumplimiento."""

    class TipoMeta(models.TextChoices):
        MAYOR_IGUAL = "MAYOR_IGUAL", "≥ (mayor o igual)"
        MENOR_IGUAL = "MENOR_IGUAL", "≤ (menor o igual)"

    class Unidad(models.TextChoices):
        PORCENTAJE = "PORCENTAJE", "%"
        NUMERO = "NUMERO", "Número"
        ESCALA = "ESCALA", "Escala (1–5)"

    class Periodicidad(models.TextChoices):
        MENSUAL = "MENSUAL", "Mensual"
        TRIMESTRAL = "TRIMESTRAL", "Trimestral"
        SEMESTRAL = "SEMESTRAL", "Semestral"
        ANUAL = "ANUAL", "Anual"

    DIAS_PERIODICIDAD = {"MENSUAL": 31, "TRIMESTRAL": 92, "SEMESTRAL": 183, "ANUAL": 366}

    programa = models.ForeignKey("programas.Programa", on_delete=models.CASCADE, related_name="indicadores")
    factor = models.ForeignKey("programas.Factor", on_delete=models.PROTECT, related_name="indicadores")
    codigo = models.CharField(max_length=20)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    tipo_meta = models.CharField(max_length=15, choices=TipoMeta.choices, default=TipoMeta.MAYOR_IGUAL)
    meta = models.DecimalField(max_digits=10, decimal_places=2)
    unidad = models.CharField(max_length=15, choices=Unidad.choices, default=Unidad.PORCENTAJE)
    periodicidad = models.CharField(max_length=15, choices=Periodicidad.choices, default=Periodicidad.SEMESTRAL)
    fuente_verificacion = models.CharField(max_length=255, blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="indicadores_a_cargo",
    )
    requiere_evidencia = models.BooleanField(default=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["programa__nombre", "codigo"]
        constraints = [models.UniqueConstraint(fields=["programa", "codigo"], name="indicador_codigo_unico_programa")]
        verbose_name_plural = "indicadores"

    def __str__(self):
        return f"{self.codigo} – {self.nombre}"

    def clean(self):
        if self.meta is not None and self.meta <= 0 and self.tipo_meta == self.TipoMeta.MAYOR_IGUAL:
            raise ValidationError({"meta": "La meta debe ser mayor que cero."})

    # --- Cálculos -------------------------------------------------------------
    @property
    def ultimo_registro(self):
        regs = getattr(self, "_registros_cache", None)
        if regs is not None:
            return regs[0] if regs else None
        return self.registros.order_by("-fecha_corte", "-id").first()

    def registros_recientes(self, n=4):
        regs = getattr(self, "_registros_cache", None)
        if regs is not None:
            return regs[:n]
        return list(self.registros.order_by("-fecha_corte", "-id")[:n])

    def cumple(self, valor) -> bool:
        if valor is None:
            return False
        return valor >= self.meta if self.tipo_meta == self.TipoMeta.MAYOR_IGUAL else valor <= self.meta

    def cumplimiento(self, valor=None) -> float | None:
        """Porcentaje de cumplimiento de la meta (0–100). Si cumple la meta, es 100 %."""
        if valor is None:
            reg = self.ultimo_registro
            valor = reg.valor if reg else None
        if valor is None:
            return None
        valor, meta = Decimal(valor), Decimal(self.meta)
        if self.cumple(valor):
            return 100.0
        if self.tipo_meta == self.TipoMeta.MAYOR_IGUAL:
            return round(float(max(Decimal(0), valor / meta * 100)), 1)
        # Meta "menor o igual": cada punto por encima de la meta descuenta proporcionalmente.
        if meta == 0:
            return 0.0
        exceso = (valor - meta) / meta
        return round(float(max(Decimal(0), (1 - exceso) * 100)), 1)

    def tendencia(self) -> str:
        """MEJORA, ESTABLE o DETERIORO según los últimos registros, en la dirección de la meta."""
        regs = self.registros_recientes(3)
        if len(regs) < 2:
            return "SIN_DATOS"
        nuevo, viejo = regs[0].valor, regs[-1].valor
        if viejo == 0:
            delta = nuevo - viejo
        else:
            delta = (nuevo - viejo) / abs(viejo)
        if self.tipo_meta == self.TipoMeta.MENOR_IGUAL:
            delta = -delta
        if delta > Decimal("0.02"):
            return "MEJORA"
        if delta < Decimal("-0.02"):
            return "DETERIORO"
        return "ESTABLE"

    def fecha_proxima_actualizacion(self):
        reg = self.ultimo_registro
        base = reg.fecha_corte if reg else self.creado.date()
        return base + timedelta(days=self.DIAS_PERIODICIDAD.get(self.periodicidad, 183))

    def desactualizado(self) -> bool:
        return timezone.localdate() > self.fecha_proxima_actualizacion()

    def estado(self) -> str:
        reg = self.ultimo_registro
        if reg is None:
            return "SIN_DATOS"
        if self.desactualizado():
            return "DESACTUALIZADO"
        if self.cumple(reg.valor):
            return "AL_DIA"
        c = self.cumplimiento(reg.valor) or 0
        return "ALERTA" if c >= 80 else "EN_RIESGO"


class RegistroIndicador(models.Model):
    """Valor medido de un indicador en un periodo. Conserva el histórico para la tendencia."""

    indicador = models.ForeignKey(Indicador, on_delete=models.CASCADE, related_name="registros")
    periodo = models.ForeignKey("programas.Periodo", on_delete=models.PROTECT, related_name="registros")
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_corte = models.DateField(default=timezone.localdate)
    observaciones = models.TextField(blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="registros_indicador"
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha_corte", "-id"]
        constraints = [models.UniqueConstraint(fields=["indicador", "periodo"], name="registro_unico_por_periodo")]
        verbose_name = "registro de indicador"
        verbose_name_plural = "registros de indicador"

    def __str__(self):
        return f"{self.indicador.codigo} {self.periodo}: {self.valor}"
