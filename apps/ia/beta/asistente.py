"""Asistente propio de la IA Beta.

La red neuronal de intenciones decide QUÉ pregunta el usuario; la respuesta se arma con consultas a la
base de datos del SIAC, siempre filtradas por los programas que ese usuario puede ver. No interviene ningún
modelo de lenguaje externo, así que el asistente no puede inventar datos: o los encuentra o dice que no los hay.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from apps.alertas.models import Alerta, PuntajeRiesgo
from apps.alertas.motor import agregar
from apps.evidencias.models import Evidencia
from apps.indicadores.models import Indicador
from apps.planes.models import PlanMejoramiento
from apps.programas.models import Programa
from apps.usuarios.permisos import filtrar_por_programa

from . import intenciones, riesgo
from .intenciones import INTENCIONES, UMBRAL_CONFIANZA, normalizar

GENERICAS = {"ingenieria", "programa", "facultad", "ciencias", "salud", "de", "del", "la", "el", "en", "y"}
SEV = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
MAX_ITEMS = 8


@dataclass
class Contexto:
    user: object
    texto: str
    programas: list = field(default_factory=list)  # programas a los que se limita la respuesta
    programa: Programa | None = None  # si la pregunta (o el selector) apunta a uno solo
    horizonte: int = 30

    @property
    def ids(self):
        return [p.id for p in self.programas]

    def filtrar(self, qs, campo="programa_id"):
        return qs.filter(**{f"{campo}__in": self.ids})

    @property
    def alcance(self) -> str:
        if self.programa:
            return f" en {self.programa.nombre}"
        return "" if len(self.programas) > 1 else (f" en {self.programas[0].nombre}" if self.programas else "")


# ----------------------------------------------------------------------------- extracción de entidades
def detectar_programa(texto: str, visibles) -> Programa | None:
    palabras = normalizar(texto).split()
    for p in visibles:
        claves = {w[:6] for w in normalizar(f"{p.nombre} {p.nombre_corto}").split()
                  if w not in GENERICAS and len(w) >= 4}
        codigo = p.codigo.lower()
        for w in palabras:
            if w == codigo or (len(w) >= 6 and w[:6] in claves) or (len(w) >= 4 and w in claves):
                return p
    return None


def detectar_horizonte(texto: str, defecto: int = 30) -> int:
    t = normalizar(texto)
    m = re.search(r"(\d{1,3})\s*(dia|dias|semana|semanas|mes|meses)", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return n * (7 if u.startswith("semana") else 30 if u.startswith("mes") else 1)
    if "manana" in t:
        return 1
    if "hoy" in t.split():
        return 0
    if "semana" in t:
        return 7
    if "trimestre" in t:
        return 90
    if "mes" in t.split() or "mes" in t:
        return 30
    return defecto


# ----------------------------------------------------------------------------- utilidades de formato
def _v(valor, ind) -> str:
    if valor is None:
        return "sin dato"
    s = f"{float(valor):.2f}".rstrip("0").rstrip(".")
    return f"{s}%" if ind.unidad == Indicador.Unidad.PORCENTAJE else s


def _fecha(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "sin fecha"


def _dias(d, hoy) -> str:
    n = (d - hoy).days
    if n < 0:
        return f"vencido hace {-n} día{'s' if n != -1 else ''}"
    if n == 0:
        return "vence hoy"
    return f"en {n} día{'s' if n != 1 else ''}"


def _n(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _pct(p: float) -> str:
    v = p * 100
    return "más de 99%" if v > 99 else ("menos de 1%" if v < 1 else f"{v:.0f}%")


def _lista(items, total=None) -> str:
    texto = "\n".join(f"- {i}" for i in items[:MAX_ITEMS])
    total = len(items) if total is None else total
    if total > MAX_ITEMS:
        texto += f"\n- … y {total - MAX_ITEMS} más."
    return texto


def _indicadores(ctx):
    return ctx.filtrar(Indicador.objects.filter(activo=True).select_related("programa", "factor", "responsable"))


# ----------------------------------------------------------------------------- respuestas por intención
def r_saludo(ctx):
    nombre = getattr(ctx.user, "first_name", "") or ""
    return (f"¡Hola{(' ' + nombre) if nombre else ''}! Soy la **IA Beta** del SIAC: una red neuronal entrenada "
            "desde cero que entiende tu pregunta y responde con los datos del sistema que tienes permiso de ver.\n\n"
            "Puedo ayudarte con:\n"
            "- Alertas activas y su severidad\n- Indicadores en riesgo o bajo la meta\n"
            "- Evidencias pendientes, en revisión o rechazadas\n- Vencimientos próximos (p. ej. «qué vence esta semana»)\n"
            "- Planes de mejoramiento y retrasos\n- Resumen de un programa\n"
            "- Predicción de qué indicadores podrían incumplir el próximo periodo\n"
            "- Comparación de programas por riesgo\n- Visitas de pares\n\n"
            "Puedes nombrar el programa en la pregunta, por ejemplo: «alertas de Odontología».")


def r_alertas(ctx):
    qs = ctx.filtrar(Alerta.objects.filter(estado=Alerta.Estado.ACTIVA).select_related("programa", "indicador"))
    t = normalizar(ctx.texto)
    for sev, claves in (("ALTA", ("alta", "altas", "critica", "criticas", "grave", "graves")),
                        ("MEDIA", ("media", "medias")), ("BAJA", ("baja", "bajas"))):
        if any(c in t.split() for c in claves):
            qs = qs.filter(severidad=sev)
            break
    alertas = sorted(qs, key=lambda a: (SEV.get(a.severidad, 3), -a.puntaje_riesgo))
    if not alertas:
        return f"No hay alertas activas{ctx.alcance} con ese criterio. 🎉"
    conteo = {s: sum(1 for a in alertas if a.severidad == s) for s in ("ALTA", "MEDIA", "BAJA")}
    cab = (f"Hay **{_n(len(alertas), 'alerta activa', 'alertas activas')}**{ctx.alcance}: {_n(conteo['ALTA'], 'alta')}, "
           f"{_n(conteo['MEDIA'], 'media')} y {_n(conteo['BAJA'], 'baja')}. Las más importantes:")
    items = [f"**[{a.severidad}]** {a.titulo}" + ("" if len(ctx.programas) == 1 else f" — {a.programa.nombre_corto}")
             for a in alertas]
    return cab + "\n" + _lista(items)


def r_indicadores_riesgo(ctx):
    inds = list(_indicadores(ctx))
    if not inds:
        return f"No encontré indicadores activos{ctx.alcance}."
    estados = {i.id: i.estado() for i in inds}
    puntajes = {p.indicador_id: p for p in PuntajeRiesgo.objects.filter(indicador__in=inds)}
    malos = [i for i in inds if estados[i.id] in ("EN_RIESGO", "ALERTA", "DESACTUALIZADO")]
    malos.sort(key=lambda i: -(puntajes[i.id].puntaje if i.id in puntajes else 0))
    n = {e: sum(1 for v in estados.values() if v == e) for e in ("AL_DIA", "ALERTA", "EN_RIESGO", "DESACTUALIZADO",
                                                                  "SIN_DATOS")}
    cab = (f"De {len(inds)} indicadores{ctx.alcance}: **{n['EN_RIESGO']} en riesgo**, {n['ALERTA']} en alerta, "
           f"{n['DESACTUALIZADO']} desactualizados, {n['SIN_DATOS']} sin datos y {n['AL_DIA']} al día.")
    if not malos:
        return cab + "\nNinguno requiere atención inmediata."
    nombres = {"EN_RIESGO": "en riesgo", "ALERTA": "en alerta", "DESACTUALIZADO": "desactualizado"}
    items = []
    for i in malos:
        reg = i.ultimo_registro
        p = puntajes.get(i.id)
        items.append(f"**{i.codigo}** {i.nombre}: {_v(reg.valor if reg else None, i)} frente a meta "
                     f"{'≥' if i.tipo_meta == 'MAYOR_IGUAL' else '≤'} {_v(i.meta, i)} ({nombres[estados[i.id]]}"
                     + (f", riesgo {p.puntaje:.0f}/100" if p else "") + ")")
    return cab + " Los que más preocupan:\n" + _lista(items)


def r_evidencias(ctx):
    E = Evidencia.Estado
    qs = ctx.filtrar(Evidencia.objects.select_related("indicador", "indicador__programa"), "indicador__programa_id")
    t = normalizar(ctx.texto)
    estados = [E.POR_CARGAR, E.PENDIENTE, E.RECHAZADA]
    if "rechaz" in t:
        estados = [E.RECHAZADA]
    elif "revis" in t or "aprobar" in t:
        estados = [E.PENDIENTE]
    elif "cargar" in t or "subir" in t or "faltan" in t:
        estados = [E.POR_CARGAR]
    pend = list(qs.filter(estado__in=estados).order_by("fecha_limite", "codigo"))
    tot = {e: qs.filter(estado=e).count() for e in E.values}
    cab = (f"Evidencias{ctx.alcance}: {tot['POR_CARGAR']} por cargar, {tot['PENDIENTE']} pendientes de revisión, "
           f"{tot['RECHAZADA']} rechazadas y {tot['APROBADA']} aprobadas.")
    if not pend:
        return cab + "\nNo hay evidencias en ese estado."
    hoy = timezone.localdate()
    items = [f"**{e.codigo}** {e.nombre} ({e.get_estado_display().lower()}, indicador {e.indicador.codigo}"
             + (f", {_dias(e.fecha_limite, hoy)}" if e.fecha_limite else "") + ")" for e in pend]
    return cab + "\n" + _lista(items)


def r_vencimientos(ctx):
    hoy = timezone.localdate()
    hasta = hoy + timedelta(days=ctx.horizonte)
    eventos = []
    evs = ctx.filtrar(Evidencia.objects.filter(estado=Evidencia.Estado.POR_CARGAR, fecha_limite__lte=hasta)
                      .select_related("indicador"), "indicador__programa_id")
    for e in evs:
        eventos.append((e.fecha_limite, f"Evidencia **{e.codigo}** {e.nombre} — {_dias(e.fecha_limite, hoy)}"))
    planes = ctx.filtrar(PlanMejoramiento.objects.exclude(estado__in=["CERRADO", "CANCELADO"])
                         .filter(fecha_limite__lte=hasta))
    for p in planes:
        eventos.append((p.fecha_limite, f"Plan **{p.codigo}** {p.accion} ({p.avance}%) — {_dias(p.fecha_limite, hoy)}"))
    for i in _indicadores(ctx):
        f = i.fecha_proxima_actualizacion()
        if f <= hasta:
            eventos.append((f, f"Actualizar indicador **{i.codigo}** {i.nombre} — {_dias(f, hoy)}"))
    periodo = "hoy" if ctx.horizonte == 0 else f"los próximos {ctx.horizonte} días"
    if not eventos:
        return f"No hay entregas ni actualizaciones que venzan en {periodo}{ctx.alcance}."
    eventos.sort(key=lambda x: x[0])
    vencidos = sum(1 for f, _ in eventos if f < hoy)
    cab = (f"Para {periodo}{ctx.alcance} hay **{len(eventos)} vencimientos**"
           + (f", de los cuales {vencidos} ya están vencidos" if vencidos else "") + ":")
    return cab + "\n" + _lista([t for _, t in eventos])


def r_planes(ctx):
    qs = ctx.filtrar(PlanMejoramiento.objects.select_related("programa", "indicador"))
    abiertos = [p for p in qs if p.abierto]
    retrasados = [p for p in abiertos if p.retrasado()]
    cerrados = qs.filter(estado="CERRADO").count()
    cab = (f"Planes de mejoramiento{ctx.alcance}: **{len(abiertos)} abiertos** ({len(retrasados)} retrasados) y "
           f"{cerrados} cerrados.")
    lista = retrasados if retrasados else abiertos
    if not lista:
        return cab + "\nNo hay planes abiertos."
    lista.sort(key=lambda p: (p.avance - p.avance_esperado(), p.fecha_limite))
    titulo = "Retrasados (avance real frente al esperado):" if retrasados else "Abiertos:"
    items = [f"**{p.codigo}** {p.accion}: {p.avance}% de avance (esperado {p.avance_esperado()}%), "
             f"límite {_fecha(p.fecha_limite)}" for p in lista]
    return cab + "\n" + titulo + "\n" + _lista(items)


def r_resumen(ctx):
    partes = []
    progs = [ctx.programa] if ctx.programa else ctx.programas
    if len(progs) == 1:
        p = progs[0]
        partes.append(f"**{p.nombre}** — {p.get_estado_display().lower()}."
                      + (f" Visita de pares el {_fecha(p.fecha_visita_pares)} ({p.dias_para_visita} días)."
                         if p.fecha_visita_pares else ""))
    else:
        partes.append(f"Resumen de los {len(progs)} programas que puedes ver.")
    inds = list(_indicadores(ctx))
    est = [i.estado() for i in inds]
    puntajes = list(PuntajeRiesgo.objects.filter(indicador__in=inds).values_list("puntaje", flat=True))
    indice = agregar(puntajes)
    al_dia = est.count("AL_DIA")
    partes.append(f"- Indicadores: {len(inds)} ({al_dia} al día, {est.count('EN_RIESGO')} en riesgo, "
                  f"{est.count('ALERTA')} en alerta, {est.count('DESACTUALIZADO')} desactualizados)"
                  + (f"; índice de riesgo {indice}/100" if indice is not None else ""))
    al = ctx.filtrar(Alerta.objects.filter(estado="ACTIVA"))
    partes.append(f"- Alertas activas: {al.count()} ({_n(al.filter(severidad='ALTA').count(), 'alta')})")
    ev = ctx.filtrar(Evidencia.objects.all(), "indicador__programa_id")
    por_cargar = ev.filter(estado="POR_CARGAR").count()
    en_revision = ev.filter(estado="PENDIENTE").count()
    aprobadas = ev.filter(estado="APROBADA").count()
    partes.append(f"- Evidencias: {por_cargar} por cargar, {en_revision} en revisión, {aprobadas} aprobadas")
    planes = [p for p in ctx.filtrar(PlanMejoramiento.objects.all()) if p.abierto]
    partes.append(f"- Planes abiertos: {len(planes)} ({_n(sum(1 for p in planes if p.retrasado()), 'retrasado')})")
    preds = riesgo.predecir(inds)
    if preds:
        altos = sum(1 for x in preds.values() if x.nivel == "ALTO")
        partes.append(f"- Predicción IA Beta: {altos} indicadores con probabilidad alta de incumplir el próximo periodo")
    return "\n".join(partes)


def r_prediccion(ctx):
    inds = list(_indicadores(ctx))
    preds = riesgo.predecir(inds)
    if not preds:
        return ("El modelo de predicción todavía no está entrenado o no hay suficiente historial. Un administrador "
                "puede entrenarlo desde la página **IA Beta**.")
    por_id = {i.id: i for i in inds}
    orden = sorted(preds.values(), key=lambda x: -x.probabilidad)
    altos = [x for x in orden if x.nivel == "ALTO"]
    medios = [x for x in orden if x.nivel == "MEDIO"]
    cab = (f"La red neuronal analizó {len(preds)} indicadores{ctx.alcance} con historial: **{len(altos)} con "
           f"probabilidad alta** de incumplir la meta en el próximo periodo y {len(medios)} con probabilidad media.")
    lista = altos + medios
    if not lista:
        return cab + "\nNingún indicador muestra señales de incumplimiento."
    items = []
    for x in lista:
        i = por_id[x.indicador_id]
        motivo = f" — {x.factores[0]}" if x.factores else ""
        items.append(f"**{i.codigo}** {i.nombre}: {_pct(x.probabilidad)}{motivo}")
    return (cab + "\n" + _lista(items) +
            "\n\n_Es una estimación estadística a partir del histórico; úsala para priorizar la revisión, no como "
            "resultado definitivo._")


def r_ranking(ctx):
    filas = []
    for p in ctx.programas:
        punt = list(PuntajeRiesgo.objects.filter(indicador__programa=p, indicador__activo=True)
                    .values_list("puntaje", flat=True))
        al = Alerta.objects.filter(programa=p, estado="ACTIVA")
        filas.append((agregar(punt) or 0.0, al.count(), al.filter(severidad="ALTA").count(), p))
    if not filas:
        return "No tienes programas visibles para comparar."
    if len(filas) == 1:
        return "Solo puedes ver un programa, así que no hay comparación posible. " + r_resumen(ctx)
    filas.sort(key=lambda f: (-f[0], -f[2]))
    items = [f"{n}. **{p.nombre}** — índice de riesgo {r:.1f}/100, {_n(a, 'alerta activa', 'alertas activas')} "
             f"({_n(alt, 'alta')})"
             for n, (r, a, alt, p) in enumerate(filas, 1)]
    return "Programas ordenados de mayor a menor riesgo:\n" + "\n".join(items)


def r_visita(ctx):
    progs = [ctx.programa] if ctx.programa else ctx.programas
    con = sorted([p for p in progs if p.fecha_visita_pares], key=lambda p: p.fecha_visita_pares)
    sin = [p for p in progs if not p.fecha_visita_pares]
    if not con:
        return f"No hay visitas de pares programadas{ctx.alcance}."
    items = []
    for p in con:
        d = p.dias_para_visita
        cuando = f"faltan {d} días" if d >= 0 else f"fue hace {-d} días"
        al = Alerta.objects.filter(programa=p, estado="ACTIVA", severidad="ALTA").count()
        altas = _n(al, "alerta alta activa", "alertas altas activas")
        items.append(f"**{p.nombre}**: {_fecha(p.fecha_visita_pares)} ({cuando}); {altas}")
    texto = "Visitas de pares programadas:\n" + _lista(items)
    if sin and not ctx.programa:
        texto += "\nSin fecha registrada: " + ", ".join(p.nombre_corto for p in sin) + "."
    return texto


def r_fuera(ctx):
    return ("Esa pregunta está fuera de lo que sé hacer. La IA Beta solo responde sobre el seguimiento del SIAC: "
            "alertas, indicadores, evidencias, vencimientos, planes, predicciones y visitas de pares. Para preguntas "
            "abiertas puedes cambiar a la **IA estándar**.")


RESPUESTAS = {
    "saludo_ayuda": r_saludo, "alertas": r_alertas, "indicadores_riesgo": r_indicadores_riesgo,
    "evidencias": r_evidencias, "vencimientos": r_vencimientos, "resumen_programa": r_resumen,
    "planes": r_planes, "prediccion": r_prediccion, "ranking_programas": r_ranking,
    "visita_pares": r_visita, "fuera_alcance": r_fuera,
}

SUGERENCIAS = {
    "alertas": "¿Qué alertas altas hay?", "indicadores_riesgo": "¿Qué indicadores están en riesgo?",
    "evidencias": "¿Qué evidencias faltan por cargar?", "vencimientos": "¿Qué vence esta semana?",
    "resumen_programa": "Dame un resumen del programa", "planes": "¿Qué planes están retrasados?",
    "prediccion": "¿Qué indicadores podrían incumplir el próximo periodo?",
    "ranking_programas": "¿Qué programa tiene más riesgo?", "visita_pares": "¿Cuándo es la visita de pares?",
}


# ----------------------------------------------------------------------------- punto de entrada
def responder(user, texto: str, programa_id=None) -> dict:
    visibles_qs = filtrar_por_programa(Programa.objects.filter(activo=True), user, "id").order_by("nombre")
    visibles = list(visibles_qs)
    ctx = Contexto(user=user, texto=texto, programas=visibles, horizonte=detectar_horizonte(texto))
    if programa_id:
        elegido = next((p for p in visibles if str(p.id) == str(programa_id)), None)
        if elegido:
            ctx.programa, ctx.programas = elegido, [elegido]
    aviso = ""
    detectado = detectar_programa(texto, Programa.objects.filter(activo=True))
    if detectado and detectado.id in {p.id for p in visibles}:
        ctx.programa, ctx.programas = detectado, [detectado]
    elif detectado:
        aviso = (f"_No tienes permiso para consultar {detectado.nombre}; te muestro la información de tus "
                 f"programas._\n\n")

    resultado = intenciones.clasificar(texto)
    if resultado is None:
        sin_modelo = ("La IA Beta aún no está entrenada. Un administrador debe entrenarla desde la página "
                      + "IA Beta (o con el comando entrenar_ia_beta).")
        return {"respuesta": sin_modelo,
                "intencion": None, "confianza": 0.0, "alternativas": [], "programa": None, "entrenada": False}
    intent, conf, top = resultado
    alternativas = [{"intencion": i, "etiqueta": INTENCIONES[i], "confianza": c} for i, c in top]
    if conf < UMBRAL_CONFIANZA:
        opciones = [SUGERENCIAS[i] for i, _ in top if i in SUGERENCIAS]
        opciones += [s for s in SUGERENCIAS.values() if s not in opciones]
        opciones = opciones[:3]
        respuesta = ("No estoy seguro de haber entendido la pregunta (confianza "
                     f"{conf * 100:.0f}%). ¿Quisiste decir alguna de estas?\n" + _lista(opciones))
        return {"respuesta": respuesta, "intencion": intent, "etiqueta": INTENCIONES[intent], "confianza": conf,
                "alternativas": alternativas, "sugerencias": opciones, "programa": None, "entendida": False,
                "entrenada": True}
    respuesta = aviso + RESPUESTAS[intent](ctx)
    return {"respuesta": respuesta, "intencion": intent, "etiqueta": INTENCIONES[intent], "confianza": conf,
            "alternativas": alternativas, "sugerencias": [],
            "programa": ctx.programa.nombre if ctx.programa else None,
            "programa_id": ctx.programa.id if ctx.programa else None, "entendida": True, "entrenada": True}
