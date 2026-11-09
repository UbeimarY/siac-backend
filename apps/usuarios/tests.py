"""Pruebas de integración de la API con los datos de demostración: permisos por rol y flujos principales."""
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.programas.models import Periodo, Programa

PWD = "Prueba.Siac2026"
MEDIA = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA)
class APIRolesTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("cargar_demo", password=PWD, verbosity=0)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def login(self, correo):
        r = self.client.post("/api/auth/login/", {"email": correo, "password": PWD}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        return r.data

    def test_login_incorrecto(self):
        r = self.client.post("/api/auth/login/", {"email": "ana.gomez@ucc.edu.co", "password": "mala"}, format="json")
        self.assertEqual(r.status_code, 401)

    def test_estados_de_programas_segun_documento(self):
        self.login("ana.gomez@ucc.edu.co")
        estados = {p["codigo"]: p["estado"] for p in self.client.get("/api/programas/").data}
        self.assertEqual(estados["ODO"], "ACREDITADO")
        for c in ("MED", "DER", "IIN"):
            self.assertEqual(estados[c], "RUTA_ACREDITACION")
        for c in ("ENF", "ISW"):
            self.assertEqual(estados[c], "CONSOLIDACION")

    def test_director_solo_ve_su_programa(self):
        self.login("liliana.torres@ucc.edu.co")
        progs = self.client.get("/api/programas/").data
        self.assertEqual([p["codigo"] for p in progs], ["ISW"])
        otros = self.client.get("/api/indicadores/").data
        self.assertTrue(all(i["programa_nombre"] == "Ing. Software" for i in otros))

    def test_consulta_es_solo_lectura(self):
        self.login("consulta.decanatura@ucc.edu.co")
        self.assertEqual(self.client.get("/api/tablero/").status_code, 200)
        prog = Programa.objects.get(codigo="ENF")
        r = self.client.post("/api/planes/", {"programa": prog.id, "accion": "x", "fecha_limite": "2027-01-01"}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_flujo_evidencia(self):
        self.login("diana.paz@ucc.edu.co")
        ind = self.client.get("/api/indicadores/?programa=" + str(Programa.objects.get(codigo="ENF").id)).data[0]
        archivo = SimpleUploadedFile("informe.pdf", b"%PDF-1.4 x", content_type="application/pdf")
        r = self.client.post("/api/evidencias/", {"indicador": ind["id"], "nombre": "Informe", "tipo": "ARCHIVO",
                                                  "archivo": archivo}, format="multipart")
        self.assertEqual(r.status_code, 201, r.content)
        ev = r.data["id"]
        self.assertEqual(self.client.post(f"/api/evidencias/{ev}/aprobar/").status_code, 403)
        self.login("ana.gomez@ucc.edu.co")
        self.assertEqual(self.client.post(f"/api/evidencias/{ev}/aprobar/", {}, format="json").data["estado"], "APROBADA")

    def test_registro_de_valor_y_bitacora(self):
        self.login("patricia.leal@ucc.edu.co")
        ind = [i for i in self.client.get("/api/indicadores/").data if i["codigo"] == "IND-006"][0]
        per = Periodo.vigente()
        r = self.client.post(f"/api/indicadores/{ind['id']}/registros/", {"periodo": per.id, "valor": "7"}, format="json")
        self.assertIn(r.status_code, (200, 201), r.content)
        self.login("ana.gomez@ucc.edu.co")
        acciones = [b["accion"] for b in self.client.get("/api/bitacora/").data["resultados"]]
        self.assertTrue(any("IND-006" in a for a in acciones))

    def test_reportes_pdf_y_excel(self):
        self.login("ana.gomez@ucc.edu.co")
        for tipo in ("estado_programa", "visita_pares", "seguimiento_indicadores", "planes"):
            r = self.client.get(f"/api/reportes/{tipo}/?formato=pdf")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.content.startswith(b"%PDF"))
            r = self.client.get(f"/api/reportes/{tipo}/?formato=xlsx")
            self.assertEqual(r.status_code, 200)

    def test_solo_global_gestiona_usuarios(self):
        self.login("carlos.rueda@ucc.edu.co")
        r = self.client.post("/api/usuarios/", {"email": "nuevo@ucc.edu.co", "first_name": "N", "last_name": "U",
                                                "rol": "CONSULTA", "password": "Clave.Larga2026"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.login("admin.siac@ucc.edu.co")
        r = self.client.post("/api/usuarios/", {"email": "nuevo@ucc.edu.co", "first_name": "N", "last_name": "U",
                                                "rol": "CONSULTA", "password": "Clave.Larga2026"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
