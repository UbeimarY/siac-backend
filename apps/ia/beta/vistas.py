"""API de la IA Beta: red neuronal propia (asistente y predicción de riesgo), sin servicios externos."""
import logging
import threading

from django.conf import settings
from django.db import connection
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.bitacora.servicio import Modulo, registrar
from apps.ia.models import InteraccionIA, ModeloBeta
from apps.indicadores.models import Indicador
from apps.programas.models import Programa
from apps.usuarios.models import Usuario
from apps.usuarios.permisos import EsGlobal, filtrar_por_programa

from . import almacen, intenciones, riesgo
from . import asistente as asistente_beta

logger = logging.getLogger(__name__)
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
        "entrenando": _estado_entrenamiento["en_curso"],
        "errores_entrenamiento": _estado_entrenamiento["errores"],
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


# Entrenamiento en segundo plano: en servidores con poca CPU tarda más que el límite de una petición.
_bloqueo = threading.Lock()
_estado_entrenamiento = {"en_curso": False, "inicio": None, "errores": {}}


def _entrenar(tipos: list[str], usuario_id: int | None, en_hilo: bool = True):
    """Entrena los modelos pedidos y registra cada versión. Se ejecuta en un hilo aparte."""
    errores = {}
    try:
        usuario = Usuario.objects.filter(pk=usuario_id).first() if usuario_id else None
        for t in tipos:
            try:
                modelo, error = TIPOS[t].entrenar(usuario=usuario)
            except Exception as exc:  # el error queda en el estado para mostrarlo en la página
                logger.exception("Falló el entrenamiento de la IA Beta (%s)", t)
                errores[t] = f"Error inesperado: {exc}"
                continue
            if error:
                errores[t] = error
                continue
            InteraccionIA.objects.create(usuario=usuario, funcion=F.ENTRENAR_BETA, entrada=f"Entrenamiento {t}",
                                         salida=modelo.arquitectura, modelo=f"siac-beta:{t.lower()}-v{modelo.version}")
    finally:
        with _bloqueo:
            _estado_entrenamiento.update(en_curso=False, errores=errores)
        if en_hilo:  # cada hilo abre su propia conexión a la base de datos; se cierra al terminar
            connection.close()


@api_view(["POST"])
@permission_classes([IsAuthenticated, EsGlobal])
def entrenar(request):
    pedido = str(request.data.get("modelo") or "TODOS").upper()
    tipos = list(TIPOS) if pedido == "TODOS" else [pedido]
    if any(t not in TIPOS for t in tipos):
        raise ValidationError({"modelo": "Use RIESGO, INTENCION o TODOS."})
    with _bloqueo:
        if _estado_entrenamiento["en_curso"]:
            return Response({"detail": "Ya hay un entrenamiento en curso. Espere a que termine."}, status=409)
        _estado_entrenamiento.update(en_curso=True, inicio=timezone.now().isoformat(), errores={})
    registrar(request, Modulo.IA, f"Inició el entrenamiento de la IA Beta ({', '.join(tipos)})", detalle={"modelos": tipos})
    if getattr(settings, "IA_BETA_SINCRONO", False):  # pruebas automatizadas
        _entrenar(tipos, request.user.id, en_hilo=False)
    else:
        threading.Thread(target=_entrenar, args=(tipos, request.user.id), daemon=True).start()
    return Response({"en_curso": True, "modelos": tipos}, status=202)
