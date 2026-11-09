"""Construcción de reportes (contenido) y su exportación a PDF y Excel."""
from dataclasses import dataclass, field
from io import BytesIO

from django.utils import timezone

from apps.alertas.models import Alerta, PuntajeRiesgo
from apps.planes.models import PlanMejoramiento

from .datos import Filtros, estado_en_periodo, indicadores, tablero, valor_en_periodo

ESTADOS = {"AL_DIA": "Al día", "ALERTA": "Alerta", "EN_RIESGO": "En riesgo", "DESACTUALIZADO": "Desactualizado",
           "SIN_DATOS": "Sin datos"}
TENDENCIAS = {"MEJORA": "↑ Mejora", "ESTABLE": "→ Estable", "DETERIORO": "↓ Deterioro", "SIN_DATOS": "—"}

TIPOS = {
    "estado_programa": {
        "titulo": "Estado del programa",
        "descripcion": "Resumen ejecutivo del estado actual de indicadores, evidencias y alertas por programa académico.",
    },
    "visita_pares": {
        "titulo": "Preparación visita de pares",
        "descripcion": "Informe consolidado para preparar la visita de pares evaluadores del CNA: brechas, evidencias y planes.",
    },
    "seguimiento_indicadores": {
        "titulo": "Seguimiento de indicadores",
        "descripcion": "Comportamiento de cada indicador en el periodo, con cumplimiento, tendencia e índice de riesgo.",
    },
    "planes": {
        "titulo": "Planes de mejoramiento",
        "descripcion": "Estado de avance de las acciones de mejoramiento activas por programa y responsable.",
    },
}


@dataclass
class Seccion:
    titulo: str
    columnas: list = field(default_factory=list)
    filas: list = field(default_factory=list)
    texto: str = ""


@dataclass
class Reporte:
    tipo: str
    titulo: str
    subtitulo: str
    secciones: list
    generado: str = ""


def _num(v, ind=None):
    if v is None:
        return "—"
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{s}%" if ind is not None and ind.unidad == "PORCENTAJE" else s


def _meta(ind):
    return f"{'≥' if ind.tipo_meta == 'MAYOR_IGUAL' else '≤'} {_num(ind.meta, ind)}"


def _filas_indicadores(inds, periodo, puntajes):
    filas = []
    for i in inds:
        v = valor_en_periodo(i, periodo)
        c = i.cumplimiento(v) if v is not None else None
        p = puntajes.get(i.id)
        filas.append([
            i.codigo, i.nombre, i.programa.nombre_corto, i.factor.nombre, _meta(i), _num(v, i),
            f"{c:.0f}%" if c is not None else "—", TENDENCIAS.get(i.tendencia(), "—"),
            ESTADOS.get(estado_en_periodo(i, periodo), "—"),
            i.responsable.get_full_name() if i.responsable else "—",
            f"{p.puntaje:.0f} ({p.nivel.title()})" if p else "—",
        ])
    return filas


COLS_IND = ["Código", "Indicador", "Programa", "Factor", "Meta", "Valor", "Cumpl.", "Tendencia", "Estado",
            "Responsable", "Riesgo"]
COLS_PLAN = ["Código", "Acción", "Programa", "Indicador", "Responsable", "Inicio", "Límite", "Avance", "Estado"]
ESTADO_PLAN = {"PROGRAMADO": "Programado", "EN_EJECUCION": "En ejecución", "POR_CERRAR": "Por cerrar",
               "CERRADO": "Cerrado", "CANCELADO": "Cancelado", "RETRASADO": "Retrasado"}


def _filas_planes(planes):
    return [[p.codigo, p.accion, p.programa.nombre_corto, p.indicador.codigo if p.indicador else "—",
             p.responsable.get_full_name() if p.responsable else "—", f"{p.fecha_inicio:%Y-%m-%d}",
             f"{p.fecha_limite:%Y-%m-%d}", f"{p.avance}%", ESTADO_PLAN.get(p.estado_visible, p.estado_visible)]
            for p in planes]


