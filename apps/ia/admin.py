from django.contrib import admin

from .models import InteraccionIA

admin.site.register(InteraccionIA, list_display=["fecha", "usuario", "funcion", "modelo", "tokens_entrada", "tokens_salida", "exito"],
                    list_filter=["funcion", "exito"])
