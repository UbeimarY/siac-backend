from django.contrib import admin

from .models import Evidencia

admin.site.register(Evidencia, list_display=["codigo", "nombre", "indicador", "estado", "fecha_carga"],
                    list_filter=["estado", "tipo"], search_fields=["codigo", "nombre"])
