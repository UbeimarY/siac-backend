from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("El correo institucional es obligatorio")
        email = self.normalize_email(email).lower()
        extra.setdefault("username", email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("rol", Usuario.Rol.ADMIN)
        return self._create_user(email, password, **extra)


class Usuario(AbstractUser):
    """Usuario del SIAC con los perfiles definidos en el numeral 2.5.5."""

    class Rol(models.TextChoices):
        ADMIN = "ADMIN", "Administrador institucional"
        COORDINADOR = "COORDINADOR", "Coordinador de calidad"
        DIRECTOR = "DIRECTOR", "Director de programa"
        RESPONSABLE = "RESPONSABLE", "Responsable de indicador"
        CONSULTA = "CONSULTA", "Usuario de consulta"

    email = models.EmailField("correo institucional", unique=True)
    rol = models.CharField(max_length=20, choices=Rol.choices, default=Rol.CONSULTA)
    cargo = models.CharField(
        max_length=120, blank=True, help_text="Cargo institucional, p. ej. 'Coordinadora de investigación'"
    )
    programas = models.ManyToManyField(
        "programas.Programa", blank=True, related_name="usuarios",
        help_text="Programas a los que tiene acceso. Administrador y coordinador ven todos.",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UsuarioManager()

    class Meta:
        ordering = ["first_name", "last_name"]
        verbose_name = "usuario"

    def __str__(self):
        return self.get_full_name() or self.email

    @property
    def es_global(self) -> bool:
        """Administrador y coordinador de calidad tienen visión transversal de los seis programas."""
        return self.rol in (self.Rol.ADMIN, self.Rol.COORDINADOR) or self.is_superuser

    @property
    def iniciales(self) -> str:
        partes = [p for p in (self.first_name, self.last_name) if p]
        return "".join(p[0] for p in partes).upper()[:2] or self.email[:2].upper()

    def programas_visibles_ids(self) -> set[int] | None:
        """IDs de programas visibles; None significa 'todos'."""
        if self.es_global:
            return None
        ids = set(self.programas.values_list("id", flat=True))
        if self.rol == self.Rol.RESPONSABLE:
            ids |= set(self.indicadores_a_cargo.values_list("programa_id", flat=True))
        return ids
