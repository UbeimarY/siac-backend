"""
Carga datos de demostración coherentes con el trabajo de grado:

- Seis programas del campus Pasto con su momento institucional real (introducción del documento):
  Odontología con acreditación renovada (Resolución 004640 de 2023); Derecho, Medicina e Ingeniería
  Industrial en ruta de acreditación multicampus; Enfermería e Ingeniería de Software en consolidación.
- Factores de calidad del numeral 2.4.4 y los cinco perfiles del numeral 2.5.5.
- Indicadores con histórico, evidencias (algunas con archivo PDF), solicitudes vencidas y planes.

Los nombres de personas son ficticios. Uso:
    python manage.py cargar_demo --password "ClaveDemo2026*"
"""
import random
import secrets
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.alertas.models import Alerta, ConfiguracionRiesgo, PuntajeRiesgo
from apps.alertas.motor import recalcular
from apps.bitacora.models import RegistroBitacora
from apps.evidencias.models import Evidencia
from apps.ia.models import InteraccionIA
from apps.indicadores.models import Indicador, RegistroIndicador
from apps.planes.models import PlanMejoramiento
from apps.programas.models import Factor, Periodo, Programa
from apps.usuarios.models import Usuario

R = Usuario.Rol

FACTORES = [
    ("EST", "Estudiantes", "EST"), ("PROF", "Profesores", "PROF"), ("INV", "Investigación", "INV"),
    ("EGR", "Egresados", "EGR"), ("BIEN", "Bienestar", "BIEN"), ("INT", "Internacionalización", "INT"),
    ("RF", "Recursos físicos y tecnológicos", "RF"), ("PS", "Proyección social", "PS"),
]

PROGRAMAS = [
    # codigo, nombre, corto, facultad, estado, detalle, visita, perfil (desempeño), deriva (tendencia)
    ("ODO", "Odontología", "Odontología", "Facultad de Ciencias de la Salud", "ACREDITADO",
     "Acreditación en alta calidad renovada mediante Resolución 004640 de 2023", None, 1.12, 0.01),
    ("MED", "Medicina", "Medicina", "Facultad de Ciencias de la Salud", "RUTA_ACREDITACION",
     "Ruta de acreditación multicampus", date(2027, 3, 15), 1.0, -0.012),
    ("DER", "Derecho", "Derecho", "Facultad de Derecho", "RUTA_ACREDITACION",
     "Ruta de acreditación multicampus", date(2027, 5, 20), 1.03, 0.0),
    ("IIN", "Ingeniería Industrial", "Ing. Industrial", "Facultad de Ingeniería", "RUTA_ACREDITACION",
     "Ruta de acreditación multicampus", date(2027, 9, 10), 1.06, 0.008),
    ("ENF", "Enfermería", "Enfermería", "Facultad de Ciencias de la Salud", "CONSOLIDACION",
     "Programa en etapa de consolidación", None, 0.95, -0.015),
    ("ISW", "Ingeniería de Software", "Ing. Software", "Facultad de Ingeniería", "CONSOLIDACION",
     "Programa en etapa de consolidación", None, 0.99, 0.012),
]

