"""Guardado y carga de los modelos de la IA Beta en la base de datos, con caché en memoria."""
from __future__ import annotations

import threading

from apps.ia.models import ModeloBeta

from .red import RedNeuronal

_cache: dict[str, tuple[int, RedNeuronal, dict]] = {}
_lock = threading.Lock()


def guardar(tipo: str, red: RedNeuronal, extra: dict, metricas: dict, historial: dict,
            n_entrenamiento: int, n_validacion: int, usuario=None) -> ModeloBeta:
    ultima = ModeloBeta.objects.filter(tipo=tipo).order_by("-version").first()
    version = (ultima.version + 1) if ultima else 1
    ModeloBeta.objects.filter(tipo=tipo, activo=True).update(activo=False)
    m = ModeloBeta.objects.create(
        tipo=tipo, version=version, pesos=red.a_bytes(extra), arquitectura=red.describir(),
        parametros=red.num_parametros(), muestras_entrenamiento=n_entrenamiento, muestras_validacion=n_validacion,
        metricas=metricas, historial=historial, activo=True, entrenado_por=usuario,
    )
    with _lock:
        _cache[tipo] = (m.id, red, extra)
    return m


def activo(tipo: str) -> ModeloBeta | None:
    return ModeloBeta.objects.filter(tipo=tipo, activo=True).order_by("-version").first()


def cargar(tipo: str) -> tuple[RedNeuronal, dict, ModeloBeta] | None:
    m = activo(tipo)
    if m is None:
        return None
    with _lock:
        en_cache = _cache.get(tipo)
        if en_cache and en_cache[0] == m.id:
            return en_cache[1], en_cache[2], m
    red, extra = RedNeuronal.desde_bytes(m.pesos)
    with _lock:
        _cache[tipo] = (m.id, red, extra)
    return red, extra, m


def limpiar_cache():
    with _lock:
        _cache.clear()
