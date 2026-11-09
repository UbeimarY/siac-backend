"""Consultas compartidas por el tablero de control, los reportes y el asistente de IA."""
from collections import defaultdict

from django.utils import timezone

from apps.alertas import motor
from apps.alertas.models import Alerta, ConfiguracionRiesgo, PuntajeRiesgo
from apps.evidencias.models import Evidencia
from apps.planes.models import PlanMejoramiento
from apps.programas.models import Factor, Periodo, Programa
from apps.usuarios.permisos import filtrar_por_programa


class Filtros:
    """Filtros del tablero y los reportes: programa, periodo, responsable y factor (numeral 2.4.8)."""

    def __init__(self, user, params):
        self.user = user
        self.programa = _int(params.get("programa"))
        self.periodo = _int(params.get("periodo"))
        self.responsable = _int(params.get("responsable"))
        self.factor = _int(params.get("factor"))

    def programas(self):
        qs = filtrar_por_programa(Programa.objects.filter(activo=True), self.user, "id")
        if self.programa:
            qs = qs.filter(id=self.programa)
        return list(qs)

    def periodo_obj(self):
        if self.periodo:
            return Periodo.objects.filter(id=self.periodo).first()
        return None

    def incluye(self, ind) -> bool:
        if self.responsable and ind.responsable_id != self.responsable:
            return False
        if self.factor and ind.factor_id != self.factor:
            return False
        return True

    def como_dict(self):
        return {"programa": self.programa, "periodo": self.periodo, "responsable": self.responsable, "factor": self.factor}


def _int(v):
    try:
        return int(v) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def valor_en_periodo(ind, periodo):
    if periodo is None:
        r = ind.ultimo_registro
        return r.valor if r else None
    for r in ind._registros_cache:
        if r.periodo_id == periodo.id:
            return r.valor
    return None


def estado_en_periodo(ind, periodo):
    if periodo is None:
        return ind.estado()
    v = valor_en_periodo(ind, periodo)
    if v is None:
        return "SIN_DATOS"
    if ind.cumple(v):
        return "AL_DIA"
    return "ALERTA" if (ind.cumplimiento(v) or 0) >= 80 else "EN_RIESGO"


def indicadores(filtros: Filtros, programas=None):
    progs = programas if programas is not None else filtros.programas()
    return [i for i in motor.indicadores_con_datos(progs) if filtros.incluye(i)]