INDICADORES = [
    # codigo, nombre, factor, tipo, meta, unidad, periodicidad, fuente, área responsable
    ("IND-001", "Tasa de retención estudiantil", "EST", "MAYOR_IGUAL", 80, "PORCENTAJE", "SEMESTRAL",
     "Registro académico", "director"),
    ("IND-002", "Tasa de deserción por periodo", "EST", "MENOR_IGUAL", 8, "PORCENTAJE", "SEMESTRAL",
     "SPADIES y registro académico", "director"),
    ("IND-003", "Tasa de graduación oportuna por cohorte", "EST", "MAYOR_IGUAL", 45, "PORCENTAJE", "ANUAL",
     "Registro académico", "director"),
    ("IND-004", "Profesores con formación de posgrado", "PROF", "MAYOR_IGUAL", 70, "PORCENTAJE", "SEMESTRAL",
     "Gestión del talento humano", "director"),
    ("IND-005", "Estudiantes por profesor de tiempo completo", "PROF", "MENOR_IGUAL", 30, "NUMERO", "SEMESTRAL",
     "Gestión del talento humano", "director"),
    ("IND-006", "Proyectos de investigación activos", "INV", "MAYOR_IGUAL", 5, "NUMERO", "ANUAL",
     "Dirección de investigación", "investigacion"),
    ("IND-007", "Productos de nuevo conocimiento", "INV", "MAYOR_IGUAL", 6, "NUMERO", "ANUAL",
     "GrupLAC y CvLAC", "investigacion"),
    ("IND-008", "Empleabilidad de egresados", "EGR", "MAYOR_IGUAL", 75, "PORCENTAJE", "ANUAL",
     "Observatorio Laboral para la Educación", "egresados"),
    ("IND-009", "Satisfacción con servicios de bienestar", "BIEN", "MAYOR_IGUAL", 4.0, "ESCALA", "SEMESTRAL",
     "Encuesta de satisfacción de bienestar", "bienestar"),
    ("IND-010", "Acciones de movilidad académica", "INT", "MAYOR_IGUAL", 10, "NUMERO", "ANUAL",
     "Oficina de relaciones internacionales", "director"),
    ("IND-011", "Aulas y laboratorios con dotación tecnológica", "RF", "MAYOR_IGUAL", 90, "PORCENTAJE", "ANUAL",
     "Planta física y tecnología", "recursos"),
    ("IND-012", "Proyectos de proyección social con la comunidad", "PS", "MAYOR_IGUAL", 3, "NUMERO", "ANUAL",
     "Coordinación de extensión", "director"),
]

PERIODOS = [
    ("2024-1", date(2024, 1, 15), date(2024, 6, 30)), ("2024-2", date(2024, 7, 15), date(2024, 12, 15)),
    ("2025-1", date(2025, 1, 15), date(2025, 6, 30)), ("2025-2", date(2025, 7, 15), date(2025, 12, 15)),
    ("2026-1", date(2026, 1, 15), date(2026, 6, 30)), ("2026-2", date(2026, 7, 15), date(2026, 12, 15)),
]

PERSONAS = [
    # correo, nombre, apellido, rol, cargo, programas (códigos) / "*"
    ("admin.siac@ucc.edu.co", "Administrador", "SIAC", R.ADMIN, "Administrador institucional", "*"),
    ("ana.gomez@ucc.edu.co", "Ana", "Gómez", R.COORDINADOR, "Coordinadora de calidad", "*"),
    ("carlos.rueda@ucc.edu.co", "Carlos", "Rueda", R.DIRECTOR, "Director de programa", ["MED"]),
    ("liliana.torres@ucc.edu.co", "Liliana", "Torres", R.DIRECTOR, "Directora de programa", ["ISW"]),
    ("jorge.medina@ucc.edu.co", "Jorge", "Medina", R.DIRECTOR, "Director de programa", ["ODO"]),
    ("marcela.rios@ucc.edu.co", "Marcela", "Ríos", R.DIRECTOR, "Directora de programa", ["IIN"]),
    ("andres.castillo@ucc.edu.co", "Andrés", "Castillo", R.DIRECTOR, "Director de programa", ["DER"]),
    ("diana.paz@ucc.edu.co", "Diana", "Paz", R.DIRECTOR, "Directora de programa", ["ENF"]),
    ("patricia.leal@ucc.edu.co", "Patricia", "Leal", R.RESPONSABLE, "Coordinadora de investigación", []),
    ("laura.benavides@ucc.edu.co", "Laura", "Benavides", R.RESPONSABLE, "Gestora de egresados", []),
    ("camilo.erazo@ucc.edu.co", "Camilo", "Erazo", R.RESPONSABLE, "Profesional de bienestar", []),
    ("felipe.ortiz@ucc.edu.co", "Felipe", "Ortiz", R.RESPONSABLE, "Coordinador de planta física", []),
    ("consulta.decanatura@ucc.edu.co", "Decanatura", "Consulta", R.CONSULTA, "Usuario de consulta", "*"),
]
AREA_RESPONSABLE = {"investigacion": "patricia.leal@ucc.edu.co", "egresados": "laura.benavides@ucc.edu.co",
                    "bienestar": "camilo.erazo@ucc.edu.co", "recursos": "felipe.ortiz@ucc.edu.co"}

