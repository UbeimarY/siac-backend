"""Pruebas de la IA Beta: red neuronal desde cero, predictor de riesgo, clasificador de intenciones y API."""
from datetime import timedelta
from decimal import Decimal

import numpy as np
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.alertas import motor
from apps.ia.beta import almacen, asistente, intenciones, riesgo
from apps.ia.beta.red import RedNeuronal
from apps.ia.models import InteraccionIA, ModeloBeta
from apps.indicadores.models import Indicador, RegistroIndicador
from apps.programas.models import Factor, Periodo, Programa
from apps.usuarios.models import Usuario


def _gradiente_numerico(red, X, y, eps=1e-6):
    num = []
    for W in red.W:
        g = np.zeros_like(W)
        it = np.nditer(W, flags=["multi_index"])
        for _ in it:
            i = it.multi_index
            orig = W[i]
            W[i] = orig + eps
            mas = red.perdida(X, y)
            W[i] = orig - eps
            menos = red.perdida(X, y)
            W[i] = orig
            g[i] = (mas - menos) / (2 * eps)
        num.append(g)
    return num


class RedNeuronalTests(SimpleTestCase):
    def _verificar(self, salida, y):
        rng = np.random.default_rng(0)
        X = rng.normal(size=(6, 4))
        red = RedNeuronal([4, 5, 3 if salida == "softmax" else 1], salida, semilla=1)
        analitico, _ = red.gradientes(X, y)
        numerico = _gradiente_numerico(red, X, y)
        for a, n in zip(analitico, numerico, strict=True):
            err = np.linalg.norm(a - n) / max(np.linalg.norm(a) + np.linalg.norm(n), 1e-12)
            self.assertLess(err, 1e-5)

    def test_gradientes_sigmoide(self):
        self._verificar("sigmoide", np.array([0, 1, 1, 0, 1, 0], dtype=float))

    def test_gradientes_softmax(self):
        self._verificar("softmax", np.array([0, 1, 2, 2, 1, 0]))

    def test_aprende_xor(self):
        X = np.array([[0, 0], [0, 1], [1, 0], [1, 1]], dtype=float)
        y = np.array([0, 1, 1, 0], dtype=float)
        red = RedNeuronal([2, 8, 1], "sigmoide", semilla=3)
        red.entrenar(X, y, epocas=1500, lote=4, tasa=0.05, l2=0.0)
        self.assertEqual(red.exactitud(X, y), 1.0)

    def test_guardar_y_cargar(self):
        red = RedNeuronal([3, 4, 2], "softmax", semilla=5)
        X = np.random.default_rng(1).normal(size=(5, 3))
        copia, extra = RedNeuronal.desde_bytes(red.a_bytes({"clave": [1, 2]}))
        self.assertEqual(extra["clave"], [1, 2])
        np.testing.assert_allclose(red.predecir_proba(X), copia.predecir_proba(X))
        self.assertEqual(copia.describir(), "3 → 4 → 2 (softmax)")


class ExtraccionTests(SimpleTestCase):
    def test_horizonte(self):
        self.assertEqual(asistente.detectar_horizonte("qué vence esta semana"), 7)
        self.assertEqual(asistente.detectar_horizonte("qué vence en 15 días"), 15)
        self.assertEqual(asistente.detectar_horizonte("vencimientos en 2 meses"), 60)
        self.assertEqual(asistente.detectar_horizonte("próximos vencimientos"), 30)

    def test_programa(self):
        progs = [Programa(id=1, codigo="ODO", nombre="Odontología", nombre_corto="Odontología"),
                 Programa(id=2, codigo="ISW", nombre="Ingeniería de Software", nombre_corto="Ing. Software"),
                 Programa(id=3, codigo="IIN", nombre="Ingeniería Industrial", nombre_corto="Ing. Industrial")]
        self.assertEqual(asistente.detectar_programa("alertas de odontologia", progs).id, 1)
        self.assertEqual(asistente.detectar_programa("cómo va software", progs).id, 2)
        self.assertEqual(asistente.detectar_programa("resumen de Industrial", progs).id, 3)
        self.assertIsNone(asistente.detectar_programa("resumen de ingeniería", progs))


def _serie(ind, valores):
    return ind, [float(v) for v in valores]


class RiesgoTests(TestCase):
    def test_entrena_y_predice(self):
        datos = {}
        rng = np.random.default_rng(4)
        for k in range(30):
            ind = Indicador(id=1000 + k, tipo_meta="MAYOR_IGUAL", meta=Decimal("80"), unidad="PORCENTAJE",
                            periodicidad="SEMESTRAL")
            base = 90 if k % 2 else 70
            datos[ind.id] = _serie(ind, base + rng.normal(0, 3, 5))
        modelo, error = riesgo.entrenar(datos=datos)
        self.assertIsNone(error)
        self.assertEqual(modelo.tipo, "RIESGO")
        self.assertIn("linea_base", modelo.metricas)
        self.assertGreater(modelo.metricas["validacion"]["exactitud"], 0.8)
        ind_malo = datos[1000][0]
        self.assertGreater(len(riesgo.CARACTERISTICAS), 10)
        p = riesgo.predecir([ind_malo])
        self.assertEqual(p, {})  # sin registros en la base no hay historial que evaluar

    def test_sin_datos_suficientes(self):
        modelo, error = riesgo.entrenar(datos={})
        self.assertIsNone(modelo)
        self.assertIn("suficientes", error)


class IntencionesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        almacen.limpiar_cache()
        cls.modelo, _ = intenciones.entrenar()

    def test_metricas(self):
        self.assertGreater(self.modelo.metricas["validacion"]["exactitud"], 0.7)
        self.assertEqual(len(self.modelo.metricas["etiquetas"]), len(intenciones.INTENCIONES))

    def test_clasifica_preguntas_nuevas(self):
        casos = {
            "¿Qué alertas altas hay en Odontología?": "alertas",
            "qué vence en los próximos 10 días": "vencimientos",
            "qué planes están retrasados": "planes",
            "cuándo es la visita de pares": "visita_pares",
            "qué evidencias me faltan por subir": "evidencias",
            "qué programa tiene más riesgo": "ranking_programas",
            "cuéntame un chiste": "fuera_alcance",
        }
        aciertos = sum(intenciones.clasificar(q)[0] == esperado for q, esperado in casos.items())
        self.assertGreaterEqual(aciertos, len(casos) - 1)


class APIBetaTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        almacen.limpiar_cache()
        hoy = timezone.localdate()
        f = Factor.objects.create(codigo="EST", nombre="Estudiantes", abreviatura="EST")
        periodos = [Periodo.objects.create(nombre=f"202{k}-1", fecha_inicio=hoy - timedelta(days=200 * (6 - k)),
                                           fecha_fin=hoy - timedelta(days=200 * (6 - k) - 150)) for k in range(5)]
        cls.odo = Programa.objects.create(codigo="ODO", nombre="Odontología", nombre_corto="Odontología")
        cls.med = Programa.objects.create(codigo="MED", nombre="Medicina", nombre_corto="Medicina")
        rng = np.random.default_rng(2)
        for prog in (cls.odo, cls.med):
            for k in range(14):
                ind = Indicador.objects.create(programa=prog, factor=f, codigo=f"IND-{k:03d}", nombre=f"Indicador {k}",
                                               meta=Decimal("80"))
                base = 88 if k % 2 else 72
                for per in periodos:
                    RegistroIndicador.objects.create(indicador=ind, periodo=per, fecha_corte=per.fecha_fin,
                                                     valor=Decimal(str(round(base + rng.normal(0, 3), 2))))
        cls.coord = Usuario.objects.create_user("coord@ucc.edu.co", "Clave.Segura2026", rol=Usuario.Rol.COORDINADOR)
        cls.director = Usuario.objects.create_user("dir@ucc.edu.co", "Clave.Segura2026", rol=Usuario.Rol.DIRECTOR)
        cls.director.programas.add(cls.odo)
        motor.recalcular()
        intenciones.entrenar()
        riesgo.entrenar()

    def test_estado(self):
        self.client.force_authenticate(self.coord)
        r = self.client.get("/api/ia/beta/estado/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["riesgo"]["tipo"], "RIESGO")
        self.assertIn("historial", r.data["intencion"])

    def test_asistente_respeta_permisos(self):
        self.client.force_authenticate(self.director)
        r = self.client.post("/api/ia/beta/asistente/", {"mensaje": "qué alertas hay en medicina"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["intencion"], "alertas")
        self.assertIn("No tienes permiso para consultar Medicina", r.data["respuesta"])
        self.assertNotIn("— Medicina", r.data["respuesta"])
        self.assertTrue(InteraccionIA.objects.filter(funcion="ASISTENTE_BETA", usuario=self.director).exists())

    def test_predicciones_filtradas(self):
        self.client.force_authenticate(self.director)
        r = self.client.get("/api/ia/beta/predicciones/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["resultados"])
        self.assertEqual({x["programa"] for x in r.data["resultados"]}, {"Odontología"})

    def test_entrenar_solo_global(self):
        self.client.force_authenticate(self.director)
        self.assertEqual(self.client.post("/api/ia/beta/entrenar/", {}, format="json").status_code, 403)
        self.client.force_authenticate(self.coord)
        antes = ModeloBeta.objects.filter(tipo="RIESGO").count()
        r = self.client.post("/api/ia/beta/entrenar/", {"modelo": "RIESGO"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ModeloBeta.objects.filter(tipo="RIESGO").count(), antes + 1)
        self.assertEqual(ModeloBeta.objects.filter(tipo="RIESGO", activo=True).count(), 1)
