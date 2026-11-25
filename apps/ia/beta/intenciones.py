"""Clasificador de intenciones de la IA Beta.

Convierte una pregunta en un vector de características (palabras, pares de palabras y trigramas de
caracteres, con «hashing») y una red neuronal propia (red.py) decide qué quiere saber el usuario.
Las frases de entrenamiento están escritas aquí mismo; no se usa ningún servicio externo.
"""
from __future__ import annotations

import itertools
import re
import unicodedata
import zlib

import numpy as np

from . import almacen
from .red import RedNeuronal

TIPO = "INTENCION"
DIM = 1024
UMBRAL_CONFIANZA = 0.5

INTENCIONES = {
    "saludo_ayuda": "Saludo o ayuda",
    "alertas": "Alertas activas",
    "indicadores_riesgo": "Indicadores en riesgo",
    "evidencias": "Evidencias pendientes",
    "vencimientos": "Próximos vencimientos",
    "resumen_programa": "Resumen del programa",
    "planes": "Planes de mejoramiento",
    "prediccion": "Predicción de incumplimiento",
    "ranking_programas": "Comparación de programas",
    "visita_pares": "Visita de pares",
    "fuera_alcance": "Fuera del alcance del SIAC",
}

FRASES = {
    "saludo_ayuda": [
        "hola", "buenos dias", "buenas tardes", "buenas noches", "hola como estas", "que puedes hacer",
        "en que me ayudas", "ayuda", "necesito ayuda", "como funciona el asistente", "que preguntas puedo hacer",
        "quien eres", "que sabes hacer", "hola asistente", "gracias", "muchas gracias", "ok gracias",
        "para que sirves", "dame opciones", "que informacion tienes", "hey", "saludos", "buen dia",
        "como te uso", "menu de ayuda",
    ],
    "alertas": [
        "que alertas hay", "alertas activas", "cuantas alertas tenemos", "muestrame las alertas",
        "hay alertas de severidad alta", "alertas criticas", "que alertas estan pendientes",
        "listado de alertas", "alertas sin atender", "cuales son las alertas mas graves",
        "tengo alertas nuevas", "alertas del programa", "que alarmas hay", "resumen de alertas",
        "alertas abiertas", "cuantas alertas altas hay", "ver alertas", "dime las alertas",
        "que problemas detecto el motor de alertas", "alertas recientes", "alertas de vencimiento",
        "alertas por tendencia", "que avisos tengo", "notificaciones de alerta", "alertas por atender",
    ],
    "indicadores_riesgo": [
        "que indicadores estan en riesgo", "indicadores criticos", "indicadores con riesgo alto",
        "cuales indicadores no cumplen la meta", "indicadores bajo la meta", "indicadores que incumplen",
        "que indicadores van mal", "indicadores en rojo", "peores indicadores", "indicadores con mayor riesgo",
        "indice de riesgo de los indicadores", "que indicadores debo revisar", "indicadores preocupantes",
        "indicadores desactualizados", "indicadores con tendencia negativa", "indicadores que empeoran",
        "cuales son los indicadores mas riesgosos", "semaforo de indicadores", "indicadores en alerta",
        "que metas no se cumplen", "cumplimiento de indicadores", "estado de los indicadores",
        "indicadores sin datos", "lista de indicadores en riesgo", "top indicadores criticos",
    ],
    "evidencias": [
        "evidencias pendientes", "que evidencias faltan", "evidencias por cargar", "evidencias rechazadas",
        "evidencias por revisar", "cuantas evidencias faltan", "que documentos faltan", "soportes pendientes",
        "evidencias sin aprobar", "que evidencias debo subir", "estado de las evidencias",
        "evidencias en revision", "evidencias faltantes", "archivos pendientes de cargar", "falta subir soportes",
        "evidencias vencidas", "documentos por aprobar", "que soportes rechazaron", "evidencias del programa",
        "listado de evidencias pendientes", "cuantas evidencias aprobadas hay", "revision de evidencias",
        "que evidencia falta para el indicador", "evidencias solicitadas", "pendientes de evidencia",
    ],
    "vencimientos": [
        "que vence esta semana", "proximos vencimientos", "que vence este mes", "fechas limite proximas",
        "que tengo que entregar pronto", "vencimientos de los proximos 15 dias", "que se vence",
        "entregas pendientes esta semana", "calendario de vencimientos", "que reportes vencen",
        "fechas de entrega", "que vence en 30 dias", "plazos proximos", "que esta por vencer",
        "vencimientos de evidencias", "vencimientos de planes", "que debo actualizar pronto",
        "proximas actualizaciones de indicadores", "fechas limite de este mes", "que vence manana",
        "agenda de entregas", "plazos de la semana", "que se vence en 7 dias", "pendientes por fecha",
        "deadlines proximos",
    ],
    "resumen_programa": [
        "resumen del programa", "como va el programa", "estado general del programa", "como vamos",
        "resumen de odontologia", "como esta ingenieria de software", "dame un resumen", "panorama general",
        "situacion actual del programa", "estado del siac", "resumen ejecutivo", "como va odontologia",
        "informe general", "como esta el programa de derecho", "balance del programa", "estado de calidad",
        "resumen general de todos los programas", "como va la acreditacion", "estado del registro calificado",
        "dame el estado actual", "resumen de indicadores alertas y planes", "como estamos en calidad",
        "vision general", "como va industrial", "resumen rapido",
    ],
    "planes": [
        "planes de mejoramiento", "planes retrasados", "que planes estan atrasados", "avance de los planes",
        "acciones de mejora", "planes abiertos", "cuantos planes hay", "planes en ejecucion",
        "acciones de mejoramiento pendientes", "planes con bajo avance", "que acciones estan retrasadas",
        "estado de los planes de mejoramiento", "planes por vencer", "plan de mejora del programa",
        "planes programados", "planes cumplidos", "que planes debo actualizar", "avance de acciones",
        "seguimiento a planes", "acciones correctivas", "planes de mejora atrasados", "listado de planes",
        "que planes tiene el programa", "planes sin avance", "porcentaje de avance de planes",
    ],
    "prediccion": [
        "que indicadores van a incumplir", "prediccion de riesgo", "predice el proximo periodo",
        "probabilidad de incumplir la meta", "que indicadores podrian fallar", "pronostico de indicadores",
        "cuales incumpliran el proximo semestre", "prediccion de la red neuronal", "que dice el modelo",
        "riesgo futuro", "que indicadores estaran en riesgo", "proyeccion de cumplimiento",
        "probabilidad de no cumplir", "anticipa los problemas", "que va a pasar con los indicadores",
        "predicciones de la ia beta", "pronostica el riesgo", "indicadores con alta probabilidad de incumplir",
        "que podria salir mal", "estimacion de incumplimiento", "prevision del proximo periodo",
        "que indicadores empeoraran", "predecir incumplimiento", "modelo predictivo", "prediccion",
    ],
    "ranking_programas": [
        "que programa esta peor", "ranking de programas", "compara los programas",
        "cual programa tiene mas riesgo", "programas con mas alertas", "cual programa va mejor",
        "comparacion entre programas", "programas ordenados por riesgo", "que programa necesita atencion",
        "peor programa", "mejor programa", "indice de riesgo por programa", "programas criticos",
        "cual facultad esta peor", "clasificacion de programas", "programas en riesgo",
        "que programa preocupa mas", "comparativo de programas", "top programas con riesgo",
        "cual programa tiene mas incumplimientos", "ordena los programas", "programas con peor cumplimiento",
        "riesgo de cada programa", "tabla de programas", "posicion de los programas",
    ],
    "visita_pares": [
        "cuando es la visita de pares", "visita de pares academicos", "faltan cuantos dias para la visita",
        "fecha de la visita", "proxima visita de pares", "cuando nos visitan los pares", "visita del cna",
        "visita de acreditacion", "dias para la visita", "programas con visita proxima",
        "estamos listos para la visita", "preparacion para la visita de pares", "visita de registro calificado",
        "cuando vienen los pares", "fecha de visita del ministerio", "que programas tienen visita",
        "cronograma de visitas", "pares evaluadores", "cuanto falta para los pares", "visita programada",
        "agenda de visita de pares", "proximas visitas", "visita de evaluacion externa", "visita",
        "llegada de los pares",
    ],
    "fuera_alcance": [
        "cual es la capital de francia", "cuentame un chiste", "que clima hace hoy", "quien gano el partido",
        "receta de arroz con pollo", "escribe un poema", "cuanto es 2 mas 2", "recomiendame una pelicula",
        "que hora es", "traduce esto al ingles", "como hago un pastel", "quien es el presidente",
        "noticias de hoy", "precio del dolar", "como programar en python", "dame consejos de amor",
        "que musica me recomiendas", "como bajar de peso", "escribe un ensayo de historia",
        "cual es el sentido de la vida", "juguemos algo", "resuelve esta ecuacion", "horoscopo de hoy",
        "donde queda medellin", "mejor celular del mercado",
    ],
}


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ ]+", " ", t).strip()


