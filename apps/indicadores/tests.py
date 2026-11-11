from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook, load_workbook
from rest_framework.test import APIClient

from apps.alertas.models import Alerta
from apps.planes.models import PlanMejoramiento
from apps.programas.models import Factor, Periodo, Programa
from apps.usuarios.models import Usuario

from .importacion import normalizar_periodo
from .models import Indicador, RegistroIndicador


def libro(hojas: dict[str, list[list]]) -> SimpleUploadedFile:
    wb = Workbook()
    wb.remove(wb.active)
    for nombre, filas in hojas.items():
        ws = wb.create_sheet(nombre)
        for f in filas:
            ws.append(f)
    buf = BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("datos.xlsx", buf.getvalue())


ENC = ["programa", "factor", "codigo", "nombre", "descripcion", "tipo_meta", "meta", "unidad", "periodicidad",
       "fuente", "responsable"]


class ImportacionTests(TestCase):
    def setUp(self):
        self.fac = Factor.objects.create(codigo="EST", nombre="Estudiantes", abreviatura="EST")
        self.odo = Programa.objects.create(codigo="ODO", nombre="Odontología", nombre_corto="Odontología")
        self.otro = Programa.objects.create(codigo="MED", nombre="Medicina", nombre_corto="Medicina")
        self.coord = Usuario.objects.create_user("coord@ucc.edu.co", "Clave-segura-123", rol="COORDINADOR")
        self.resp = Usuario.objects.create_user("resp@ucc.edu.co", "Clave-segura-123", rol="RESPONSABLE")
        self.api = APIClient()
        self.api.force_authenticate(self.coord)

    def subir(self, archivo, **extra):
        return self.api.post("/api/indicadores/importar/", {"archivo": archivo, **extra}, format="multipart")

    def test_normalizar_periodo(self):
        self.assertEqual(normalizar_periodo("2025-1"), "2025-1")
        self.assertEqual(normalizar_periodo("2025 - II"), "2025-2")
        self.assertEqual(normalizar_periodo(2024), "2024")
        self.assertIsNone(normalizar_periodo("meta"))

    def test_formato_ancho_crea_indicadores_valores_y_periodos(self):
        archivo = libro({"Indicadores": [
            [*ENC, "2024-2", "2025-1"],
            ["ODO", "EST", "IND-1", "Retención", "", ">=", 85, "%", "SEMESTRAL", "Registro", "resp@ucc.edu.co", 82, "0,88"],
            ["Odontología", "Estudiantes", "IND-2", "Deserción", "", "<=", "0.1", "%", "SEMESTRAL", "", "", 12, None],
        ]})
        r = self.subir(archivo)
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual((d["creados"], d["valores_creados"]), (2, 3))
        self.assertEqual(sorted(d["periodos_creados"]), ["2024-2", "2025-1"])
        self.assertEqual(d["errores"], [])
        i1 = Indicador.objects.get(programa=self.odo, codigo="IND-1")
        self.assertEqual(i1.responsable, self.resp)
        self.assertEqual(RegistroIndicador.objects.get(indicador=i1, periodo__nombre="2025-1").valor, Decimal("0.88") * 100)
        i2 = Indicador.objects.get(programa=self.odo, codigo="IND-2")
        self.assertEqual((i2.tipo_meta, i2.meta), ("MENOR_IGUAL", Decimal("10.00")))
        p = Periodo.objects.get(nombre="2024-2")
        self.assertEqual((p.fecha_inicio.month, p.fecha_fin.month), (7, 12))

    def test_hoja_valores_formato_largo(self):
        Indicador.objects.create(programa=self.odo, factor=self.fac, codigo="IND-1", nombre="R", meta=80)
        archivo = libro({"Valores": [
            ["programa", "codigo", "periodo", "valor", "fecha_corte", "observaciones"],
            ["ODO", "IND-1", "2025-1", 79.5, "30/06/2025", "Dato oficial"],
            ["ODO", "IND-9", "2025-1", 70, None, ""],
            ["ODO", "IND-1", "2025-x", 70, None, ""],
        ]})
        d = self.subir(archivo).json()
        self.assertEqual(d["valores_creados"], 1)
        self.assertEqual(len(d["errores"]), 2)
        reg = RegistroIndicador.objects.get()
        self.assertEqual((reg.valor, str(reg.fecha_corte), reg.observaciones), (Decimal("79.50"), "2025-06-30", "Dato oficial"))

    def test_simular_no_guarda_nada(self):
        archivo = libro({"Indicadores": [[*ENC, "2025-1"], ["ODO", "EST", "IND-1", "R", "", ">=", 80, "%", "", "", "", 75]]})
        d = self.subir(archivo, simular="1").json()
        self.assertTrue(d["simulado"])
        self.assertEqual((d["creados"], d["valores_creados"]), (1, 1))
        self.assertFalse(Indicador.objects.exists())
        self.assertFalse(Periodo.objects.exists())

    def test_reemplazar_borra_datos_previos_solo_del_programa_importado(self):
        viejo = Indicador.objects.create(programa=self.odo, factor=self.fac, codigo="DEMO-1", nombre="Demo", meta=80)
        Alerta.objects.create(programa=self.odo, indicador=viejo, tipo="BAJO_META", severidad="ALTA", clave="k",
                              titulo="x", detalle="x")
        PlanMejoramiento.objects.create(programa=self.odo, accion="Plan demo", fecha_limite="2026-12-31")
        ajeno = Indicador.objects.create(programa=self.otro, factor=self.fac, codigo="DEMO-1", nombre="Demo", meta=80)
        archivo = libro({"Indicadores": [ENC, ["ODO", "EST", "IND-1", "Real", "", ">=", 80, "%", "", "", ""]]})
        d = self.subir(archivo, reemplazar="1").json()
        self.assertEqual(d["eliminados"], 1)
        self.assertEqual(list(Indicador.objects.filter(programa=self.odo).values_list("codigo", flat=True)), ["IND-1"])
        self.assertFalse(PlanMejoramiento.objects.filter(programa=self.odo).exists())
        self.assertTrue(Indicador.objects.filter(pk=ajeno.pk).exists())

    def test_errores_por_fila_no_detienen_la_importacion(self):
        archivo = libro({"Hoja1": [
            ["Seguimiento de indicadores 2025"],
            ENC,
            ["XXX", "EST", "IND-1", "R", "", ">=", 80, "%", "", "", ""],
            ["ODO", "NOPE", "IND-2", "R", "", ">=", 80, "%", "", "", ""],
            ["ODO", "EST", "IND-3", "R", "", ">=", "ochenta", "%", "", "", ""],
            ["ODO", "EST", "IND-4", "R", "", ">=", 80, "%", "", "", "nadie@ucc.edu.co"],
        ]})
        d = self.subir(archivo).json()
        self.assertEqual(d["creados"], 1)
        self.assertEqual(len(d["errores"]), 3)
        self.assertEqual(len(d["avisos"]), 1)

    def test_reemplazar_requiere_rol_global(self):
        director = Usuario.objects.create_user("dir@ucc.edu.co", "Clave-segura-123", rol="DIRECTOR")
        director.programas.add(self.odo)
        self.api.force_authenticate(director)
        archivo = libro({"Indicadores": [ENC, ["ODO", "EST", "IND-1", "R", "", ">=", 80, "%", "", "", ""]]})
        self.assertEqual(self.subir(archivo, reemplazar="1").status_code, 403)

    def test_plantilla_precargada_con_el_programa(self):
        ind = Indicador.objects.create(programa=self.odo, factor=self.fac, codigo="IND-1", nombre="R", meta=80)
        per = Periodo.objects.create(nombre="2025-1", fecha_inicio="2025-01-01", fecha_fin="2025-06-30")
        RegistroIndicador.objects.create(indicador=ind, periodo=per, valor=81)
        r = self.api.get(f"/api/indicadores/plantilla/?programa={self.odo.id}")
        wb = load_workbook(BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Instrucciones", "Indicadores", "Valores", "Catálogos"])
        filas = list(wb["Indicadores"].iter_rows(values_only=True))
        self.assertEqual(filas[0][-1], "2025-1")
        self.assertEqual((filas[1][2], filas[1][-1]), ("IND-1", 81))
