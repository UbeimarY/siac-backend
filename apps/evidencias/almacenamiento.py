"""Almacenamiento de archivos de evidencias dentro de PostgreSQL.

Se usa en plataformas sin disco persistente (p. ej. el plan gratuito de Render): los archivos se
guardan como binarios en la base de datos, así no se pierden en cada reinicio. Para volúmenes
grandes conviene un almacenamiento de objetos (USE_S3=true) o un disco persistente."""
import os

from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class AlmacenamientoBD(Storage):
    def _modelo(self):
        from .models import ArchivoBD

        return ArchivoBD

    def _open(self, name, mode="rb"):
        obj = self._modelo().objects.get(nombre=name)
        return ContentFile(bytes(obj.contenido), name=name)

    def _save(self, name, content):
        name = self.get_available_name(name)
        content.seek(0) if hasattr(content, "seek") else None
        datos = content.read()
        self._modelo().objects.create(nombre=name, contenido=datos, tamano=len(datos))
        return name

    def exists(self, name):
        return self._modelo().objects.filter(nombre=name).exists()

    def delete(self, name):
        self._modelo().objects.filter(nombre=name).delete()

    def size(self, name):
        return self._modelo().objects.values_list("tamano", flat=True).get(nombre=name)

    def url(self, name):
        return None

    def path(self, name):
        raise NotImplementedError("Los archivos se guardan en la base de datos.")

    def get_available_name(self, name, max_length=None):
        base, ext = os.path.splitext(name)
        n = 1
        while self.exists(name):
            name = f"{base}_{n}{ext}"
            n += 1
        return name
