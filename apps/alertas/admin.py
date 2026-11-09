from django.contrib import admin

from .models import Alerta, ConfiguracionRiesgo

admin.site.register(Alerta, list_display=["titulo", "tipo", "severidad", "estado", "programa", "fecha_generacion"],
                    list_filter=["estado", "severidad", "tipo", "programa"])
admin.site.register(ConfiguracionRiesgo)