def construir(tipo: str, filtros: Filtros, resumen_ia: str | None = None) -> Reporte:
    if tipo not in TIPOS:
        raise ValueError("Tipo de reporte no válido")
    progs = filtros.programas()
    periodo = filtros.periodo_obj()
    inds = indicadores(filtros, progs)
    puntajes = {p.indicador_id: p for p in PuntajeRiesgo.objects.filter(indicador__in=[i.id for i in inds])}
    t = tablero(filtros)
    k = t["kpis"]
    alcance = progs[0].nombre if len(progs) == 1 else "Todos los programas"
    sub = f"{alcance} · Periodo {t['periodo'] or '—'}"
    secs: list[Seccion] = []
    if resumen_ia:
        secs.append(Seccion("Resumen ejecutivo (generado con IA y revisado por el usuario)", texto=resumen_ia))

    planes_qs = PlanMejoramiento.objects.filter(programa__in=progs).select_related("programa", "indicador", "responsable")
    if filtros.responsable:
        planes_qs = planes_qs.filter(responsable_id=filtros.responsable)

    if tipo == "estado_programa":
        secs.append(Seccion("Indicadores clave", ["Indicador clave", "Valor"], [
            ["Índice de riesgo promedio", f"{k['riesgo_promedio']} / 100 ({(k['nivel_riesgo'] or '—').title()})"],
            ["Indicadores al día", f"{k['indicadores_al_dia']} de {k['indicadores_total']} ({k['porcentaje_al_dia']}%)"],
            ["Indicadores desactualizados", k["indicadores_desactualizados"]],
            ["Evidencias pendientes de revisión", k["evidencias_pendientes_revision"]],
            ["Evidencias por cargar / vencidas", f"{k['evidencias_por_cargar']} / {k['evidencias_vencidas']}"],
            ["Acciones de mejoramiento abiertas / retrasadas", f"{k['acciones_abiertas']} / {k['acciones_retrasadas']}"],
            ["Alertas activas (altas)", f"{k['alertas_activas']} ({k['alertas_altas']})"],
        ]))
        secs.append(Seccion("Índice de riesgo por programa",
                            ["Programa", "Estado institucional", "Riesgo", "Nivel", "Al día"],
                            [[r["programa"], r["estado"], r["riesgo"] if r["riesgo"] is not None else "—",
                              (r["nivel"] or "—").title(), f"{r['al_dia']}/{r['indicadores']}"]
                             for r in t["riesgo_programas"]]))
        secs.append(Seccion("Alertas activas", ["Severidad", "Tipo", "Detalle", "Programa", "Responsable"],
                            [[a["severidad"].title(), a["tipo"], a["titulo"], a["programa"], a["responsable"] or "—"]
                             for a in t["ultimas_alertas"]]))
        secs.append(Seccion("Indicadores", COLS_IND, _filas_indicadores(inds, periodo, puntajes)))

    elif tipo == "visita_pares":
        for p in progs:
            p_inds = [i for i in inds if i.programa_id == p.id]
            dias = p.dias_para_visita
            visita = f"{p.fecha_visita_pares:%Y-%m-%d} ({dias} días)" if p.fecha_visita_pares else "Sin fecha programada"
            brechas = [i for i in p_inds if estado_en_periodo(i, periodo) != "AL_DIA"]
            secs.append(Seccion(f"{p.nombre} — {p.get_estado_display()}",
                                texto=f"Visita de pares: {visita}. {p.detalle_estado or ''} "
                                      f"Indicadores con brecha: {len(brechas)} de {len(p_inds)}."))
            if brechas:
                secs.append(Seccion("Brechas frente a la meta", COLS_IND, _filas_indicadores(brechas, periodo, puntajes)))
            hoy = timezone.localdate()
            faltantes = [i for i in p_inds if i.requiere_evidencia and not any(e.vigente_en(hoy) for e in i._evidencias_cache)]
            if faltantes:
                secs.append(Seccion("Evidencias faltantes", ["Código", "Indicador", "Responsable", "Fuente de verificación"],
                                    [[i.codigo, i.nombre, i.responsable.get_full_name() if i.responsable else "—",
                                      i.fuente_verificacion or "—"] for i in faltantes]))
            abiertos = [pl for pl in planes_qs if pl.programa_id == p.id and pl.abierto]
            if abiertos:
                secs.append(Seccion("Planes de mejoramiento abiertos", COLS_PLAN, _filas_planes(abiertos)))

    elif tipo == "seguimiento_indicadores":
        secs.append(Seccion("Indicadores", COLS_IND, _filas_indicadores(inds, periodo, puntajes)))
        hist = []
        for i in inds:
            for r in sorted(i._registros_cache, key=lambda r: r.fecha_corte):
                hist.append([i.codigo, i.programa.nombre_corto, r.periodo.nombre if r.periodo_id else "—",
                             f"{r.fecha_corte:%Y-%m-%d}", _num(r.valor, i), f"{i.cumplimiento(r.valor):.0f}%"])
        secs.append(Seccion("Histórico de registros", ["Código", "Programa", "Periodo", "Fecha de corte", "Valor", "Cumpl."], hist))

    elif tipo == "planes":
        planes = list(planes_qs.exclude(estado=PlanMejoramiento.Estado.CANCELADO))
        secs.append(Seccion("Acciones de mejoramiento", COLS_PLAN, _filas_planes(planes)))
        alertas_sin_plan = Alerta.objects.filter(programa__in=progs, estado=Alerta.Estado.ACTIVA,
                                                 severidad=Alerta.Severidad.ALTA, planes__isnull=True)
        secs.append(Seccion("Alertas altas sin plan de mejoramiento", ["Programa", "Alerta"],
                            [[a.programa.nombre_corto, a.titulo] for a in alertas_sin_plan.select_related("programa")]))

    return Reporte(tipo, TIPOS[tipo]["titulo"], sub, secs, timezone.localtime().strftime("%Y-%m-%d %H:%M"))