def _h(token: str) -> int:
    return zlib.crc32(token.encode()) % DIM


def vectorizar(texto: str) -> np.ndarray:
    """Bolsa de palabras + bigramas + trigramas de caracteres, en un vector de tamaño fijo (hashing)."""
    t = normalizar(texto)
    v = np.zeros(DIM)
    palabras = t.split()
    for w in palabras:
        v[_h("w:" + w)] += 1.0
        if len(w) > 4:
            v[_h("r:" + w[:5])] += 0.5  # raíz aproximada: «alertas» ≈ «alerta»
    for a, b in itertools.pairwise(palabras):
        v[_h(f"b:{a}_{b}")] += 1.0
    p = f" {t} "
    for i in range(len(p) - 2):
        v[_h("c:" + p[i:i + 3])] += 0.3
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


RELLENOS = ["", "por favor", "me puedes decir", "quisiera saber", "necesito saber", "oye", "dime",
            "podrias mostrarme", "una consulta"]


def _variantes(frase: str, rng) -> list[str]:
    """Aumento de datos: rellenos de cortesía al inicio o al final y una palabra eliminada al azar."""
    out = [frase]
    for _ in range(3):
        r = RELLENOS[rng.integers(len(RELLENOS))]
        out.append(f"{r} {frase}" if rng.random() < 0.5 else f"{frase} {r}")
    ws = frase.split()
    if len(ws) > 3:
        k = rng.integers(len(ws))
        out.append(" ".join(ws[:k] + ws[k + 1:]))
    return out