def tablero(filtros: Filtros) -> dict:
    motor.recalcular_si_obsoleto()
    hoy = timezone.localdate()
    progs = filtros.programas()
    periodo = filtros.periodo_obj()
    inds = indicadores(filtros, progs)
    puntajes = {p.indicador_id: p for p in PuntajeRiesgo.objects.filter(indicador__in=[i.id for i in inds])}
    configs = {p.id: ConfiguracionRiesgo.para_programa(p) for p in progs}
    cfg_global = ConfiguracionRiesgo.global_()

    estados = {i.id: estado_en_periodo(i, periodo) for i in inds}
    al_dia = sum(1 for e in estados.values() if e == "AL_DIA")
    pts = [puntajes[i.id].puntaje for i in inds if i.id in puntajes]
    riesgo = motor.agregar(pts) or 0

    ind_ids = {i.id for i in inds}
    evs = Evidencia.objects.filter(indicador_id__in=ind_ids)
    ev_pend = evs.filter(estado=Evidencia.Estado.PENDIENTE).count()
    ev_por_cargar = evs.filter(estado=Evidencia.Estado.POR_CARGAR).count()
    ev_vencidas = sum(1 for e in evs.filter(estado=Evidencia.Estado.POR_CARGAR) if e.vencida)

    planes = PlanMejoramiento.objects.filter(programa__in=progs).exclude(
        estado__in=[PlanMejoramiento.Estado.CERRADO, PlanMejoramiento.Estado.CANCELADO]
    )
    if filtros.responsable:
        planes = planes.filter(responsable_id=filtros.responsable)
    planes = list(planes)
    retrasados = sum(1 for p in planes if p.retrasado(hoy))

    alertas = Alerta.objects.filter(programa__in=progs, estado=Alerta.Estado.ACTIVA)
    if filtros.responsable:
        alertas = alertas.filter(responsable_id=filtros.responsable)
    if filtros.factor:
        alertas = alertas.filter(indicador__factor_id=filtros.factor)
    alertas_list = list(alertas.select_related("programa", "indicador", "responsable"))
    orden = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    alertas_list.sort(key=lambda a: (orden[a.severidad], -a.fecha_generacion.timestamp()))

    # Riesgo por programa y mapa de calor programa × factor
    por_prog = defaultdict(list)
    for i in inds:
        por_prog[i.programa_id].append(i)
    factores = list(Factor.objects.filter(activo=True))
    if filtros.factor:
        factores = [f for f in factores if f.id == filtros.factor]
    riesgo_programas, mapa = [], []
    for p in progs:
        lista = por_prog.get(p.id, [])
        pp = [puntajes[i.id].puntaje for i in lista if i.id in puntajes]
        r = motor.agregar(pp)
        cfg = configs[p.id]
        riesgo_programas.append({
            "id": p.id, "programa": p.nombre, "nombre_corto": p.nombre_corto, "riesgo": r,
            "nivel": cfg.nivel(r) if r is not None else None, "estado": p.get_estado_display(),
            "indicadores": len(lista), "al_dia": sum(1 for i in lista if estados[i.id] == "AL_DIA"),
        })
        celdas = []
        for f in factores:
            fi = [puntajes[i.id].puntaje for i in lista if i.factor_id == f.id and i.id in puntajes]
            v = motor.agregar(fi)
            celdas.append({"factor_id": f.id, "valor": v, "nivel": cfg.nivel(v) if v is not None else None})
        mapa.append({"programa_id": p.id, "programa": p.nombre_corto, "celdas": celdas})
    riesgo_programas.sort(key=lambda x: -(x["riesgo"] or -1))

    # Cumplimiento por responsable
    por_resp = defaultdict(list)
    for i in inds:
        if i.responsable_id:
            por_resp[(i.responsable_id, i.responsable.get_full_name())].append(i)
    responsables = []
    for (rid, nombre), lista in por_resp.items():
        cs = [c for c in (i.cumplimiento(valor_en_periodo(i, periodo)) if valor_en_periodo(i, periodo) is not None else None for i in lista) if c is not None]
        responsables.append({
            "id": rid, "nombre": nombre, "indicadores": len(lista),
            "cumplimiento_promedio": round(sum(cs) / len(cs), 1) if cs else None,
            "al_dia": sum(1 for i in lista if estados[i.id] == "AL_DIA"),
            "alertas": sum(1 for a in alertas_list if a.responsable_id == rid),
        })
    responsables.sort(key=lambda x: (x["cumplimiento_promedio"] is None, x["cumplimiento_promedio"] or 0))

    return {
        "filtros": filtros.como_dict(),
        "periodo": periodo.nombre if periodo else (Periodo.vigente().nombre if Periodo.vigente() else None),
        "kpis": {
            "riesgo_promedio": riesgo,
            "nivel_riesgo": cfg_global.nivel(riesgo) if pts else None,
            "indicadores_total": len(inds),
            "indicadores_al_dia": al_dia,
            "porcentaje_al_dia": round(al_dia / len(inds) * 100, 1) if inds else 0,
            "indicadores_desactualizados": sum(1 for i in inds if i.ultimo_registro is None or i.desactualizado()),
            "evidencias_pendientes_revision": ev_pend,
            "evidencias_por_cargar": ev_por_cargar,
            "evidencias_vencidas": ev_vencidas,
            "acciones_abiertas": len(planes),
            "acciones_retrasadas": retrasados,
            "alertas_activas": len(alertas_list),
            "alertas_altas": sum(1 for a in alertas_list if a.severidad == "ALTA"),
        },
        "umbrales": {"bajo": cfg_global.umbral_bajo, "medio": cfg_global.umbral_medio},
        "riesgo_programas": riesgo_programas,
        "factores": [{"id": f.id, "nombre": f.nombre, "abreviatura": f.abreviatura} for f in factores],
        "mapa_calor": mapa,
        "responsables": responsables,
        "ultimas_alertas": [
            {"id": a.id, "severidad": a.severidad, "tipo": a.get_tipo_display(), "titulo": a.titulo,
             "programa": a.programa.nombre_corto, "responsable": a.responsable.get_full_name() if a.responsable else None,
             "fecha": a.fecha_generacion}
            for a in alertas_list[:6]
        ],
    }
