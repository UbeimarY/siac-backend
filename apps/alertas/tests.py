from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.evidencias.models import Evidencia
from apps.indicadores.models import Indicador, RegistroIndicador
from apps.planes.models import PlanMejoramiento
from apps.programas.models import Factor, Periodo, Programa

from . import motor
from .models import Alerta, ConfiguracionRiesgo


class MotorAlertasTests(TestCase):
    def setUp(self):
        hoy = timezone.localdate()
        self.hoy = hoy
        self.f = Factor.objects.create(codigo="EST", nombre="Estudiantes", abreviatura="EST")
        self.p1 = Periodo.objects.create(nombre="A", fecha_inicio=hoy - timedelta(days=400), fecha_fin=hoy - timedelta(days=220))
        self.p2 = Periodo.objects.create(nombre="B", fecha_inicio=hoy - timedelta(days=200), fecha_fin=hoy - timedelta(days=20), activo=True)
        self.prog = Programa.objects.create(codigo="X", nombre="Programa X", nombre_corto="X")
        self.ind = Indicador.objects.create(programa=self.prog, factor=self.f, codigo="I1", nombre="Retención",
                                            meta=Decimal("80"), periodicidad="SEMESTRAL")

    def registrar(self, periodo, valor):
        return RegistroIndicador.objects.create(indicador=self.ind, periodo=periodo, valor=Decimal(valor),
                                                fecha_corte=periodo.fecha_fin)

    def tipos_activos(self):
        return set(Alerta.objects.filter(estado="ACTIVA").values_list("tipo", flat=True))

    def test_cumplimiento_es_100_si_cumple_meta(self):
        self.registrar(self.p2, "85")
        self.assertEqual(self.ind.cumplimiento(), 100.0)

    def test_cumplimiento_meta_menor_igual(self):
        self.ind.tipo_meta = Indicador.TipoMeta.MENOR_IGUAL
        self.ind.meta = Decimal("8")
        self.ind.save()
        self.assertEqual(self.ind.cumplimiento(Decimal("8")), 100.0)
        self.assertAlmostEqual(self.ind.cumplimiento(Decimal("9.2")), 85.0)

    def test_tendencia_deterioro(self):
        self.registrar(self.p1, "85")
        self.registrar(self.p2, "70")
        self.assertEqual(self.ind.tendencia(), "DETERIORO")

    def test_alertas_bajo_meta_y_evidencia(self):
        self.registrar(self.p1, "85")
        self.registrar(self.p2, "70")
        motor.recalcular()
        self.assertTrue({"BAJO_META", "EVIDENCIA_FALTANTE"} <= self.tipos_activos())

    def test_alerta_se_resuelve_cuando_desaparece_condicion(self):
        self.registrar(self.p2, "70")
        motor.recalcular()
        RegistroIndicador.objects.filter(indicador=self.ind).update(valor=Decimal("90"))
        motor.recalcular()
        self.assertNotIn("BAJO_META", self.tipos_activos())
        self.assertTrue(Alerta.objects.filter(tipo="BAJO_META", estado="RESUELTA").exists())

    def test_alertas_no_se_duplican(self):
        self.registrar(self.p2, "70")
        motor.recalcular()
        n = Alerta.objects.count()
        motor.recalcular()
        self.assertEqual(Alerta.objects.count(), n)

    def test_desactualizado(self):
        self.registrar(self.p1, "85")  # último registro hace 220 días > periodicidad semestral
        motor.recalcular()
        self.assertIn("DESACTUALIZADO", self.tipos_activos())

    def test_solicitud_vencida_genera_alerta_de_vencimiento(self):
        self.registrar(self.p2, "85")
        Evidencia.objects.create(indicador=self.ind, nombre="Informe", estado=Evidencia.Estado.POR_CARGAR,
                                 fecha_limite=self.hoy - timedelta(days=1))
        motor.recalcular()
        self.assertIn("VENCIMIENTO", self.tipos_activos())

    def test_plan_retrasado(self):
        self.registrar(self.p2, "85")
        PlanMejoramiento.objects.create(programa=self.prog, accion="Acción", fecha_inicio=self.hoy - timedelta(days=60),
                                        fecha_limite=self.hoy - timedelta(days=1), avance=50)
        motor.recalcular()
        self.assertIn("PLAN_RETRASADO", self.tipos_activos())

    def test_configuracion_por_programa_cambia_el_puntaje(self):
        self.registrar(self.p2, "70")
        base = motor.calcular_indicador(self.ind, ConfiguracionRiesgo.global_()).puntaje
        cfg = ConfiguracionRiesgo.objects.create(programa=self.prog, peso_tendencia=0, peso_brecha=100,
                                                 peso_evidencia=0, peso_vencimiento=0, peso_visita=0)
        propio = motor.calcular_indicador(self.ind, cfg).puntaje
        self.assertNotEqual(base, propio)
        self.assertEqual(ConfiguracionRiesgo.para_programa(self.prog), cfg)

    def test_visita_cercana_aumenta_riesgo(self):
        self.registrar(self.p2, "85")
        cfg = ConfiguracionRiesgo.global_()
        sin = motor.calcular_indicador(self.ind, cfg).puntaje
        self.prog.fecha_visita_pares = self.hoy + timedelta(days=30)
        self.prog.save()
        con = motor.calcular_indicador(self.ind, cfg).puntaje
        self.assertGreater(con, sin)

    def test_agregar_pondera_los_mas_criticos(self):
        self.assertEqual(motor.agregar([]), None)
        puntajes = [90, 10, 10, 10, 10, 10]
        self.assertGreater(motor.agregar(puntajes), sum(puntajes) / len(puntajes))
