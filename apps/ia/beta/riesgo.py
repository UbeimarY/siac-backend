"""IA Beta · Predicción del riesgo de incumplimiento de cada indicador en el próximo periodo.

La red aprende del histórico registrado en SIAC Monitor: para cada indicador toma una ventana de sus últimos
valores (hasta cuatro) y aprende si el valor siguiente incumplió la meta. Al predecir, aplica la misma ventana
sobre los últimos registros y estima la probabilidad de que el próximo valor no alcance la meta.

Flujo: serie de valores → 15 características (caracteristicas) → normalización → red [15, 16, 8, 1] con
salida sigmoide → probabilidad entre 0 y 1 → nivel ALTO (≥ 0,66), MEDIO (≥ 0,33) o BAJO.

Honestidad metodológica: el modelo se compara siempre con una regla simple de «persistencia» (el próximo
periodo repite el estado actual). Con los datos ficticios de demostración, el histórico es corto y la red rinde
parecido a esa regla; las dos métricas se muestran lado a lado en la página IA Beta.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from apps.indicadores.models import Indicador, RegistroIndicador

from . import almacen
from .red import RedNeuronal

TIPO = "RIESGO"
VENTANA = 4
PERIODICIDADES = ["MENSUAL", "TRIMESTRAL", "SEMESTRAL", "ANUAL"]
# Las 15 características que la red «ve» de cada indicador, calculadas con sus últimos cuatro valores.
CARACTERISTICAS = [
    "Cumplimiento actual",
    "Cumple la meta",
    "Distancia a la meta",
    "Último cambio",
    "Pendiente reciente",
    "Variabilidad",
    "Proporción de periodos cumplidos",
    "Racha de incumplimientos",
    "Cantidad de registros",
    "Periodicidad mensual",
    "Periodicidad trimestral",
    "Periodicidad semestral",
    "Periodicidad anual",
    "Meta de tipo «menor o igual»",
    "Unidad porcentual",
]
CONTINUAS = list(range(0, 9))  # columnas sobre las que se aplica ruido al aumentar datos


@dataclass
class Prediccion:
    indicador_id: int
    probabilidad: float
    nivel: str
    factores: list[str]


def _cumple(ind: Indicador, v: float) -> bool:
    meta = float(ind.meta)
    return v >= meta if ind.tipo_meta == Indicador.TipoMeta.MAYOR_IGUAL else v <= meta


def _cumplimiento(ind: Indicador, v: float) -> float:
    meta = float(ind.meta)
    if _cumple(ind, v):
        return 1.0
    if ind.tipo_meta == Indicador.TipoMeta.MAYOR_IGUAL:
        return max(0.0, v / meta) if meta else 0.0
    return max(0.0, 1 - (v - meta) / meta) if meta else 0.0


def caracteristicas(ind: Indicador, valores: list[float]) -> np.ndarray:
    """Vector de entrada a partir de los valores en orden cronológico (el último es el más reciente).

    Todo se expresa en la dirección de la meta (signo) y relativo a ella (÷ meta), para que indicadores con
    escalas distintas (porcentajes, números, escalas de 1 a 5) sean comparables para la red.
    """
    v = np.array(valores[-VENTANA:], dtype=float)
    meta = float(ind.meta) or 1.0
    signo = 1.0 if ind.tipo_meta == Indicador.TipoMeta.MAYOR_IGUAL else -1.0
    actual = v[-1]
    cumple = [_cumple(ind, x) for x in v]
    racha = 0
    for c in reversed(cumple):
        if c:
            break
        racha += 1
    if len(v) >= 2:
        delta = signo * (v[-1] - v[-2]) / meta
        pendiente = signo * np.polyfit(np.arange(len(v)), v, 1)[0] / meta
        variab = float(np.std(v)) / meta
    else:
        delta = pendiente = variab = 0.0
    x = [
        _cumplimiento(ind, actual),
        1.0 if cumple[-1] else 0.0,
        np.clip(signo * (actual - meta) / meta, -1, 1),
        np.clip(delta, -1, 1),
        np.clip(pendiente, -1, 1),
        np.clip(variab, 0, 1),
        float(np.mean(cumple)),
        racha / VENTANA,
        len(v) / VENTANA,
        *[1.0 if ind.periodicidad == p else 0.0 for p in PERIODICIDADES],
        1.0 if ind.tipo_meta == Indicador.TipoMeta.MENOR_IGUAL else 0.0,
        1.0 if ind.unidad == Indicador.Unidad.PORCENTAJE else 0.0,
    ]
    return np.array(x, dtype=float)


def _historicos(indicadores=None) -> dict[int, tuple[Indicador, list[float]]]:
    qs = indicadores if indicadores is not None else Indicador.objects.filter(activo=True)
    inds = {i.id: i for i in qs}
    series: dict[int, list[float]] = {i: [] for i in inds}
    regs = RegistroIndicador.objects.filter(indicador_id__in=inds.keys()).order_by("fecha_corte", "id")
    for r in regs.values_list("indicador_id", "valor"):
        series[r[0]].append(float(r[1]))
    return {i: (inds[i], s) for i, s in series.items()}


def construir_dataset(datos=None):
    """Muestras (ventana -> ¿el siguiente valor incumple?) y el indicador al que pertenece cada una.

    Ejemplo: un indicador con valores [70, 78, 82, 79] y meta 80 genera tres ejemplos de entrenamiento:
    [70] → 78 incumple (1); [70, 78] → 82 cumple (0); [70, 78, 82] → 79 incumple (1).
    """
    datos = datos if datos is not None else _historicos()
    X, y, grupos = [], [], []
    for iid, (ind, serie) in datos.items():
        for t in range(1, len(serie)):
            X.append(caracteristicas(ind, serie[:t]))
            y.append(0 if _cumple(ind, serie[t]) else 1)
            grupos.append(iid)
    return np.array(X), np.array(y), np.array(grupos)


def _metricas(y, p, umbral=0.5):
    pred = (p >= umbral).astype(int)
    vp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    vn = int(((pred == 0) & (y == 0)).sum())
    prec = vp / (vp + fp) if vp + fp else 0.0
    rec = vp / (vp + fn) if vp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"exactitud": round((vp + vn) / max(len(y), 1), 4), "precision": round(prec, 4), "sensibilidad": round(rec, 4),
            "f1": round(f1, 4), "brier": round(float(np.mean((p - y) ** 2)) if len(y) else 0.0, 4),
            "matriz": {"vp": vp, "fp": fp, "fn": fn, "vn": vn}}


def _aumentar(Xtr, ytr, rng, copias=4):
    """Aumento de datos: copias con ruido pequeño en las variables continuas."""
    partes = [Xtr]
    for _ in range(copias):
        r = Xtr.copy()
        r[:, CONTINUAS] += rng.normal(0, 0.02, (len(r), len(CONTINUAS)))
        partes.append(r)
    return np.vstack(partes), np.tile(ytr, len(partes))


def _pesos_clase(y):
    n1 = max(int(y.sum()), 1)
    n0 = max(len(y) - n1, 1)
    return {0: len(y) / (2 * n0), 1: len(y) / (2 * n1)}  # compensa el desbalance de clases


HIPER = {"capas_ocultas": [16, 8], "dropout": 0.1, "tasa": 0.005, "l2": 1e-2, "lote": 32}


def _nueva_red(n_entradas, semilla):
    return RedNeuronal([n_entradas, *HIPER["capas_ocultas"], 1], "sigmoide", dropout=HIPER["dropout"], semilla=semilla)


def entrenar(usuario=None, semilla: int = 7, datos=None, pliegues: int = 5):
    """Evalúa con validación cruzada por indicador y entrena la versión final con todos los datos.

    Por qué validación cruzada AGRUPADA: si los periodos de un mismo indicador quedaran a la vez en
    entrenamiento y en validación, la red «vería» la serie que luego se le pregunta y las métricas saldrían
    infladas. Por eso cada indicador completo queda en un solo pliegue.

    Devuelve (ModeloBeta, None) o (None, mensaje de error)."""
    X, y, grupos = construir_dataset(datos)
    if len(X) < 20 or len(set(y.tolist())) < 2:
        mensaje = ("No hay suficientes datos históricos para entrenar: se necesitan al menos 20 pares de "
                   + "periodos consecutivos con casos que cumplan y que incumplan la meta.")
        return None, mensaje
    rng = np.random.default_rng(semilla)
    ids = np.unique(grupos)
    rng.shuffle(ids)
    pliegues = max(2, min(pliegues, len(ids)))
    asignacion = {g: k % pliegues for k, g in enumerate(ids)}
    pliegue = np.array([asignacion[g] for g in grupos])
    p_oof = np.zeros(len(y))
    mejores, historial = [], None
    # Validación cruzada agrupada: cada indicador se evalúa con una red que nunca vio su serie.
    for k in range(pliegues):
        tr, va = pliegue != k, pliegue == k
        # Normalización con la media y desviación SOLO del entrenamiento (la validación no debe filtrarse).
        media, desv = X[tr].mean(axis=0), X[tr].std(axis=0)
        desv[desv < 1e-6] = 1.0
        Xa, ya = _aumentar(X[tr], y[tr], rng)  # aumento de datos: copias con un poco de ruido
        red = _nueva_red(X.shape[1], semilla + k)
        h = red.entrenar((Xa - media) / desv, ya, (X[va] - media) / desv, y[va], epocas=300, lote=HIPER["lote"],
                         tasa=HIPER["tasa"], l2=HIPER["l2"], pesos_clase=_pesos_clase(ya), paciencia=40, min_epocas=15)
        p_oof[va] = red.predecir_proba((X[va] - media) / desv)
        mejores.append(h.mejor_epoca + 1)
        if historial is None:
            historial = h.como_dict()
    # Modelo final con todos los datos y el número de épocas típico de la validación cruzada.
    epocas_final = int(np.median(mejores))
    media, desv = X.mean(axis=0), X.std(axis=0)
    desv[desv < 1e-6] = 1.0
    Xa, ya = _aumentar(X, y, rng)
    red = _nueva_red(X.shape[1], semilla)
    red.entrenar((Xa - media) / desv, ya, epocas=epocas_final, lote=HIPER["lote"], tasa=HIPER["tasa"],
                 l2=HIPER["l2"], pesos_clase=_pesos_clase(ya))
    base = (X[:, 1] < 0.5).astype(float)  # línea base: «si hoy incumple, el próximo también» (la columna vale 0 o 1)
    metricas = {
        "validacion": _metricas(y, p_oof),
        "linea_base": _metricas(y, base) | {"descripcion": "Persistencia: el próximo periodo repite el estado actual"},
        "entrenamiento": _metricas(y, red.predecir_proba((X - media) / desv)),
        "metodo_validacion": f"Validación cruzada agrupada por indicador ({pliegues} pliegues)",
        "pliegues": pliegues,
        "epocas_final": epocas_final,
        "indicadores": len(ids),
        "tasa_incumplimiento": round(float(y.mean()), 4),
        "hiperparametros": HIPER,
    }
    extra = {"media": media.tolist(), "desv": desv.tolist(), "caracteristicas": CARACTERISTICAS}
    modelo = almacen.guardar(TIPO, red, extra, metricas, historial or {}, len(X), len(X), usuario)
    return modelo, None


def _nivel(p: float) -> str:
    return "ALTO" if p >= 0.66 else "MEDIO" if p >= 0.33 else "BAJO"


def _factores(x: np.ndarray) -> list[str]:
    """Explicación sencilla de las señales que suelen pesar en la predicción."""
    f = []
    if x[1] < 0.5:  # «cumple la meta» vale 0 o 1; se compara con un umbral, no por igualdad
        f.append(f"Hoy no cumple la meta (cumplimiento {x[0] * 100:.0f} %)")
    if x[4] < -0.02:
        f.append("Tendencia reciente en contra de la meta")
    elif x[4] > 0.02:
        f.append("Tendencia reciente a favor de la meta")
    if x[7] >= 0.5:
        f.append("Varios periodos seguidos sin cumplir")
    if x[5] > 0.1:
        f.append("Valores con alta variabilidad")
    if x[8] <= 0.5:
        f.append("Pocos registros históricos: predicción menos confiable")
    if not f:
        f.append("Cumple la meta con estabilidad")
    return f


def predecir(indicadores) -> dict[int, Prediccion]:
    """Predicción para cada indicador con al menos un registro. Devuelve {} si no hay modelo entrenado."""
    cargado = almacen.cargar(TIPO)
    if cargado is None:
        return {}
    red, extra, _ = cargado
    media, desv = np.array(extra["media"]), np.array(extra["desv"])
    datos = _historicos(indicadores)
    ids, X = [], []
    for iid, (ind, serie) in datos.items():
        if serie:
            ids.append(iid)
            X.append(caracteristicas(ind, serie))
    if not X:
        return {}
    X = np.array(X)
    p = red.predecir_proba((X - media) / desv)
    return {iid: Prediccion(iid, round(float(pi), 4), _nivel(float(pi)), _factores(x)) for iid, pi, x in zip(ids, p, X, strict=True)}
