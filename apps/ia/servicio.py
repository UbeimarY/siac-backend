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
    default_detail = "El asistente de IA no está configurado (falta IA_API_KEY en el servidor)."
    default_code = "ia_no_disponible"


class IAError(APIException):
    status_code = 502
    default_detail = "El servicio de IA no respondió correctamente. Intente de nuevo en unos minutos."
    default_code = "ia_error"


PRESETS = {
    # proveedor: (URL base compatible con OpenAI, modelo por defecto)
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}


def config() -> dict:
    """Proveedor de IA configurado. IA_PROVEEDOR: anthropic | groq | gemini | openrouter | openai | compatible."""
    prov = settings.IA_PROVEEDOR
    if prov == "anthropic":
        return {"proveedor": prov, "clave": settings.IA_API_KEY or settings.ANTHROPIC_API_KEY,
                "modelo": settings.IA_MODELO or "claude-sonnet-5-5", "base": None}
    base, modelo = PRESETS.get(prov, (None, None))
    return {"proveedor": prov, "clave": settings.IA_API_KEY, "modelo": settings.IA_MODELO or modelo,
            "base": (settings.IA_BASE_URL or base or "").rstrip("/")}


def disponible() -> bool:
    c = config()
    return bool(c["clave"] and c["modelo"] and (c["proveedor"] == "anthropic" or c["base"]))


def modelo_actual() -> str | None:
    return config()["modelo"] if disponible() else None


_cliente = None


def cliente():
    """Cliente de Anthropic (solo para IA_PROVEEDOR=anthropic)."""
    global _cliente
    if not disponible():
        raise IANoDisponible()
    if _cliente is None:
        import anthropic

        _cliente = anthropic.Anthropic(api_key=config()["clave"], timeout=90, max_retries=2)
    return _cliente


def _pdf_a_texto(b64: str) -> str:
    import base64
    from io import BytesIO

    from pypdf import PdfReader

    try:
        lector = PdfReader(BytesIO(base64.b64decode(b64)))
        texto = "\n".join((p.extract_text() or "") for p in lector.pages[:40])
    except Exception:
        texto = ""
    return texto.strip() or "(No se pudo extraer texto del PDF; puede ser un documento escaneado.)"


def _a_formato_openai(sistema: str, mensajes: list) -> list:
    """Convierte mensajes con bloques (documento, imagen) al formato de chat compatible con OpenAI."""
    out = [{"role": "system", "content": sistema}]
    for m in mensajes:
        cont = m["content"]
        if isinstance(cont, str):
            out.append({"role": m["role"], "content": cont})
            continue
        partes = []
        for b in cont:
            if b.get("type") == "text":
                partes.append(b["text"])
            elif b.get("type") == "document":
                partes.append(f"<contenido_archivo>\n{_pdf_a_texto(b['source']['data'])[:60000]}\n</contenido_archivo>")
            elif b.get("type") == "image":
                partes.append("(El archivo es una imagen; este modelo no puede verla. Evalúa con el nombre y la descripción.)")
        out.append({"role": m["role"], "content": "\n\n".join(partes)})
    return out


def _llamar_compatible(c: dict, sistema: str, mensajes: list, max_tokens: int):
    import httpx

    cuerpo = {"model": c["modelo"], "messages": _a_formato_openai(sistema, mensajes), "max_tokens": max_tokens,
              "temperature": 0.3}
    if "gpt-oss" in c["modelo"]:
        # Modelos de razonamiento: razonamiento breve y oculto; el razonamiento también consume tokens.
        cuerpo["max_tokens"] = max_tokens + 1500
        if c["proveedor"] == "groq":
            cuerpo.update({"reasoning_effort": "low", "include_reasoning": False})
    r = httpx.post(
        f"{c['base']}/chat/completions",
        headers={"Authorization": f"Bearer {c['clave']}"},
        json=cuerpo,
        timeout=90,
    )
    if r.status_code >= 400:
        try:
            detalle = r.json().get("error", {}).get("message", "")
        except ValueError:
            detalle = r.text[:300]
        log.error("Proveedor de IA respondió %s: %s", r.status_code, detalle)
        if r.status_code == 429:
            raise IAError("Se alcanzó el límite gratuito del proveedor de IA. Intente de nuevo en unos minutos.")
        if r.status_code in (401, 403):
            raise IAError("La clave del proveedor de IA (IA_API_KEY) no es válida o no tiene permisos.")
        if r.status_code == 413 or "too large" in detalle.lower() or "tokens" in detalle.lower():
            raise IAError("La consulta supera el límite del plan gratuito de IA. Enfoque la pregunta en un programa.")
        if "model" in detalle.lower():
            raise IAError(f"El modelo de IA configurado no está disponible ({c['modelo']}). Revise IA_MODELO.")
        raise IAError(f"El proveedor de IA respondió con error {r.status_code}: {detalle[:200]}")
    d = r.json()
    texto = (d["choices"][0]["message"].get("content") or "").strip()
    if not texto:
        raise IAError("El modelo de IA no devolvió texto. Intente de nuevo con una pregunta más concreta.")
    uso = d.get("usage") or {}
    return texto, uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0)


def llamar(request, funcion: str, sistema: str, mensajes: list, programa=None, max_tokens=None,
           resumen_entrada: str = "") -> str:
    """Llama al modelo configurado, registra la interacción y devuelve el texto de respuesta."""
    if not disponible():
        raise IANoDisponible()
    c = config()
    inter = InteraccionIA(usuario=request.user, funcion=funcion, programa=programa,
                          entrada=resumen_entrada[:4000], modelo=f"{c['proveedor']}:{c['modelo']}"[:60])
    sistema_total = f"{BASE_SISTEMA}\n\n{sistema}"
    tokens = max_tokens or settings.IA_MAX_TOKENS
    try:
        if c["proveedor"] == "anthropic":
            resp = cliente().messages.create(model=c["modelo"], max_tokens=tokens, system=sistema_total, messages=mensajes)
            texto = "".join(getattr(b, "text", "") for b in resp.content).strip()
            t_in, t_out = getattr(resp.usage, "input_tokens", 0), getattr(resp.usage, "output_tokens", 0)
        else:
            texto, t_in, t_out = _llamar_compatible(c, sistema_total, mensajes, tokens)
    except (IANoDisponible, IAError) as exc:
        inter.exito = False
        inter.salida = str(exc.detail)[:2000]
        inter.save()
        raise
    except Exception as exc:  # errores de red, cuota, etc.
        log.exception("Error llamando a la IA")
        inter.exito = False
        inter.salida = str(exc)[:2000]
        inter.save()
        raise IAError() from exc
    inter.salida = texto[:20000]
    inter.tokens_entrada = t_in or 0
    inter.tokens_salida = t_out or 0
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
