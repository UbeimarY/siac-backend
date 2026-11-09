"""Construcción del contexto (datos del SIAC) que se entrega a la IA, respetando los permisos del usuario."""
from django.utils import timezone

from apps.alertas.models import Alerta
from apps.planes.models import PlanMejoramiento
from apps.reportes.datos import Filtros, indicadores, tablero

ESTADOS = {"AL_DIA": "al día", "ALERTA": "alerta", "EN_RIESGO": "en riesgo", "DESACTUALIZADO": "desactualizado",
           "SIN_DATOS": "sin datos"}


def _v(v, ind):
    if v is None:
        return "sin dato"
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{s}%" if ind.unidad == "PORCENTAJE" else s


def describir_indicador(ind, historial=True) -> str:
    meta = f"{'≥' if ind.tipo_meta == 'MAYOR_IGUAL' else '≤'} {_v(ind.meta, ind)}"
    reg = ind.ultimo_registro
    linea = (f"{ind.codigo} «{ind.nombre}» ({ind.programa.nombre_corto}, factor {ind.factor.nombre}): "
             f"meta {meta}, último valor {_v(reg.valor if reg else None, ind)}, "
             f"cumplimiento {ind.cumplimiento() if reg else '—'}%, tendencia {ind.tendencia().lower()}, "
             f"estado {ESTADOS.get(ind.estado(), ind.estado())}, periodicidad {ind.get_periodicidad_display().lower()}, "
             f"responsable {ind.responsable.get_full_name() if ind.responsable else 'sin asignar'}")
    if ind.descripcion:
        linea += f". Descripción: {ind.descripcion}"
    if historial:
        regs = sorted(ind.registros_recientes(6), key=lambda r: r.fecha_corte)
        if regs:
            linea += ". Histórico: " + ", ".join(f"{r.periodo.nombre}={_v(r.valor, ind)}" for r in regs)
    return linea


def contexto_general(user, programa_id=None) -> str:
    params = {"programa": programa_id} if programa_id else {}
    f = Filtros(user, params)
    t = tablero(f)
    k = t["kpis"]
    hoy = timezone.localdate()
    partes = [
        f"Fecha de hoy: {hoy:%Y-%m-%d}. Periodo vigente: {t['periodo']}.",
        f"Usuario: {user.get_full_name()} ({user.get_rol_display()}"
        + (f", {user.cargo}" if user.cargo else "") + ").",
        f"Resumen: índice de riesgo promedio {k['riesgo_promedio']}/100; indicadores al día {k['indicadores_al_dia']} "
        f"de {k['indicadores_total']}; desactualizados {k['indicadores_desactualizados']}; evidencias pendientes de "
        f"revisión {k['evidencias_pendientes_revision']}; evidencias por cargar {k['evidencias_por_cargar']} "
        f"(vencidas {k['evidencias_vencidas']}); acciones abiertas {k['acciones_abiertas']} "
        f"(retrasadas {k['acciones_retrasadas']}); alertas activas {k['alertas_activas']} (altas {k['alertas_altas']}).",
        f"Semáforo: bajo ≤ {t['umbrales']['bajo']}, medio ≤ {t['umbrales']['medio']}, alto > {t['umbrales']['medio']}.",
        "\nPROGRAMAS:",
    ]
    progs = {p.id: p for p in f.programas()}
    for r in t["riesgo_programas"]:
        p = progs[r["id"]]
        visita = f", visita de pares {p.fecha_visita_pares:%Y-%m-%d}" if p.fecha_visita_pares else ""
        partes.append(f"- {p.nombre}: {p.get_estado_display()}{visita}; {p.detalle_estado or ''} "
                      f"riesgo {r['riesgo']} ({(r['nivel'] or '—').lower()}), al día {r['al_dia']}/{r['indicadores']}.")
    partes.append("\nINDICADORES:")
    for ind in indicadores(f):
        partes.append("- " + describir_indicador(ind))
    partes.append("\nALERTAS ACTIVAS:")
    alertas = Alerta.objects.filter(programa_id__in=progs.keys(), estado=Alerta.Estado.ACTIVA).select_related(
        "programa", "responsable")[:60]
    for a in alertas:
        partes.append(f"- [{a.severidad}] {a.programa.nombre_corto}: {a.titulo} "
                      f"(responsable {a.responsable.get_full_name() if a.responsable else 'sin asignar'})")
    partes.append("\nPLANES DE MEJORAMIENTO ABIERTOS:")
    planes = PlanMejoramiento.objects.filter(programa_id__in=progs.keys()).exclude(
        estado__in=["CERRADO", "CANCELADO"]).select_related("programa", "indicador", "responsable")[:60]
    for p in planes:
        partes.append(f"- {p.codigo} ({p.programa.nombre_corto}{', ' + p.indicador.codigo if p.indicador else ''}): "
                      f"{p.accion}; avance {p.avance}% de {p.avance_esperado()}% esperado; límite "
                      f"{p.fecha_limite:%Y-%m-%d}; estado {p.estado_visible.lower()}; responsable "
                      f"{p.responsable.get_full_name() if p.responsable else 'sin asignar'}.")
    return "\n".join(partes)
