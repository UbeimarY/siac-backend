"""Registro de acciones en la bitácora. Solo se escribe; nunca se edita ni se borra desde la API."""
from .models import RegistroBitacora

Modulo = RegistroBitacora.Modulo


def _ip(request):
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")) or None


def registrar(request, modulo, accion, objeto=None, programa=None, detalle=None, usuario=None):
    user = usuario or (getattr(request, "user", None) if request else None)
    if user is not None and not user.is_authenticated:
        user = None
    if programa is None and objeto is not None:
        programa = getattr(objeto, "programa", None)
    return RegistroBitacora.objects.create(
        usuario=user,
        modulo=modulo,
        accion=accion[:255],
        objeto_tipo=objeto.__class__.__name__ if objeto is not None else "",
        objeto_id=str(getattr(objeto, "pk", "") or ""),
        programa=programa if getattr(programa, "pk", None) else None,
        detalle=detalle or {},
        ip=_ip(request),
    )
