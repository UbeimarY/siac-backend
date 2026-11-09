from django.contrib import admin

from .models import RegistroBitacora


@admin.register(RegistroBitacora)
class BitacoraAdmin(admin.ModelAdmin):
    list_display = ["fecha", "usuario", "modulo", "accion"]
    list_filter = ["modulo"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