# ------------------------------------------------------------------ exportación
def a_excel(rep: Reporte) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = rep.titulo[:31]
    azul = PatternFill("solid", fgColor="1F3A68")
    ws.append([f"SIAC Monitor — {rep.titulo}"])
    ws["A1"].font = Font(bold=True, size=14, color="1F3A68")
    ws.append([f"Universidad Cooperativa de Colombia · Campus Pasto · {rep.subtitulo}"])
    ws.append([f"Generado: {rep.generado}"])
    ws.append([])
    anchos: dict[int, int] = {}
    for s in rep.secciones:
        ws.append([s.titulo])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
        if s.texto:
            ws.append([s.texto])
            ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=True, vertical="top")
        if s.columnas:
            ws.append(s.columnas)
            for c in range(1, len(s.columnas) + 1):
                cell = ws.cell(ws.max_row, c)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = azul
            for f in s.filas:
                ws.append(list(f))
                for idx, v in enumerate(f, start=1):
                    anchos[idx] = max(anchos.get(idx, 8), min(len(str(v)) + 2, 60))
            if not s.filas:
                ws.append(["Sin registros"])
        ws.append([])
    for idx, w in anchos.items():
        ws.column_dimensions[get_column_letter(idx)].width = w
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def a_pdf(rep: Reporte) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm, title=f"SIAC Monitor - {rep.titulo}")
    st = getSampleStyleSheet()
    azul = colors.HexColor("#1F3A68")
    h1 = ParagraphStyle("h1", parent=st["Title"], textColor=azul, alignment=TA_LEFT, fontSize=18)
    h2 = ParagraphStyle("h2", parent=st["Heading2"], textColor=azul, spaceBefore=10)
    cel = ParagraphStyle("cel", parent=st["BodyText"], fontSize=7.5, leading=9)
    celh = ParagraphStyle("celh", parent=cel, textColor=colors.white, fontName="Helvetica-Bold")
    body = ParagraphStyle("body", parent=st["BodyText"], fontSize=9.5, leading=13)
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731

    el = [Paragraph(f"SIAC Monitor — {esc(rep.titulo)}", h1),
          Paragraph(f"Universidad Cooperativa de Colombia · Campus Pasto · {esc(rep.subtitulo)}", body),
          Paragraph(f"Generado: {rep.generado}", body), Spacer(1, 8)]
    ancho = landscape(A4)[0] - 3 * cm
    for s in rep.secciones:
        el.append(Paragraph(esc(s.titulo), h2))
        if s.texto:
            for parrafo in s.texto.split("\n"):
                if parrafo.strip():
                    el.append(Paragraph(esc(parrafo), body))
        if s.columnas:
            if not s.filas:
                el.append(Paragraph("Sin registros.", body))
                continue
            data = [[Paragraph(esc(c), celh) for c in s.columnas]]
            data += [[Paragraph(esc(v), cel) for v in f] for f in s.filas]
            pesos = [2.2 if c in ("Indicador", "Acción", "Detalle", "Alerta", "Indicador clave") else 1 for c in s.columnas]
            tot = sum(pesos)
            t = Table(data, colWidths=[ancho * p / tot for p in pesos], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), azul),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FA")]),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D5DCE6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            el.append(t)
    doc.build(el)
    return buf.getvalue()


def a_texto(rep: Reporte, max_filas=40) -> str:
    """Versión en texto plano para dar contexto a la IA."""
    out = [f"{rep.titulo} — {rep.subtitulo}"]
    for s in rep.secciones:
        out.append(f"\n## {s.titulo}")
        if s.texto:
            out.append(s.texto)
        if s.columnas:
            out.append(" | ".join(s.columnas))
            out += [" | ".join(str(v) for v in f) for f in s.filas[:max_filas]]
            if len(s.filas) > max_filas:
                out.append(f"... ({len(s.filas) - max_filas} filas más)")
    return "\n".join(out)


__all__ = ["TIPOS", "construir", "a_excel", "a_pdf", "a_texto"]
