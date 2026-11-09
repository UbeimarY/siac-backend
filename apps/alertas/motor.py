"""
Motor de alertas tempranas basado en indicadores ponderados y configurables (numeral 2.5.7).

Capas:
1. Análisis de tendencia histórica (mejora / estable / deterioro).
2. Puntaje de riesgo ponderado por indicador y por programa (0–100), combinando:
   tendencia, brecha frente a la meta, evidencia faltante, proximidad al vencimiento
   y cercanía a la visita de pares del CNA.
3. Motor de reglas configurable: pesos y umbrales por programa sin modificar código.
4. Panel comparativo: el índice por programa alimenta el tablero de control.

Las alertas NO son predicciones estadísticas (numeral 4.2.4): son reglas de seguimiento.
Disparadores (numeral 2.4.6): resultado inferior a la meta, ausencia de evidencia, falta de
actualización de datos, vencimiento de una actividad y demora en una acción de mejoramiento.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.core.cache import cache
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.evidencias.models import Evidencia
from apps.indicadores.models import Indicador, RegistroIndicador
from apps.planes.models import PlanMejoramiento
from apps.programas.models import Programa

from .models import Alerta, ConfiguracionRiesgo, PuntajeRiesgo

log = logging.getLogger(__name__)

FACTOR_TENDENCIA = {"DETERIORO": 1.0, "SIN_DATOS": 0.5, "ESTABLE": 0.4, "MEJORA": 0.0}
SEVERIDAD_POR_NIVEL = {"ALTO": Alerta.Severidad.ALTA, "MEDIO": Alerta.Severidad.MEDIA, "BAJO": Alerta.Severidad.BAJA}
DIAS_VISITA_CRITICA = 180
CACHE_KEY = "motor_alertas_ultima_ejecucion"
# Una brecha de 25 % frente a la meta ya se considera riesgo máximo en ese componente.
BRECHA_MAXIMA = 0.25


@dataclass
class ResultadoIndicador:
    indicador: Indicador
    puntaje: float
    nivel: str
    componentes: dict = field(default_factory=dict)


def _componente_evidencia(ind: Indicador, hoy) -> float:
    if not ind.requiere_evidencia:
        return 0.0
    evs = getattr(ind, "_evidencias_cache", None)
    if evs is None:
        evs = list(ind.evidencias.all())
    vigentes = [e for e in evs if e.vigente_en(hoy)]
    if any(e.estado == Evidencia.Estado.APROBADA for e in vigentes):
        return 0.0
    if vigentes:  # solo pendientes de revisión
        return 0.4
    return 1.0


def _componente_vencimiento(ind: Indicador, cfg: ConfiguracionRiesgo, hoy) -> float:
    aviso = max(cfg.dias_aviso_vencimiento, 1)
    fechas = [ind.fecha_proxima_actualizacion()]
    evs = getattr(ind, "_evidencias_cache", None)
    if evs is None:
        evs = list(ind.evidencias.all())
    fechas += [e.fecha_limite for e in evs if e.estado == Evidencia.Estado.POR_CARGAR and e.fecha_limite]
    peor = 0.0
    for f in fechas:
        dias = (f - hoy).days
        if dias < 0:
            return 1.0
        if dias <= aviso:
            peor = max(peor, 1 - dias / aviso)
    return round(peor, 3)


def _componente_visita(programa: Programa, cfg: ConfiguracionRiesgo) -> float:
    dias = programa.dias_para_visita
    if dias is None or dias < 0 or dias > cfg.horizonte_visita_dias:
        return 0.0
    return round(1 - dias / max(cfg.horizonte_visita_dias, 1), 3)


def calcular_indicador(ind: Indicador, cfg: ConfiguracionRiesgo, hoy=None) -> ResultadoIndicador:
    hoy = hoy or timezone.localdate()
    cumplimiento = ind.cumplimiento()
    tendencia = ind.tendencia()
    f = {
        "tendencia": FACTOR_TENDENCIA.get(tendencia, 0.5),
        "brecha": 1.0 if cumplimiento is None else round(min(1.0, (1 - cumplimiento / 100) / BRECHA_MAXIMA), 3),
        "evidencia": _componente_evidencia(ind, hoy),
        "vencimiento": _componente_vencimiento(ind, cfg, hoy),
        "visita": _componente_visita(ind.programa, cfg),
    }
    pesos = {
        "tendencia": cfg.peso_tendencia,
        "brecha": cfg.peso_brecha,
        "evidencia": cfg.peso_evidencia,
        "vencimiento": cfg.peso_vencimiento,
        "visita": cfg.peso_visita,
    }
    puntaje = round(sum(f[k] * pesos[k] for k in f), 1)
    comps = {k: {"factor": f[k], "peso": pesos[k], "aporte": round(f[k] * pesos[k], 1)} for k in f}
    comps["tendencia"]["valor"] = tendencia
    comps["brecha"]["cumplimiento"] = cumplimiento
    return ResultadoIndicador(ind, puntaje, cfg.nivel(puntaje), comps)


def indicadores_con_datos(programas=None):
    qs = Indicador.objects.filter(activo=True).select_related("programa", "factor", "responsable")
    if programas is not None:
        qs = qs.filter(programa__in=programas)
    qs = qs.prefetch_related(
        Prefetch("registros", queryset=RegistroIndicador.objects.order_by("-fecha_corte", "-id"), to_attr="_regs"),
        Prefetch("evidencias", queryset=Evidencia.objects.all(), to_attr="_evs"),
    )
    out = []
    for ind in qs:
        ind._registros_cache = ind._regs
        ind._evidencias_cache = ind._evs
        out.append(ind)
    return out


def agregar(puntajes: list[float]) -> float | None:
    """Índice de un conjunto de indicadores: la mitad es el promedio general y la otra mitad el promedio de
    los tres indicadores más críticos, para que unos pocos indicadores en riesgo no se diluyan."""
    if not puntajes:
        return None
    criticos = sorted(puntajes, reverse=True)[:3]
    return round(0.5 * sum(puntajes) / len(puntajes) + 0.5 * sum(criticos) / len(criticos), 1)


def riesgo_programa(resultados: list[ResultadoIndicador]) -> float:
    return agregar([r.puntaje for r in resultados]) or 0.0


# --------------------------------------------------------------------------- reglas
def _alerta(claves, tipo, severidad, programa, titulo, detalle, puntaje=0, **rel):
    clave = f"{tipo}:{rel.get('indicador').id if rel.get('indicador') else ''}:" \
            f"{rel.get('evidencia').id if rel.get('evidencia') else ''}:{rel.get('plan').id if rel.get('plan') else ''}"
    claves[clave] = dict(
        tipo=tipo, severidad=severidad, programa=programa, titulo=titulo[:200], detalle=detalle,
        puntaje_riesgo=puntaje, **rel,
    )


def _fmt(valor, ind: Indicador) -> str:
    v = f"{valor:.2f}".rstrip("0").rstrip(".")
    return f"{v}%" if ind.unidad == Indicador.Unidad.PORCENTAJE else v


def _reglas_indicador(res: ResultadoIndicador, claves: dict, hoy):
    ind, prog = res.indicador, res.indicador.programa
    reg = ind.ultimo_registro
    sev_riesgo = SEVERIDAD_POR_NIVEL[res.nivel]
    visita_cerca = prog.dias_para_visita is not None and 0 <= prog.dias_para_visita <= DIAS_VISITA_CRITICA
    signo = "≥" if ind.tipo_meta == Indicador.TipoMeta.MAYOR_IGUAL else "≤"
    rel = {"indicador": ind, "responsable": ind.responsable}

    if reg and not ind.cumple(reg.valor):
        _alerta(
            claves, Alerta.Tipo.BAJO_META, sev_riesgo, prog,
            f"{ind.codigo} {ind.nombre}: {_fmt(reg.valor, ind)} (meta {signo} {_fmt(ind.meta, ind)})",
            f"El último valor registrado ({reg.periodo}) no alcanza la meta. "
            f"Cumplimiento: {res.componentes['brecha']['cumplimiento']}%. Índice de riesgo: {res.puntaje}.",
            res.puntaje, **rel,
        )
    if reg is None or ind.desactualizado():
        prox = ind.fecha_proxima_actualizacion()
        _alerta(
            claves, Alerta.Tipo.DESACTUALIZADO,
            Alerta.Severidad.ALTA if visita_cerca else Alerta.Severidad.MEDIA, prog,
            f"{ind.codigo} {ind.nombre}: sin actualizar"
            + (f" desde {reg.fecha_corte:%Y-%m-%d}" if reg else " (sin registros)"),
            f"Según su periodicidad ({ind.get_periodicidad_display().lower()}) debía actualizarse "
            f"antes del {prox:%Y-%m-%d}.",
            res.puntaje, **rel,
        )
    if res.componentes["evidencia"]["factor"] >= 1.0:
        _alerta(
            claves, Alerta.Tipo.EVIDENCIA_FALTANTE,
            Alerta.Severidad.ALTA if visita_cerca else Alerta.Severidad.MEDIA, prog,
            f"{ind.codigo} {ind.nombre}: sin evidencia vigente",
            "El indicador no tiene evidencia documental vigente que respalde su cumplimiento.",
            res.puntaje, **rel,
        )
    if reg and ind.cumple(reg.valor) and res.componentes["tendencia"]["valor"] == "DETERIORO":
        _alerta(
            claves, Alerta.Tipo.TENDENCIA, Alerta.Severidad.BAJA, prog,
            f"{ind.codigo} {ind.nombre}: tendencia en deterioro",
            "Aunque hoy cumple la meta, los últimos registros muestran deterioro. Alerta preventiva.",
            res.puntaje, **rel,
        )
    for ev in getattr(ind, "_evidencias_cache", []):
        if ev.estado == Evidencia.Estado.POR_CARGAR and ev.fecha_limite and ev.fecha_limite < hoy:
            _alerta(
                claves, Alerta.Tipo.VENCIMIENTO, Alerta.Severidad.ALTA, prog,
                f"{ev.nombre}: no entregado (vencía {ev.fecha_limite:%Y-%m-%d})",
                f"La evidencia solicitada {ev.codigo} para {ind.codigo} superó su fecha límite sin cargarse.",
                res.puntaje, indicador=ind, evidencia=ev, responsable=ev.responsable or ind.responsable,
            )


def _reglas_planes(programas, claves: dict, hoy):
    qs = PlanMejoramiento.objects.select_related("programa", "indicador", "responsable").exclude(
        estado__in=[PlanMejoramiento.Estado.CERRADO, PlanMejoramiento.Estado.CANCELADO]
    )
    if programas is not None:
        qs = qs.filter(programa__in=programas)
    for plan in qs:
        if not plan.retrasado(hoy):
            continue
        vencido = plan.fecha_limite < hoy
        _alerta(
            claves, Alerta.Tipo.PLAN_RETRASADO,
            Alerta.Severidad.ALTA if vencido else Alerta.Severidad.MEDIA, plan.programa,
            f"{plan.codigo} {plan.accion[:120]}: " + ("vencido" if vencido else "avance retrasado"),
            f"Avance {plan.avance}% frente a {plan.avance_esperado(hoy)}% esperado "
            f"(fecha límite {plan.fecha_limite:%Y-%m-%d}).",
            0, plan=plan, indicador=plan.indicador, responsable=plan.responsable,
        )


@transaction.atomic
def recalcular(programas=None) -> dict:
    """Recalcula puntajes y sincroniza alertas. Devuelve un resumen."""
    hoy = timezone.localdate()
    if programas is not None and not isinstance(programas, (list, tuple, set)):
        programas = [programas]
    progs = list(programas) if programas is not None else list(Programa.objects.filter(activo=True))
    configs = {p.id: ConfiguracionRiesgo.para_programa(p) for p in progs}

    claves: dict[str, dict] = {}
    n_ind = 0
    for ind in indicadores_con_datos(progs):
        res = calcular_indicador(ind, configs[ind.programa_id], hoy)
        PuntajeRiesgo.objects.update_or_create(
            indicador=ind, defaults={"puntaje": res.puntaje, "nivel": res.nivel, "componentes": res.componentes}
        )
        _reglas_indicador(res, claves, hoy)
        n_ind += 1
    _reglas_planes(progs, claves, hoy)

    activas = {a.clave: a for a in Alerta.objects.filter(estado=Alerta.Estado.ACTIVA, programa__in=progs)}
    # Las alertas descartadas manualmente no se regeneran mientras la condición persista.
    descartadas = set(
        Alerta.objects.filter(estado=Alerta.Estado.DESCARTADA, programa__in=progs, clave__in=claves.keys())
        .values_list("clave", flat=True)
    )
    creadas = actualizadas = resueltas = 0
    for clave, datos in claves.items():
        if clave in descartadas:
            continue
        if clave in activas:
            a = activas[clave]
            cambios = False
            for k in ("severidad", "titulo", "detalle", "puntaje_riesgo", "responsable"):
                if getattr(a, k) != datos[k]:
                    setattr(a, k, datos[k])
                    cambios = True
            if cambios:
                a.save()
                actualizadas += 1
        else:
            Alerta.objects.create(clave=clave, **datos)
            creadas += 1
    ahora = timezone.now()
    for clave, a in activas.items():
        if clave not in claves:
            a.estado = Alerta.Estado.RESUELTA
            a.fecha_cierre = ahora
            a.comentario_cierre = "La condición que originó la alerta ya no se presenta."
            a.save(update_fields=["estado", "fecha_cierre", "comentario_cierre"])
            resueltas += 1
    if programas is None:
        cache.set(CACHE_KEY, ahora, 60 * 60 * 24)
    resumen = {"indicadores": n_ind, "creadas": creadas, "actualizadas": actualizadas, "resueltas": resueltas}
    log.info("Motor de alertas: %s", resumen)
    return resumen


def recalcular_si_obsoleto(minutos=60):
    """Las reglas dependen de la fecha; se recalcula de forma perezosa si pasó cierto tiempo."""
    ultima = cache.get(CACHE_KEY)
    if ultima is None or (timezone.now() - ultima).total_seconds() > minutos * 60:
        try:
            recalcular()
        except Exception:  # nunca romper una consulta por el motor
            log.exception("Error recalculando alertas")


def recalcular_programa_seguro(programa):
    try:
        recalcular([programa])
    except Exception:
        log.exception("Error recalculando alertas del programa %s", programa)
