from decimal import Decimal, InvalidOperation

from django.db import transaction
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
from apps.usuarios.models import Usuario
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
        """Importa indicadores desde .xlsx. Columnas: programa (código), factor (código), codigo, nombre,
        descripcion, tipo_meta (>= o <=), meta, unidad (%, numero, escala), periodicidad, fuente, responsable (correo)."""
        from openpyxl import load_workbook

        archivo = request.FILES.get("archivo")
        if not archivo or not archivo.name.lower().endswith(".xlsx"):
            raise drf_serializers.ValidationError({"archivo": "Adjunte un archivo .xlsx."})
        try:
            wb = load_workbook(archivo, read_only=True, data_only=True)
        except Exception:
            raise drf_serializers.ValidationError({"archivo": "No se pudo leer el archivo Excel."})
        ws = wb.active
        filas = list(ws.iter_rows(values_only=True))
        if not filas:
            raise drf_serializers.ValidationError({"archivo": "El archivo está vacío."})
        enc = [str(c or "").strip().lower() for c in filas[0]]
        requeridas = ["programa", "factor", "codigo", "nombre", "meta"]
        faltan = [c for c in requeridas if c not in enc]
        if faltan:
            raise drf_serializers.ValidationError({"archivo": f"Faltan columnas: {', '.join(faltan)}"})
        idx = {c: i for i, c in enumerate(enc)}
        programas = {p.codigo.lower(): p for p in Programa.objects.all()}
        factores = {f.codigo.lower(): f for f in Factor.objects.all()}
        usuarios = {u.email.lower(): u for u in Usuario.objects.all()}
        unidades = {"%": "PORCENTAJE", "porcentaje": "PORCENTAJE", "numero": "NUMERO", "número": "NUMERO", "escala": "ESCALA"}
        creados, actualizados, errores = 0, 0, []

        def val(fila, col):
            i = idx.get(col)
            v = fila[i] if i is not None and i < len(fila) else None
            return str(v).strip() if v is not None else ""

        with transaction.atomic():
            for n, fila in enumerate(filas[1:], start=2):
                if not any(fila):
                    continue
                prog = programas.get(val(fila, "programa").lower())
                fac = factores.get(val(fila, "factor").lower())
                if not prog or not fac:
                    errores.append(f"Fila {n}: programa o factor no existe.")
                    continue
                if not puede_editar_programa(request.user, prog.id):
                    errores.append(f"Fila {n}: sin permisos sobre {prog.nombre_corto}.")
                    continue
                try:
                    meta = Decimal(val(fila, "meta").replace(",", ".").replace("%", ""))
                except InvalidOperation:
                    errores.append(f"Fila {n}: meta inválida.")
                    continue
                datos = {
                    "factor": fac, "nombre": val(fila, "nombre")[:200], "descripcion": val(fila, "descripcion"),
                    "meta": meta,
                    "tipo_meta": "MENOR_IGUAL" if val(fila, "tipo_meta") in ("<=", "≤", "menor") else "MAYOR_IGUAL",
                    "unidad": unidades.get(val(fila, "unidad").lower(), "PORCENTAJE"),
                    "periodicidad": (val(fila, "periodicidad").upper() or "SEMESTRAL")
                    if val(fila, "periodicidad").upper() in Indicador.Periodicidad.values else "SEMESTRAL",
                    "fuente_verificacion": val(fila, "fuente")[:255],
                    "responsable": usuarios.get(val(fila, "responsable").lower()),
                }
                _, creado = Indicador.objects.update_or_create(
                    programa=prog, codigo=val(fila, "codigo")[:20], defaults=datos
                )
                creados += creado
                actualizados += not creado
        registrar(request, Modulo.INDICADORES, f"Importó indicadores desde Excel ({creados} nuevos, {actualizados} actualizados)")
        if creados or actualizados:
            recalcular()
        return Response({"creados": creados, "actualizados": actualizados, "errores": errores})

    @action(detail=False, methods=["get"])
    def plantilla(self, request):
        from io import BytesIO

        from django.http import HttpResponse
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Indicadores"
        ws.append(["programa", "factor", "codigo", "nombre", "descripcion", "tipo_meta", "meta", "unidad",
                   "periodicidad", "fuente", "responsable"])
        ws.append(["ENF", "EST", "IND-099", "Ejemplo de indicador", "Descripción", ">=", 80, "%", "SEMESTRAL",
                   "Registro académico", "correo@ucc.edu.co"])
        buf = BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="plantilla_indicadores.xlsx"'
        return resp

