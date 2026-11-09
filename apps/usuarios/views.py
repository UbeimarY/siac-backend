from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.bitacora.servicio import Modulo, registrar

from .models import Usuario
from .permisos import EsGlobal
from .serializers import CambioPasswordSerializer, LoginSerializer, UsuarioMinSerializer, UsuarioSerializer


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        resp = super().post(request, *args, **kwargs)
        if resp.status_code == 200:
            user = Usuario.objects.get(email=request.data.get("email", "").lower().strip())
            registrar(request, Modulo.AUTENTICACION, "Inició sesión", usuario=user)
        return resp


@api_view(["POST"])
def logout(request):
    """Cierre de sesión: invalida el refresh token (numeral 2.5.5)."""
    try:
        RefreshToken(request.data.get("refresh", "")).blacklist()
    except TokenError:
        pass
    registrar(request, Modulo.AUTENTICACION, "Cerró sesión")
    return Response(status=status.HTTP_205_RESET_CONTENT)


@api_view(["GET"])
def me(request):
    return Response(UsuarioSerializer(request.user).data)


@api_view(["POST"])
def cambiar_password(request):
    ser = CambioPasswordSerializer(data=request.data, context={"request": request})
    ser.is_valid(raise_exception=True)
    request.user.set_password(ser.validated_data["nueva"])
    request.user.save()
    registrar(request, Modulo.USUARIOS, "Cambió su contraseña", objeto=request.user)
    return Response({"detail": "Contraseña actualizada."})


class UsuarioViewSet(viewsets.ModelViewSet):
    queryset = Usuario.objects.prefetch_related("programas").order_by("first_name", "last_name")
    serializer_class = UsuarioSerializer
    search_fields = ["first_name", "last_name", "email", "cargo"]
    filterset_fields = ["rol", "is_active"]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "opciones"):
            return [permissions.IsAuthenticated()]
        return [EsGlobal()]

    def get_serializer_class(self):
        if self.action in ("list", "retrieve") and not self.request.user.es_global:
            return UsuarioMinSerializer
        return UsuarioSerializer

    def perform_create(self, serializer):
        u = serializer.save()
        registrar(self.request, Modulo.USUARIOS, f"Creó usuario {u.get_full_name()}", objeto=u)

    def perform_update(self, serializer):
        u = serializer.save()
        registrar(self.request, Modulo.USUARIOS, f"Actualizó usuario {u.get_full_name()}", objeto=u)

    def destroy(self, request, *args, **kwargs):
        # No se eliminan usuarios: se desactivan para preservar la trazabilidad.
        u = self.get_object()
        if u == request.user:
            return Response({"detail": "No puede desactivar su propio usuario."}, status=400)
        u.is_active = False
        u.save(update_fields=["is_active"])
        registrar(request, Modulo.USUARIOS, f"Desactivó usuario {u.get_full_name()}", objeto=u)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def activar(self, request, pk=None):
        u = self.get_object()
        u.is_active = not u.is_active
        if u == request.user and not u.is_active:
            return Response({"detail": "No puede desactivar su propio usuario."}, status=400)
        u.save(update_fields=["is_active"])
        registrar(request, Modulo.USUARIOS, f"{'Activó' if u.is_active else 'Desactivó'} usuario {u.get_full_name()}", objeto=u)
        return Response(UsuarioSerializer(u).data)

    @action(detail=False, methods=["get"])
    def opciones(self, request):
        """Lista mínima para selects (responsables)."""
        qs = Usuario.objects.filter(is_active=True).exclude(rol=Usuario.Rol.CONSULTA)
        return Response(UsuarioMinSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def roles(self, request):
        return Response([{"valor": v, "nombre": n} for v, n in Usuario.Rol.choices])