# Situaciones puntuales para que la demostración muestre todos los tipos de alerta.
SIN_ULTIMO_REGISTRO = {("DER", "IND-009"), ("ENF", "IND-004"), ("MED", "IND-001")}
SIN_EVIDENCIA = {("MED", "IND-006"), ("ENF", "IND-007"), ("ISW", "IND-010"), ("DER", "IND-008"), ("MED", "IND-012")}
EVIDENCIA_PENDIENTE = {("ENF", "IND-002"), ("ISW", "IND-001"), ("IIN", "IND-009"), ("DER", "IND-004")}


def pdf_evidencia(titulo, programa, indicador, periodo, valor):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 720, "Universidad Cooperativa de Colombia - Campus Pasto")
    c.setFont("Helvetica", 12)
    c.drawString(72, 698, f"Programa de {programa.nombre}")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, 660, titulo)
    c.setFont("Helvetica", 11)
    y = 630
    for linea in [
        f"Indicador: {indicador.codigo} - {indicador.nombre}",
        f"Periodo reportado: {periodo}",
        f"Fuente de verificación: {indicador.fuente_verificacion}",
        f"Resultado del periodo: {valor}",
        "",
        "Documento de demostración generado para SIAC Monitor.",
        "Los datos son ficticios y se usan únicamente con fines de prueba.",
    ]:
        c.drawString(72, y, linea)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


