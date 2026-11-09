from django.contrib import admin

from .models import Indicador, RegistroIndicador


class RegistroInline(admin.TabularInline):
    model = RegistroIndicador
    extra = 0


@admin.register(Indicador)
class IndicadorAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "programa", "factor", "meta", "responsable", "activo"]
    list_filter = ["programa", "factor", "activo"]
    search_fields = ["codigo", "nombre"]
    inlines = [RegistroInline]
