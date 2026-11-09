from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.bitacora.servicio import Modulo, registrar
from apps.programas.models import Programa

from . import generadores
from .datos import Filtros, tablero


@api_view(["GET"])
def tablero_control(request):
    """Tablero de control (numeral 2.4.8) con filtros por programa, periodo, responsable y factor."""
    return Response(tablero(Filtros(request.user, request.query_params)))


@api_view(["GET"])
def tipos(request):
    return Response([{"tipo": k, **v} for k, v in generadores.TIPOS.items()])


@api_view(["GET", "POST"])
def generar(request, tipo):
    """GET/POST /reportes/<tipo>/?formato=pdf|xlsx&programa=&periodo=&responsable=&factor=
    En POST se puede enviar `resumen_ia` (texto ya revisado por el usuario) para incluirlo."""
    if tipo not in generadores.TIPOS:
        raise ValidationError("Tipo de reporte no válido.")
    params = request.query_params
    formato = params.get("formato", "pdf").lower()
    if formato not in ("pdf", "xlsx", "json"):
        raise ValidationError("Formato no válido (pdf, xlsx o json).")
    filtros = Filtros(request.user, params)
    resumen = (request.data.get("resumen_ia") or "").strip()[:6000] if request.method == "POST" else None
    rep = generadores.construir(tipo, filtros, resumen or None)
    prog = Programa.objects.filter(id=filtros.programa).first() if filtros.programa else None
    registrar(request, Modulo.REPORTES, f"Exportó reporte '{rep.titulo}' ({formato.upper()})"
              + (f" de {prog.nombre_corto}" if prog else ""), programa=prog, detalle=filtros.como_dict())
    if formato == "json":
        return Response({"titulo": rep.titulo, "subtitulo": rep.subtitulo, "generado": rep.generado,
                         "secciones": [s.__dict__ for s in rep.secciones]})
    nombre = f"siac_{tipo}_{rep.generado[:10]}"
    if formato == "xlsx":
        resp = HttpResponse(generadores.a_excel(rep),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{nombre}.xlsx"'
        return resp
    resp = HttpResponse(generadores.a_pdf(rep), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{nombre}.pdf"'
    return resp
