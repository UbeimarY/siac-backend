from django.core.management.base import BaseCommand

from apps.alertas.motor import recalcular


class Command(BaseCommand):
    help = "Recalcula los índices de riesgo y sincroniza las alertas tempranas (programar a diario con cron)."

    def handle(self, *args, **options):
        resumen = recalcular()
        self.stdout.write(self.style.SUCCESS(f"Motor de alertas ejecutado: {resumen}"))
