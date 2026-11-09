from django.contrib import admin

from .models import PlanMejoramiento

admin.site.register(PlanMejoramiento, list_display=["codigo", "accion", "programa", "avance", "estado", "fecha_limite"],
                    list_filter=["estado", "programa"])