def construir_dataset(semilla=3):
    rng = np.random.default_rng(semilla)
    etiquetas = list(INTENCIONES)
    filas, ys, origen = [], [], []
    for k, intent in enumerate(etiquetas):
        for j, frase in enumerate(FRASES[intent]):
            for var in _variantes(frase, rng):
                filas.append(vectorizar(var))
                ys.append(k)
                origen.append(f"{intent}:{j}")
    return np.array(filas), np.array(ys), np.array(origen), etiquetas


def entrenar(usuario=None, semilla: int = 11):
    X, y, origen, etiquetas = construir_dataset()
    rng = np.random.default_rng(semilla)
    # Validación estratificada por frase original: las variantes de una misma frase no se reparten.
    frases = np.unique(origen)
    val = set()
    for intent in etiquetas:
        propias = [f for f in frases if f.startswith(intent + ":")]
        rng.shuffle(propias)
        val.update(propias[: max(1, len(propias) // 5)])
    es_val = np.array([o in val for o in origen])
    red = RedNeuronal([DIM, 64, len(etiquetas)], "softmax", dropout=0.2, semilla=semilla)
    hist = red.entrenar(X[~es_val], y[~es_val], X[es_val], y[es_val], epocas=200, lote=32, tasa=0.003,
                        l2=1e-4, paciencia=30, min_epocas=20)
    p_val = red.predecir_proba(X[es_val]).argmax(axis=1)
    p_tr = red.predecir_proba(X[~es_val]).argmax(axis=1)
    y_val = y[es_val]
    por_clase = {}
    for k, intent in enumerate(etiquetas):
        m = y_val == k
        por_clase[intent] = round(float((p_val[m] == k).mean()), 4) if m.any() else None
    matriz = np.zeros((len(etiquetas), len(etiquetas)), dtype=int)
    for a, b in zip(y_val, p_val, strict=True):
        matriz[a, b] += 1
    # Versión final con todas las frases y las épocas que funcionaron mejor en validación.
    final = RedNeuronal([DIM, 64, len(etiquetas)], "softmax", dropout=0.2, semilla=semilla)
    final.entrenar(X, y, epocas=max(hist.mejor_epoca + 1, 20), lote=32, tasa=0.003, l2=1e-4)
    metricas = {
        "validacion": {"exactitud": round(float((p_val == y_val).mean()), 4)},
        "entrenamiento": {"exactitud": round(float((p_tr == y[~es_val]).mean()), 4)},
        "por_intencion": por_clase,
        "matriz": matriz.tolist(),
        "etiquetas": etiquetas,
        "metodo_validacion": "20 % de las frases de cada intención, con sus variantes, fuera del entrenamiento",
        "frases": len(frases),
        "umbral_confianza": UMBRAL_CONFIANZA,
    }
    extra = {"etiquetas": etiquetas, "dim": DIM}
    modelo = almacen.guardar(TIPO, final, extra, metricas, hist.como_dict(), int((~es_val).sum()),
                             int(es_val.sum()), usuario)
    return modelo, None


def clasificar(texto: str) -> tuple[str, float, list[tuple[str, float]]] | None:
    """Devuelve (intención, confianza, top-3) o None si no hay modelo entrenado."""
    cargado = almacen.cargar(TIPO)
    if cargado is None:
        return None
    red, extra, _ = cargado
    p = red.predecir_proba(vectorizar(texto)[None, :])[0]
    orden = np.argsort(p)[::-1]
    etiquetas = extra["etiquetas"]
    top = [(etiquetas[i], round(float(p[i]), 4)) for i in orden[:3]]
    return top[0][0], top[0][1], top
