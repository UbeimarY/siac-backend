"""Agregados del índice de riesgo por programa y por factor (panel comparativo, numeral 2.5.7)."""
from collections import defaultdict

from django.db.models import Count, Q

from apps.programas.models import Factor

from .models import Alerta, ConfiguracionRiesgo, PuntajeRiesgo
from .motor import indicadores_con_datos


def _nivel(cfg, puntaje):
    return cfg.nivel(puntaje) if puntaje is not None else None


def resumen_programas(programas, detalle=False, indicadores_filtro=None):
    """Para cada programa: índice de riesgo, nivel, alertas y (opcional) semáforo por factor.

    `indicadores_filtro` permite restringir (p. ej. por responsable o factor) los indicadores
    que se agregan."""
    programas = list(programas)
    if not programas:
        return []
    inds = indicadores_con_datos(programas)
    if indicadores_filtro is not None:
        inds = [i for i in inds if indicadores_filtro(i)]
    puntajes = {p.indicador_id: p for p in PuntajeRiesgo.objects.filter(indicador__in=[i.id for i in inds])}
    alertas = dict(
        Alerta.objects.filter(programa__in=programas, estado=Alerta.Estado.ACTIVA)
        .values_list("programa").annotate(n=Count("id")).values_list("programa", "n")
    )
    altas = dict(
        Alerta.objects.filter(programa__in=programas, estado=Alerta.Estado.ACTIVA, severidad=Alerta.Severidad.ALTA)
        .values_list("programa").annotate(n=Count("id")).values_list("programa", "n")
    )
    por_prog = defaultdict(list)
    for i in inds:
        por_prog[i.programa_id].append(i)
    factores = list(Factor.objects.filter(activo=True))

    out = []
    for p in programas:
        cfg = ConfiguracionRiesgo.para_programa(p)
        lista = por_prog.get(p.id, [])
        pts = [puntajes[i.id].puntaje for i in lista if i.id in puntajes]
        riesgo = round(sum(pts) / len(pts), 1) if pts else None
        estados = [i.estado() for i in lista]
        d = {
            "id": p.id,
            "riesgo": riesgo,
            "nivel": _nivel(cfg, riesgo),
            "alertas_activas": alertas.get(p.id, 0),
            "alertas_altas": altas.get(p.id, 0),
            "indicadores_total": len(lista),
            "indicadores_al_dia": estados.count("AL_DIA"),
            "umbral_bajo": cfg.umbral_bajo,
            "umbral_medio": cfg.umbral_medio,
            "config_propia": cfg.programa_id is not None,
        }
        if detalle:
            sem = []
            for f in factores:
                fi = [i for i in lista if i.factor_id == f.id and i.id in puntajes]
                if not fi:
                    sem.append({"factor_id": f.id, "factor": f.nombre, "abreviatura": f.abreviatura,
                                "riesgo": None, "nivel": None, "indicadores": 0})
                    continue
                r = round(sum(puntajes[i.id].puntaje for i in fi) / len(fi), 1)
                sem.append({"factor_id": f.id, "factor": f.nombre, "abreviatura": f.abreviatura,
                            "riesgo": r, "nivel": _nivel(cfg, r), "indicadores": len(fi)})
            d["semaforo_factores"] = sem
        out.append(d)
    return out


def contar_alertas(qs):
    return qs.aggregate(
        total=Count("id"),
        altas=Count("id", filter=Q(severidad=Alerta.Severidad.ALTA)),
        medias=Count("id", filter=Q(severidad=Alerta.Severidad.MEDIA)),
        bajas=Count("id", filter=Q(severidad=Alerta.Severidad.BAJA)),
    )
