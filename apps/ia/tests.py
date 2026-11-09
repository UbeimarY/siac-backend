from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.alertas import motor
from apps.alertas.models import Alerta
from apps.evidencias.models import Evidencia
from apps.indicadores.models import Indicador, RegistroIndicador
from apps.programas.models import Factor, Periodo, Programa
from apps.usuarios.models import Usuario

from . import servicio


def respuesta(texto):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=texto)],
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50))


@override_settings(IA_PROVEEDOR="anthropic", ANTHROPIC_API_KEY="test-key", IA_API_KEY="", IA_MODELO="")
class IATests(APITestCase):
    def setUp(self):
        hoy = timezone.localdate()
        f = Factor.objects.create(codigo="EST", nombre="Estudiantes", abreviatura="EST")
        per = Periodo.objects.create(nombre="2026-1", fecha_inicio=hoy - timedelta(days=150),
                                     fecha_fin=hoy - timedelta(days=10), activo=True)
        self.prog = Programa.objects.create(codigo="ENF", nombre="Enfermería", nombre_corto="Enfermería")
        self.ind = Indicador.objects.create(programa=self.prog, factor=f, codigo="IND-002", nombre="Deserción",
                                            tipo_meta="MENOR_IGUAL", meta=Decimal("8"))
        RegistroIndicador.objects.create(indicador=self.ind, periodo=per, valor=Decimal("9.5"), fecha_corte=per.fecha_fin)
        self.user = Usuario.objects.create_user("coord@ucc.edu.co", "Clave.Segura2026", first_name="Ana",
                                                last_name="Gómez", rol=Usuario.Rol.COORDINADOR)
        self.client.force_authenticate(self.user)
        motor.recalcular()
        servicio._cliente = None

    def mock_cliente(self, texto):
        cli = mock.MagicMock()
        cli.messages.create.return_value = respuesta(texto)
        return mock.patch.object(servicio, "cliente", return_value=cli), cli

    def test_sugerir_plan(self):
        alerta = Alerta.objects.filter(tipo="BAJO_META").first()
        p, cli = self.mock_cliente('{"acciones": [{"accion": "Implementar tutorías", "descripcion": "d", '
                                   '"meta_esperada": "Bajar a 8%", "plazo_dias": 90, "rol_responsable": "Director"}], '
                                   '"justificacion": "j"}')
        with p:
            r = self.client.post("/api/ia/sugerir-plan/", {"alerta": alerta.id}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["acciones"][0]["accion"], "Implementar tutorías")
        prompt = cli.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("IND-002", prompt)

    def test_asistente_incluye_contexto(self):
        p, cli = self.mock_cliente("Enfermería tiene 1 indicador en riesgo.")
        with p:
            r = self.client.post("/api/ia/asistente/", {"mensajes": [{"role": "user", "content": "¿Cómo va Enfermería?"}]},
                                 format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Enfermería", cli.messages.create.call_args.kwargs["system"])

    def test_resumen(self):
        p, _ = self.mock_cliente("Resumen ejecutivo.")
        with p:
            r = self.client.post("/api/ia/resumen/", {"tipo": "estado_programa", "programa": self.prog.id}, format="json")
        self.assertEqual(r.data["resumen"], "Resumen ejecutivo.")

    def test_revisar_evidencia_csv(self):
        from django.core.files.base import ContentFile

        ev = Evidencia(indicador=self.ind, nombre="Datos", estado=Evidencia.Estado.PENDIENTE)
        ev.archivo.save("datos.csv", ContentFile(b"periodo,desercion\n2026-1,9.5\n"), save=True)
        p, _ = self.mock_cliente('```json\n{"corresponde": "SI", "confianza": "ALTA", "periodo_detectado": "2026-1", '
                                 '"valor_detectado": "9.5", "resumen": "ok", "observaciones": [], "faltantes": []}\n```')
        with p:
            r = self.client.post(f"/api/ia/revisar-evidencia/{ev.id}/")
        self.assertEqual(r.status_code, 200, r.content)
        ev.refresh_from_db()
        self.assertEqual(ev.revision_ia["corresponde"], "SI")
        self.assertEqual(ev.estado, Evidencia.Estado.PENDIENTE)  # la IA no aprueba

    @override_settings(ANTHROPIC_API_KEY="")
    def test_sin_clave_503(self):
        r = self.client.post("/api/ia/asistente/", {"mensajes": [{"role": "user", "content": "hola"}]}, format="json")
        self.assertEqual(r.status_code, 503)

    def test_extraer_json_tolerante(self):
        self.assertEqual(servicio.extraer_json('Claro: {"a": 1} listo'), {"a": 1})


@override_settings(IA_PROVEEDOR="groq", IA_API_KEY="gsk-test", IA_MODELO="", ANTHROPIC_API_KEY="")
class ProveedorCompatibleTests(IATests):
    """Las mismas pruebas con un proveedor compatible con OpenAI (Groq)."""

    def mock_cliente(self, texto):
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = {"choices": [{"message": {"content": texto}}],
                                  "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        llamadas = mock.MagicMock()

        def post(url, headers, json, timeout):
            assert url == "https://api.groq.com/openai/v1/chat/completions"
            assert json["messages"][0]["role"] == "system"
            llamadas.messages.create(system=json["messages"][0]["content"], messages=[
                {"role": m["role"], "content": m["content"]} for m in json["messages"][1:]])
            return resp

        return mock.patch("httpx.post", side_effect=post), llamadas

    @override_settings(IA_API_KEY="")
    def test_sin_clave_503(self):
        r = self.client.post("/api/ia/asistente/", {"mensajes": [{"role": "user", "content": "hola"}]}, format="json")
        self.assertEqual(r.status_code, 503)

    def test_pdf_se_convierte_a_texto(self):
        import base64
        from io import BytesIO

        from reportlab.pdfgen import canvas

        buf = BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(72, 720, "Deserción 2026-1: 9.5%")
        c.save()
        msgs = servicio._a_formato_openai("s", [{"role": "user", "content": [
            {"type": "document", "source": {"data": base64.b64encode(buf.getvalue()).decode()}}]}])
        self.assertIn("9.5", msgs[1]["content"])
