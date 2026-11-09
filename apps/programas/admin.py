from django.contrib import admin

from .models import Factor, Periodo, Programa

admin.site.register(Factor, list_display=["codigo", "nombre", "abreviatura", "orden", "activo"])
admin.site.register(Periodo, list_display=["nombre", "fecha_inicio", "fecha_fin", "activo"])
admin.site.register(Programa, list_display=["codigo", "nombre", "estado", "fecha_visita_pares", "activo"])
