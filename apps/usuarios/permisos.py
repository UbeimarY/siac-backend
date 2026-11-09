"""Permisos por rol (numeral 2.5.5: cada usuario consulta o modifica solo lo necesario)."""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Usuario

R = Usuario.Rol


def tiene_rol(user, *roles) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.rol in roles))


class EsGlobal(BasePermission):
    """Administrador o coordinador de calidad."""

    message = "Solo el administrador o la coordinación de calidad pueden realizar esta acción."

    def has_permission(self, request, view):
        return tiene_rol(request.user, R.ADMIN, R.COORDINADOR)


class LecturaOGlobal(BasePermission):
    """Todos leen; solo administrador/coordinador escriben."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return tiene_rol(request.user, R.ADMIN, R.COORDINADOR)


class NoConsulta(BasePermission):
    """El usuario de consulta es de solo lectura en todo el sistema."""

    message = "Su perfil es de solo consulta."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return request.user.is_authenticated and request.user.rol != R.CONSULTA


def filtrar_por_programa(qs, user, campo="programa_id"):
    ids = user.programas_visibles_ids()
    if ids is None:
        return qs
    return qs.filter(**{f"{campo}__in": ids})


def puede_editar_programa(user, programa_id) -> bool:
    if user.es_global:
        return True
    if user.rol == R.DIRECTOR:
        return user.programas.filter(id=programa_id).exists()
    return False
