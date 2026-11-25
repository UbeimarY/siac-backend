"""API de la IA Beta: red neuronal propia (asistente y predicción de riesgo), sin servicios externos."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.bitacora.servicio import Modulo, registrar
from apps.ia.models import InteraccionIA, ModeloBeta
from apps.indicadores.models import Indicador
from apps.programas.models import Programa
from apps.usuarios.permisos import EsGlobal, filtrar_por_programa

from . import almacen, intenciones, riesgo
from . import asistente as asistente_beta

F = InteraccionIA.Funcion
TIPOS = {"RIESGO": riesgo, "INTENCION": intenciones}


def _modelo(m: ModeloBeta | None, con_historial: bool):
    if m is None:
        return None
    d = {
        "id": m.id, "tipo": m.tipo, "version": m.version, "arquitectura": m.arquitectura,
        "parametros": m.parametros, "muestras_entrenamiento": m.muestras_entrenamiento,
        "muestras_validacion": m.muestras_validacion, "metricas": m.metricas, "entrenado": m.entrenado,
        "entrenado_por": m.entrenado_por.get_full_name() if m.entrenado_por else "Sistema",
    }
    if con_historial:
        d["historial"] = m.historial
    return d


@api_view(["GET"])
def estado(request):
    global_ = request.user.es_global
    return Response({
        "riesgo": _modelo(almacen.activo("RIESGO"), global_),
        "intencion": _modelo(almacen.activo("INTENCION"), global_),
        "intenciones": intenciones.INTENCIONES,
        "caracteristicas": riesgo.CARACTERISTICAS,
    })


@api_view(["POST"])
def asistente(request):
    mensaje = request.data.get("mensaje")
    if not mensaje:
        mensajes = request.data.get("mensajes") or []
        usuario_msgs = [m for m in mensajes if isinstance(m, dict) and m.get("role") == "user"]
        mensaje = usuario_msgs[-1].get("content") if usuario_msgs else ""
    mensaje = str(mensaje or "").strip()[:1000]
    if not mensaje:
        raise ValidationError({"mensaje": "Escriba una pregunta."})
    r = asistente_beta.responder(request.user, mensaje, request.data.get("programa"))
    m = almacen.activo("INTENCION")
    programa = Programa.objects.filter(pk=r.get("programa_id")).first() if r.get("programa_id") else None
    inter = InteraccionIA.objects.create(
        usuario=request.user, funcion=F.ASISTENTE_BETA, programa=programa, entrada=mensaje,
        salida=r["respuesta"][:20000], modelo=f"siac-beta:intencion-v{m.version}" if m else "siac-beta",
        exito=r.get("entrenada", False),
    )
    registrar(request, Modulo.IA, "Usó IA Beta: asistente propio", programa=programa,
              detalle={"interaccion": inter.id, "intencion": r.get("intencion"), "confianza": r.get("confianza")})
    return Response(r)


@api_view(["GET"])
def predicciones(request):
    qs = filtrar_por_programa(Indicador.objects.filter(activo=True).select_related("programa"), request.user)
    if request.query_params.get("programa"):
        qs = qs.filter(programa_id=request.query_params["programa"])
    if request.query_params.get("indicador"):
        qs = qs.filter(pk=request.query_params["indicador"])
    inds = list(qs)
    preds = riesgo.predecir(inds)
    m = almacen.activo("RIESGO")
    por_id = {i.id: i for i in inds}
    filas = [{
        "indicador": p.indicador_id, "codigo": por_id[p.indicador_id].codigo,
        "nombre": por_id[p.indicador_id].nombre, "programa": por_id[p.indicador_id].programa.nombre_corto,
        "probabilidad": round(p.probabilidad, 4), "nivel": p.nivel, "factores": p.factores,
    } for p in sorted(preds.values(), key=lambda x: -x.probabilidad)]
    return Response({"modelo": {"version": m.version, "entrenado": m.entrenado} if m else None,
                     "resultados": filas})


@api_view(["POST"])
@permission_classes([IsAuthenticated, EsGlobal])
def entrenar(request):
    pedido = str(request.data.get("modelo") or "TODOS").upper()
    tipos = list(TIPOS) if pedido == "TODOS" else [pedido]
    if any(t not in TIPOS for t in tipos):
        raise ValidationError({"modelo": "Use RIESGO, INTENCION o TODOS."})
    salida, errores = {}, {}
    for t in tipos:
        modelo, error = TIPOS[t].entrenar(usuario=request.user)
        if error:
            errores[t] = error
        else:
            salida[t.lower()] = _modelo(modelo, True)
            InteraccionIA.objects.create(usuario=request.user, funcion=F.ENTRENAR_BETA,
                                         entrada=f"Entrenamiento {t}", salida=modelo.arquitectura,
                                         modelo=f"siac-beta:{t.lower()}-v{modelo.version}")
    registrar(request, Modulo.IA, f"Entrenó la IA Beta ({', '.join(tipos)})",
              detalle={"versiones": {k: v["version"] for k, v in salida.items()}, "errores": errores})
    return Response({"modelos": salida, "errores": errores}, status=200 if salida else 400)
