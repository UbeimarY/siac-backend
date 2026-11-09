"""
Cliente del modelo de lenguaje (Anthropic) para el asistente de IA del SIAC.

Principio de diseño: la IA APOYA, no decide. El motor de alertas sigue siendo un sistema de
reglas (numeral 4.2.4); la IA redacta sugerencias y resúmenes que el usuario revisa y aprueba.
"""
from __future__ import annotations

import json
import logging
import re

from django.conf import settings
from rest_framework.exceptions import APIException

from apps.bitacora.servicio import Modulo, registrar

from .models import InteraccionIA

log = logging.getLogger(__name__)

BASE_SISTEMA = (
    "Eres el asistente de IA de SIAC Monitor, la herramienta de seguimiento del Sistema Interno de "
    "Aseguramiento de la Calidad (SIAC) de la Universidad Cooperativa de Colombia, campus Pasto. "
    "Apoyas a coordinadores de calidad, directores de programa y responsables de indicadores en procesos de "
    "autoevaluación, registro calificado y acreditación en alta calidad bajo el Decreto 1330 de 2019, el "
    "Acuerdo CESU 01 de 2025 y los lineamientos del CNA. Respondes siempre en español de Colombia, con tono "
    "profesional y concreto. Usa solo los datos que se te entregan; si un dato no está, dilo, no lo inventes. "
    "Tus respuestas son sugerencias que una persona revisará antes de usarlas."
)


class IANoDisponible(APIException):
    status_code = 503
    default_detail = "El asistente de IA no está configurado (falta ANTHROPIC_API_KEY en el servidor)."
    default_code = "ia_no_disponible"


class IAError(APIException):
    status_code = 502
    default_detail = "El servicio de IA no respondió correctamente. Intente de nuevo en unos minutos."
    default_code = "ia_error"


def disponible() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


_cliente = None


def cliente():
    global _cliente
    if not disponible():
        raise IANoDisponible()
    if _cliente is None:
        import anthropic

        _cliente = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=90, max_retries=2)
    return _cliente


def llamar(request, funcion: str, sistema: str, mensajes: list, programa=None, max_tokens=None,
           resumen_entrada: str = "") -> str:
    """Llama al modelo, registra la interacción y devuelve el texto de respuesta."""
    modelo = settings.IA_MODELO
    inter = InteraccionIA(usuario=request.user, funcion=funcion, programa=programa,
                          entrada=resumen_entrada[:4000], modelo=modelo)
    try:
        resp = cliente().messages.create(
            model=modelo,
            max_tokens=max_tokens or settings.IA_MAX_TOKENS,
            system=f"{BASE_SISTEMA}\n\n{sistema}",
            messages=mensajes,
        )
    except IANoDisponible:
        raise
    except Exception as exc:  # errores de red, cuota, etc.
        log.exception("Error llamando a la IA")
        inter.exito = False
        inter.salida = str(exc)[:2000]
        inter.save()
        raise IAError() from exc
    texto = "".join(getattr(b, "text", "") for b in resp.content).strip()
    inter.salida = texto[:20000]
    inter.tokens_entrada = getattr(resp.usage, "input_tokens", 0) or 0
    inter.tokens_salida = getattr(resp.usage, "output_tokens", 0) or 0
    inter.save()
    registrar(request, Modulo.IA, f"Usó IA: {InteraccionIA.Funcion(funcion).label}", programa=programa,
              detalle={"interaccion": inter.id})
    return texto


def extraer_json(texto: str):
    """Extrae el primer objeto o arreglo JSON de la respuesta del modelo."""
    bloque = re.search(r"```(?:json)?\s*(.*?)```", texto, re.S)
    candidato = bloque.group(1) if bloque else texto
    for abre, cierra in (("{", "}"), ("[", "]")):
        i, j = candidato.find(abre), candidato.rfind(cierra)
        if i != -1 and j > i:
            try:
                return json.loads(candidato[i: j + 1])
            except json.JSONDecodeError:
                continue
    raise IAError("La IA devolvió una respuesta con formato inesperado. Intente de nuevo.")
