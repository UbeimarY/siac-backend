
from django.db.models import Prefetch
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.alertas.motor import recalcular, recalcular_programa_seguro
from apps.bitacora.models import RegistroBitacora
from apps.bitacora.servicio import Modulo, registrar
from apps.evidencias.models import Evidencia
from apps.programas.models import Factor, Programa
from apps.usuarios.permisos import NoConsulta, filtrar_por_programa, puede_editar_programa

from .models import Indicador, RegistroIndicador
from .serializers import IndicadorSerializer, RegistroIndicadorSerializer


class IndicadorViewSet(viewsets.ModelViewSet):
    serializer_class = IndicadorSerializer
    permission_classes = [NoConsulta]
    search_fields = ["codigo", "nombre", "descripcion"]
    filterset_fields = ["programa", "factor", "responsable", "periodicidad", "activo"]
    ordering_fields = ["codigo", "nombre"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = Indicador.objects.select_related("programa", "factor", "responsable", "riesgo").prefetch_related(
            Prefetch("registros", queryset=RegistroIndicador.objects.order_by("-fecha_corte", "-id"), to_attr="_regs"),
            Prefetch("evidencias", queryset=Evidencia.objects.all(), to_attr="_evs"),
        )
        return filtrar_por_programa(qs, self.request.user)

    def _anotar(self, objs):
        for o in objs:
            o._registros_cache = o._regs
            o._evidencias_cache = o._evs
        return objs

    def list(self, request, *args, **kwargs):
        objs = self._anotar(list(self.filter_queryset(self.get_queryset())))
        estado = request.query_params.get("estado")
        if estado:
            objs = [o for o in objs if o.estado() == estado]
        return Response(self.get_serializer(objs, many=True).data)

    def get_object(self):
        obj = super().get_object()
        self._anotar([obj])
        return obj

    def _validar_edicion(self, programa_id):
        if not puede_editar_programa(self.request.user, programa_id):
            raise PermissionDenied("No tiene permisos para gestionar indicadores de este programa.")

    def perform_create(self, serializer):
        ind = serializer.save()
        registrar(self.request, Modulo.INDICADORES, f"Creó indicador {ind.codigo}", objeto=ind)
        recalcular_programa_seguro(ind.programa)

    def perform_update(self, serializer):
        self._validar_edicion(serializer.instance.programa_id)
        ind = serializer.save()
        registrar(self.request, Modulo.INDICADORES, f"Actualizó indicador {ind.codigo}", objeto=ind,
                  detalle={"campos": sorted(serializer.validated_data.keys())})
        recalcular_programa_seguro(ind.programa)

    def perform_destroy(self, instance):
        self._validar_edicion(instance.programa_id)
        # Se inactiva para conservar el histórico y la trazabilidad.
        instance.activo = False
        instance.save(update_fields=["activo"])
        registrar(self.request, Modulo.INDICADORES, f"Inactivó indicador {instance.codigo}", objeto=instance)
        recalcular_programa_seguro(instance.programa)

    # --- Registros (valores medidos) -------------------------------------------
    @action(detail=True, methods=["get", "post"])
    def registros(self, request, pk=None):
        ind = self.get_object()
        if request.method == "GET":
            regs = ind.registros.select_related("periodo", "registrado_por").order_by("fecha_corte")
            return Response(RegistroIndicadorSerializer(regs, many=True).data)
        user = request.user
        if not (puede_editar_programa(user, ind.programa_id) or ind.responsable_id == user.id):
            raise PermissionDenied("Solo el responsable del indicador, el director o la coordinación pueden registrar valores.")
        ser = RegistroIndicadorSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        periodo = ser.validated_data["periodo"]
        reg, creado = RegistroIndicador.objects.update_or_create(
            indicador=ind, periodo=periodo,
            defaults={
                "valor": ser.validated_data["valor"],
                "fecha_corte": ser.validated_data.get("fecha_corte") or periodo.fecha_fin,
                "observaciones": ser.validated_data.get("observaciones", ""),
                "registrado_por": user,
            },
        )
        registrar(
            request, Modulo.INDICADORES,
            f"{'Registró' if creado else 'Corrigió'} valor {reg.valor} de {ind.codigo} ({periodo})",
            objeto=ind, detalle={"registro_id": reg.id, "valor": str(reg.valor), "periodo": periodo.nombre},
        )
        recalcular_programa_seguro(ind.programa)
        return Response(RegistroIndicadorSerializer(reg).data, status=status.HTTP_201_CREATED if creado else 200)

    @action(detail=True, methods=["get"])
    def historial(self, request, pk=None):
        """Historial de cambios del indicador (trazabilidad por indicador, numeral 2.4.7)."""
        ind = self.get_object()
        evs = list(ind.evidencias.values_list("id", flat=True))
        qs = RegistroBitacora.objects.select_related("usuario").filter(
            objeto_tipo="Indicador", objeto_id=str(ind.id)
        ) | RegistroBitacora.objects.select_related("usuario").filter(
            objeto_tipo="Evidencia", objeto_id__in=[str(e) for e in evs]
        )
        return Response([
            {"fecha": r.fecha, "usuario": r.usuario.get_full_name() if r.usuario else "Sistema",
             "accion": r.accion, "modulo": r.get_modulo_display()}
            for r in qs.order_by("-fecha")[:100]
        ])

    @action(detail=False, methods=["get"])
    def opciones(self, request):
        qs = filtrar_por_programa(Indicador.objects.filter(activo=True), request.user)
        if request.query_params.get("programa"):
            qs = qs.filter(programa_id=request.query_params["programa"])
        return Response(list(qs.values("id", "codigo", "nombre", "programa_id")))

    # --- Importación desde Excel ----------------------------------------------
    @action(detail=False, methods=["post"], url_path="importar")
    def importar(self, request):
        """Importa indicadores y sus valores históricos desde .xlsx (ver apps/indicadores/importacion.py).
        Parámetros: simular=1 (no guarda nada), reemplazar=1 (borra antes los indicadores de los programas del archivo)."""
        from openpyxl import load_workbook

        from .importacion import importar_libro

        archivo = request.FILES.get("archivo")
        if not archivo or not archivo.name.lower().endswith((".xlsx", ".xlsm")):
            raise drf_serializers.ValidationError({"archivo": "Adjunte un archivo de Excel (.xlsx)."})
        try:
            wb = load_workbook(archivo, read_only=True, data_only=True)
        except Exception:
            raise drf_serializers.ValidationError({"archivo": "No se pudo leer el archivo Excel."})
        si = lambda k: str(request.data.get(k, "")).lower() in ("1", "true", "si", "sí")  # noqa: E731
        simular, reemplazar = si("simular"), si("reemplazar")
        if reemplazar and not request.user.es_global:
            raise PermissionDenied("Solo el administrador o la coordinación pueden reemplazar los datos de un programa.")
        res = importar_libro(wb, request.user, simular=simular, reemplazar=reemplazar)
        if not simular:
            registrar(
                request, Modulo.INDICADORES,
                f"Importó desde Excel: {res.creados} indicadores nuevos, {res.actualizados} actualizados, "
                f"{res.valores_creados + res.valores_actualizados} valores"
                + (f"; reemplazó {res.eliminados} indicadores previos" if res.eliminados else ""),
                detalle={"archivo": archivo.name, **res.como_dict()},
            )
            if res.creados or res.actualizados or res.valores_creados or res.valores_actualizados or res.eliminados:
                recalcular()
        return Response(res.como_dict())

    @action(detail=False, methods=["get"])
    def plantilla(self, request):
        """Plantilla de importación. Con ?programa=<id> trae precargados los indicadores actuales del programa."""
        from io import BytesIO

        from django.http import HttpResponse
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation

        from apps.programas.models import Periodo

        azul, blanco = PatternFill("solid", fgColor="1E3A8A"), Font(bold=True, color="FFFFFF")
        periodos = [p.nombre for p in Periodo.objects.order_by("fecha_inicio")][-4:] or ["2025-1", "2025-2"]
        wb = Workbook()

        ins = wb.active
        ins.title = "Instrucciones"
        ins.column_dimensions["A"].width = 110
        for linea in [
            "PLANTILLA DE IMPORTACIÓN · SIAC Monitor",
            "",
            "1. Hoja «Indicadores»: una fila por indicador. Columnas obligatorias: programa, factor, codigo, nombre, meta.",
            "   · programa: código del programa (ver hoja Catálogos), p. ej. ODO para Odontología.",
            "   · factor: código del factor de calidad (ver hoja Catálogos).",
            "   · tipo_meta: >= si más es mejor (p. ej. retención) o <= si menos es mejor (p. ej. deserción).",
            "   · unidad: %, numero o escala. Los porcentajes pueden escribirse 85 o 0,85.",
            "   · periodicidad: MENSUAL, TRIMESTRAL, SEMESTRAL o ANUAL.",
            "   · responsable: correo de un usuario ya registrado en SIAC Monitor (opcional).",
            "   · Columnas de periodo (2024-2, 2025-1, 2025-2…): valor medido en ese periodo. Agregue o quite las que necesite.",
            "",
            "2. Hoja «Valores» (opcional): otra forma de cargar los valores, una fila por indicador y periodo.",
            "",
            "3. En SIAC Monitor: Indicadores → Importar → elija el archivo → «Simular» para revisar → «Importar».",
            "   Si el código del indicador ya existe en el programa, se actualiza; los periodos nuevos se crean solos.",
            "   La opción «Reemplazar» borra antes todos los indicadores del programa (útil para quitar los datos de demostración).",
        ]:
            ins.append([linea])
        ins["A1"].font = Font(bold=True, size=14, color="1E3A8A")

        ws = wb.create_sheet("Indicadores")
        enc = ["programa", "factor", "codigo", "nombre", "descripcion", "tipo_meta", "meta", "unidad",
               "periodicidad", "fuente", "responsable", *periodos]
        ws.append(enc)
        qs = Indicador.objects.none()
        if request.query_params.get("programa"):
            qs = filtrar_por_programa(Indicador.objects.filter(
                programa_id=request.query_params["programa"], activo=True
            ), request.user).select_related("programa", "factor", "responsable").prefetch_related("registros__periodo")
        for ind in qs:
            vals = {r.periodo.nombre: float(r.valor) for r in ind.registros.all()}
            ws.append([ind.programa.codigo, ind.factor.codigo, ind.codigo, ind.nombre, ind.descripcion,
                       ">=" if ind.tipo_meta == "MAYOR_IGUAL" else "<=", float(ind.meta),
                       {"PORCENTAJE": "%", "NUMERO": "numero", "ESCALA": "escala"}[ind.unidad], ind.periodicidad,
                       ind.fuente_verificacion, ind.responsable.email if ind.responsable else "",
                       *[vals.get(p) for p in periodos]])
        if not qs:
            ws.append(["ODO", "EST", "IND-101", "Tasa de retención estudiantil", "Estudiantes que continúan / matriculados",
                       ">=", 85, "%", "SEMESTRAL", "Registro académico", "", *[None] * len(periodos)])
        for i, ancho in enumerate([10, 8, 11, 42, 40, 9, 8, 9, 13, 26, 28] + [10] * len(periodos), start=1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = ancho
        dv_tipo = DataValidation(type="list", formula1='">=,<="', allow_blank=True)
        dv_uni = DataValidation(type="list", formula1='"%,numero,escala"', allow_blank=True)
        dv_per = DataValidation(type="list", formula1='"MENSUAL,TRIMESTRAL,SEMESTRAL,ANUAL"', allow_blank=True)
        for dv, col in ((dv_tipo, "F"), (dv_uni, "H"), (dv_per, "I")):
            ws.add_data_validation(dv)
            dv.add(f"{col}2:{col}500")

        val = wb.create_sheet("Valores")
        val.append(["programa", "codigo", "periodo", "valor", "fecha_corte", "observaciones"])
        for i, ancho in enumerate([10, 11, 10, 10, 13, 50], start=1):
            val.column_dimensions[val.cell(1, i).column_letter].width = ancho

        cat = wb.create_sheet("Catálogos")
        cat.append(["Código programa", "Programa", "", "Código factor", "Factor"])
        progs = list(Programa.objects.filter(activo=True).values_list("codigo", "nombre"))
        facs = list(Factor.objects.filter(activo=True).values_list("codigo", "nombre"))
        for i in range(max(len(progs), len(facs))):
            p = progs[i] if i < len(progs) else ("", "")
            f = facs[i] if i < len(facs) else ("", "")
            cat.append([p[0], p[1], "", f[0], f[1]])
        for col, ancho in zip("ABCDE", [16, 34, 3, 14, 34], strict=True):
            cat.column_dimensions[col].width = ancho

        for hoja in (ws, val, cat):
            for c in hoja[1]:
                if c.value:
                    c.fill, c.font = azul, blanco
                    c.alignment = Alignment(horizontal="center")
            hoja.freeze_panes = "A2"
        wb.active = 1

        buf = BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="plantilla_importacion_siac.xlsx"'
        return resp

