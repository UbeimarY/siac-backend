from django.core.management.base import BaseCommand

from apps.ia.beta import almacen, intenciones, riesgo


class Command(BaseCommand):
    help = "Entrena los modelos de la IA Beta (red neuronal propia): intenciones del asistente y riesgo."

    def add_arguments(self, parser):
        parser.add_argument("--si-falta", action="store_true",
                            help="Solo entrena los modelos que todavía no tienen una versión activa.")

    def handle(self, *args, si_falta=False, **opts):
        for tipo, modulo in (("INTENCION", intenciones), ("RIESGO", riesgo)):
            if si_falta and almacen.activo(tipo):
                self.stdout.write(f"{tipo}: ya existe la versión {almacen.activo(tipo).version}; se omite.")
                continue
            modelo, error = modulo.entrenar()
            if error:
                self.stdout.write(self.style.WARNING(f"{tipo}: {error}"))
                continue
            val = modelo.metricas.get("validacion", {})
            self.stdout.write(self.style.SUCCESS(
                f"{tipo} v{modelo.version}: {modelo.arquitectura}, {modelo.parametros} parámetros, "
                f"exactitud de validación {val.get('exactitud')}"))
