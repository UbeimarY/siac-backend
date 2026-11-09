from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Usuario


class UsuarioSerializer(serializers.ModelSerializer):
    nombre = serializers.CharField(source="get_full_name", read_only=True)
    rol_display = serializers.CharField(source="get_rol_display", read_only=True)
    iniciales = serializers.CharField(read_only=True)
    es_global = serializers.BooleanField(read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    programas_nombres = serializers.SerializerMethodField()

    class Meta:
        model = Usuario
        fields = [
            "id", "email", "first_name", "last_name", "nombre", "iniciales", "rol", "rol_display", "cargo",
            "programas", "programas_nombres", "is_active", "es_global", "last_login", "date_joined", "password",
        ]
        read_only_fields = ["last_login", "date_joined"]

    def get_programas_nombres(self, obj):
        if obj.es_global:
            return ["Todos"]
        return [p.nombre_corto for p in obj.programas.all()]

    def validate_email(self, value):
        value = value.lower().strip()
        qs = Usuario.objects.filter(email=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya existe un usuario con este correo.")
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": "La contraseña es obligatoria para usuarios nuevos."})
        if attrs.get("password"):
            validate_password(attrs["password"])
        return attrs

    def create(self, validated):
        programas = validated.pop("programas", [])
        password = validated.pop("password")
        user = Usuario.objects.create_user(password=password, **validated)
        user.programas.set(programas)
        return user

    def update(self, instance, validated):
        password = validated.pop("password", None)
        programas = validated.pop("programas", None)
        for k, v in validated.items():
            setattr(instance, k, v)
        if "email" in validated:
            instance.username = validated["email"]
        if password:
            instance.set_password(password)
        instance.save()
        if programas is not None:
            instance.programas.set(programas)
        return instance


class UsuarioMinSerializer(serializers.ModelSerializer):
    nombre = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = Usuario
        fields = ["id", "nombre", "email", "iniciales", "rol", "cargo"]


class LoginSerializer(TokenObtainPairSerializer):
    username_field = "email"

    def validate(self, attrs):
        attrs["email"] = attrs.get("email", "").lower().strip()
        data = super().validate(attrs)
        data["usuario"] = UsuarioSerializer(self.user).data
        return data


class CambioPasswordSerializer(serializers.Serializer):
    actual = serializers.CharField()
    nueva = serializers.CharField()

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["actual"]):
            raise serializers.ValidationError({"actual": "La contraseña actual no es correcta."})
        validate_password(attrs["nueva"], user)
        return attrs