class Command(BaseCommand):
    help = "Carga datos de demostración del SIAC (programas, usuarios, indicadores, evidencias y planes)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Contraseña para todos los usuarios de demostración")
        parser.add_argument("--reset", action="store_true", help="Borra los datos existentes antes de cargar")

    @transaction.atomic
    def handle(self, *args, **opts):
        if Programa.objects.exists() and not opts["reset"]:
            raise CommandError("Ya existen datos. Use --reset para reemplazarlos.")
        if opts["reset"]:
            for m in (Alerta, PuntajeRiesgo, PlanMejoramiento, Evidencia, RegistroIndicador, Indicador,
                      ConfiguracionRiesgo, RegistroBitacora, InteraccionIA):
                if m is Evidencia:
                    for e in Evidencia.objects.exclude(archivo=""):
                        e.archivo.delete(save=False)
                m.objects.all().delete()
            Usuario.objects.filter(email__endswith="@ucc.edu.co").delete()
            Programa.objects.all().delete()
            Periodo.objects.all().delete()
            Factor.objects.all().delete()

        password = opts["password"] or secrets.token_urlsafe(10)
        hoy = timezone.localdate()

        factores = {}
        for i, (cod, nom, ab) in enumerate(FACTORES):
            factores[cod] = Factor.objects.create(codigo=cod, nombre=nom, abreviatura=ab, orden=i)
        periodos = []
        for nom, ini, fin in PERIODOS:
            periodos.append(Periodo.objects.create(nombre=nom, fecha_inicio=ini, fecha_fin=fin,
                                                   activo=ini <= hoy <= fin + timedelta(days=30)))
        if not Periodo.objects.filter(activo=True).exists():
            periodos[-1].activo = True
            periodos[-1].save()

        programas = {}
        for cod, nom, corto, fac, est, det, visita, *_ in PROGRAMAS:
            programas[cod] = Programa.objects.create(codigo=cod, nombre=nom, nombre_corto=corto, facultad=fac,
                                                     estado=est, detalle_estado=det, fecha_visita_pares=visita)

        usuarios = {}
        for correo, nom, ape, rol, cargo, progs in PERSONAS:
            u = Usuario.objects.create_user(correo, password, first_name=nom, last_name=ape, rol=rol, cargo=cargo,
                                            is_staff=rol == R.ADMIN, is_superuser=rol == R.ADMIN)
            if progs != "*":
                u.programas.set([programas[c] for c in progs])
            usuarios[correo] = u
        directores = {}
        for correo, *_r, progs in PERSONAS:
            u = usuarios[correo]
            if u.rol == R.DIRECTOR:
                for c in progs:
                    programas[c].director = u
                    programas[c].save()
                    directores[c] = u
        for corr in AREA_RESPONSABLE.values():
            usuarios[corr].programas.set(programas.values())

        ConfiguracionRiesgo.global_()
        # Medicina tiene visita cercana: la coordinación le da más peso a la visita de pares.
        ConfiguracionRiesgo.objects.create(
            programa=programas["MED"], peso_tendencia=20, peso_brecha=30, peso_evidencia=20, peso_vencimiento=10,
            peso_visita=20, actualizado_por=usuarios["ana.gomez@ucc.edu.co"],
        )

        # Indicadores con histórico
        n_ev = 0
        for pcod, *_x, perfil, deriva in PROGRAMAS:
            prog = programas[pcod]
            for icod, inom, fcod, tipo, meta, unidad, perio, fuente, area in INDICADORES:
                responsable = usuarios[AREA_RESPONSABLE[area]] if area in AREA_RESPONSABLE else directores[pcod]
                ind = Indicador.objects.create(
                    programa=prog, factor=factores[fcod], codigo=icod, nombre=inom, tipo_meta=tipo,
                    meta=Decimal(str(meta)), unidad=unidad, periodicidad=perio, fuente_verificacion=fuente,
                    responsable=responsable,
                    descripcion=f"{inom} del programa de {prog.nombre}, medido con periodicidad {perio.lower()}.",
                )
                rnd = random.Random(f"{pcod}-{icod}")
                ratio = perfil + rnd.uniform(-0.1, 0.1)
                regs = periodos[:-1] if perio == "SEMESTRAL" else [p for p in periodos[:-1] if p.nombre.endswith("-2")]
                if (pcod, icod) in SIN_ULTIMO_REGISTRO:
                    regs = regs[:-1]
                n = len(regs)
                ultimo_valor = None
                for k, per in enumerate(regs):
                    paso = (k - (n - 1)) * (deriva + rnd.uniform(-0.01, 0.01))
                    r = ratio + paso
                    if tipo == "MAYOR_IGUAL":
                        v = meta * r
                    else:
                        v = meta * (2 - r)
                    if unidad == "PORCENTAJE":
                        v = max(0, min(100, v))
                    elif unidad == "ESCALA":
                        v = max(1, min(5, v))
                    else:
                        v = max(0, round(v)) if meta >= 3 else max(0, v)
                    v = Decimal(str(round(v, 1 if unidad != "NUMERO" else 0)))
                    RegistroIndicador.objects.create(
                        indicador=ind, periodo=per, valor=v, fecha_corte=per.fecha_fin,
                        registrado_por=responsable, observaciones="Carga inicial de datos históricos.",
                    )
                    ultimo_valor = v
                if not regs:
                    continue
                per = regs[-1]
                clave = (pcod, icod)
                if clave in SIN_EVIDENCIA:
                    continue
                vig_desde = per.fecha_inicio
                vig_hasta = per.fecha_fin + timedelta(days=200 if perio == "SEMESTRAL" else 380)
                estado = Evidencia.Estado.PENDIENTE if clave in EVIDENCIA_PENDIENTE else Evidencia.Estado.APROBADA
                titulo = f"Informe {inom.lower()} {per.nombre}"
                ev = Evidencia(
                    indicador=ind, periodo=per, nombre=titulo[:200], tipo=Evidencia.Tipo.ARCHIVO,
                    vigencia_desde=vig_desde, vigencia_hasta=vig_hasta, estado=estado, responsable=responsable,
                    cargado_por=responsable, fecha_carga=timezone.make_aware(
                        timezone.datetime.combine(per.fecha_fin + timedelta(days=10), timezone.datetime.min.time())),
                    nombre_archivo=f"{icod}_{pcod}_{per.nombre}.pdf",
                )
                if estado == Evidencia.Estado.APROBADA:
                    ev.revisado_por = usuarios["ana.gomez@ucc.edu.co"]
                    ev.fecha_revision = ev.fecha_carga + timedelta(days=3)
                ev.archivo.save(f"{icod}_{pcod}.pdf",
                                ContentFile(pdf_evidencia(titulo, prog, ind, per.nombre, ultimo_valor)), save=False)
                ev.save()
                n_ev += 1

        def ind(p, c):
            return Indicador.objects.get(programa=programas[p], codigo=c)

        # Solicitudes de evidencia (una vencida = "reporte vencido")
        sol = [
            ("ENF", "IND-006", "Informe de investigación 2026-1", hoy - timedelta(days=25)),
            ("MED", "IND-008", "Encuesta de seguimiento a egresados 2026", hoy + timedelta(days=20)),
            ("ISW", "IND-012", "Convenios de proyección social 2026-2", hoy - timedelta(days=8)),
            ("DER", "IND-010", "Soportes de movilidad académica 2026", hoy + timedelta(days=45)),
        ]
        for p, c, nombre, limite in sol:
            i = ind(p, c)
            Evidencia.objects.create(indicador=i, periodo=periodos[-1], nombre=nombre, estado=Evidencia.Estado.POR_CARGAR,
                                     fecha_limite=limite, responsable=i.responsable, tipo=Evidencia.Tipo.ARCHIVO)
        # Una evidencia rechazada
        rech = Evidencia.objects.filter(indicador__programa=programas["IIN"], estado=Evidencia.Estado.APROBADA).first()
        if rech:
            rech.estado = Evidencia.Estado.RECHAZADA
            rech.observaciones_revision = "El informe no discrimina los datos por cohorte. Por favor ajustar."
            rech.save()

        # Planes de mejoramiento
        planes = [
            ("ENF", "IND-002", "Implementar tutorías por pares para estudiantes de primer semestre",
             hoy - timedelta(days=70), hoy + timedelta(days=40), 30, "diana.paz@ucc.edu.co"),
            ("ENF", "IND-006", "Vincular dos investigadores externos al grupo del programa",
             hoy - timedelta(days=120), hoy - timedelta(days=10), 40, "patricia.leal@ucc.edu.co"),
            ("MED", "IND-001", "Fortalecer el sistema de alertas académicas con consejería docente",
             hoy - timedelta(days=60), hoy + timedelta(days=90), 35, "carlos.rueda@ucc.edu.co"),
            ("MED", "IND-007", "Plan de escritura científica con apoyo de la biblioteca",
             hoy - timedelta(days=150), hoy + timedelta(days=30), 25, "patricia.leal@ucc.edu.co"),
            ("ISW", "IND-003", "Seminario de opción de grado para estudiantes de último año",
             hoy - timedelta(days=30), hoy + timedelta(days=150), 20, "liliana.torres@ucc.edu.co"),
            ("IIN", "IND-009", "Actualizar la encuesta de satisfacción de bienestar",
             hoy - timedelta(days=50), hoy + timedelta(days=10), 85, "camilo.erazo@ucc.edu.co"),
            ("DER", "IND-008", "Actualizar la base de datos de egresados",
             hoy - timedelta(days=90), hoy + timedelta(days=20), 45, "laura.benavides@ucc.edu.co"),
            ("ODO", "IND-010", "Ampliar convenios de movilidad con universidades de la región andina",
             hoy - timedelta(days=200), hoy - timedelta(days=5), 100, "jorge.medina@ucc.edu.co"),
        ]
        for p, c, accion, ini, fin, avance, resp in planes:
            plan = PlanMejoramiento(programa=programas[p], indicador=ind(p, c), accion=accion, fecha_inicio=ini,
                                    fecha_limite=fin, avance=avance, responsable=usuarios[resp],
                                    creado_por=usuarios["ana.gomez@ucc.edu.co"],
                                    estado=PlanMejoramiento.Estado.EN_EJECUCION)
            plan.save()

        RegistroBitacora.objects.create(modulo=RegistroBitacora.Modulo.CONFIGURACION,
                                        accion="Carga inicial de datos de demostración")
        resumen = recalcular()
        self.stdout.write(self.style.SUCCESS(
            f"Datos de demostración cargados: {Programa.objects.count()} programas, {Indicador.objects.count()} "
            f"indicadores, {n_ev} evidencias con archivo, {PlanMejoramiento.objects.count()} planes. Motor: {resumen}"
        ))
        if not opts["password"]:
            self.stdout.write(self.style.WARNING(f"Contraseña generada para los usuarios de demostración: {password}"))
