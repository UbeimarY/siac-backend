"""Importación masiva desde Excel: indicadores y sus valores históricos por periodo.

Hojas reconocidas (por nombre, sin importar mayúsculas):

* ``Indicadores`` (o la primera hoja si ninguna se llama así). Columnas: programa, factor, codigo, nombre,
  descripcion, tipo_meta, meta, unidad, periodicidad, fuente, responsable. Cualquier columna adicional cuyo
  encabezado sea un periodo (``2025-1``, ``2025-2``, ``2026``…) se toma como el valor medido en ese periodo
  (formato "ancho", el más común en las hojas de seguimiento de las oficinas de calidad).
* ``Valores`` (opcional, formato "largo"). Columnas: programa, codigo, periodo, valor, fecha_corte, observaciones.

Los periodos que no existan se crean automáticamente (``AAAA-1`` = enero–junio, ``AAAA-2`` = julio–diciembre,
``AAAA`` = año completo). Con ``simular=True`` todo se ejecuta dentro de una transacción que se revierte, de modo
que el usuario ve exactamente qué pasaría sin guardar nada.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from apps.programas.models import Factor, Periodo, Programa
from apps.usuarios.models import Usuario
from apps.usuarios.permisos import puede_editar_programa

from .models import Indicador, RegistroIndicador

RE_PERIODO = re.compile(r"^(\d{4})(?:\s*[-–/ ]\s*([12]|i{1,2}))?$", re.IGNORECASE)
UNIDADES = {"%": "PORCENTAJE", "porcentaje": "PORCENTAJE", "numero": "NUMERO", "número": "NUMERO",
            "escala": "ESCALA", "escala (1–5)": "ESCALA", "escala (1-5)": "ESCALA"}
COLUMNAS_INDICADOR = {"programa", "factor", "codigo", "código", "nombre", "descripcion", "descripción", "tipo_meta",
                      "meta", "unidad", "periodicidad", "fuente", "responsable"}
SINONIMOS = {"código": "codigo", "descripción": "descripcion", "fuente de verificación": "fuente",
             "fuente_verificacion": "fuente", "tipo de meta": "tipo_meta", "fecha de corte": "fecha_corte",
             "responsable (correo)": "responsable", "correo responsable": "responsable"}


@dataclass
class Resultado:
    simulado: bool = False
    creados: int = 0
    actualizados: int = 0
    valores_creados: int = 0
    valores_actualizados: int = 0
    periodos_creados: list[str] = field(default_factory=list)
    eliminados: int = 0
    programas: set[str] = field(default_factory=set)
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def como_dict(self):
        return {
            "simulado": self.simulado, "creados": self.creados, "actualizados": self.actualizados,
            "valores_creados": self.valores_creados, "valores_actualizados": self.valores_actualizados,
            "periodos_creados": self.periodos_creados, "eliminados": self.eliminados,
            "programas": sorted(self.programas), "errores": self.errores[:200], "avisos": self.avisos[:100],
        }


class _Simulacion(Exception):
    """Se lanza para revertir la transacción en modo simulación."""


def normalizar_periodo(texto) -> str | None:
    if texto is None:
        return None
    if isinstance(texto, (int, float)) and float(texto).is_integer():
        texto = str(int(texto))
    m = RE_PERIODO.match(str(texto).strip())
    if not m:
        return None
    anio, sem = m.group(1), (m.group(2) or "").lower()
    sem = {"i": "1", "ii": "2"}.get(sem, sem)
    return f"{anio}-{sem}" if sem else anio


def _encabezado(c) -> str:
    t = str(c or "").strip().lower()
    return SINONIMOS.get(t, t)


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _decimal(v) -> Decimal | None:
    if v is None or _texto(v) == "":
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    t = _texto(v).replace("%", "").replace(" ", "")
    if "," in t and "." in t:  # 1.234,5 → 1234.5
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _fecha(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = _texto(v)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None


def _hoja(wb, nombre):
    for ws in wb.worksheets:
        if ws.title.strip().lower() == nombre:
            return ws
    return None


def _filas(ws):
    filas = [f for f in ws.iter_rows(values_only=True)]
    # Salta filas vacías o de título antes del encabezado real.
    for i, f in enumerate(filas[:10]):
        enc = [_encabezado(c) for c in f]
        if "codigo" in enc:
            return enc, filas[i + 1:], i + 1
    return None, [], 0


class Importador:
    def __init__(self, usuario, reemplazar=False):
        self.usuario = usuario
        self.reemplazar = reemplazar
        self.r = Resultado()
        self.programas = {p.codigo.lower(): p for p in Programa.objects.all()}
        self.programas.update({p.nombre.lower(): p for p in Programa.objects.all()})
        self.programas.update({p.nombre_corto.lower(): p for p in Programa.objects.all()})
        self.codigos_factor = sorted(Factor.objects.values_list("codigo", flat=True))
        self.factores = {f.codigo.lower(): f for f in Factor.objects.all()}
        self.factores.update({f.nombre.lower(): f for f in Factor.objects.all()})
        self.usuarios = {u.email.lower(): u for u in Usuario.objects.all()}
        self.periodos = {p.nombre: p for p in Periodo.objects.all()}
        self.limpiados: set[int] = set()

    # --- Utilidades -------------------------------------------------------------
    def programa(self, texto, fila, hoja, limpiar=False):
        prog = self.programas.get(_texto(texto).lower())
        if not prog:
            self.r.errores.append(f"{hoja}, fila {fila}: el programa «{_texto(texto)}» no existe en SIAC Monitor.")
            return None
        if not puede_editar_programa(self.usuario, prog.id):
            self.r.errores.append(f"{hoja}, fila {fila}: sin permisos sobre {prog.nombre_corto}.")
            return None
        if limpiar and self.reemplazar and prog.id not in self.limpiados:
            from apps.planes.models import PlanMejoramiento

            qs = Indicador.objects.filter(programa=prog)
            self.r.eliminados += qs.count()
            qs.delete()  # en cascada: valores, evidencias, alertas y puntajes de riesgo
            PlanMejoramiento.objects.filter(programa=prog).delete()
            self.limpiados.add(prog.id)
        self.r.programas.add(prog.nombre_corto)
        return prog

    def periodo(self, texto):
        nombre = normalizar_periodo(texto)
        if not nombre:
            return None
        if nombre in self.periodos:
            return self.periodos[nombre]
        anio = int(nombre[:4])
        if nombre.endswith("-1"):
            ini, fin = date(anio, 1, 1), date(anio, 6, 30)
        elif nombre.endswith("-2"):
            ini, fin = date(anio, 7, 1), date(anio, 12, 31)
        else:
            ini, fin = date(anio, 1, 1), date(anio, 12, 31)
        p = Periodo.objects.create(nombre=nombre, fecha_inicio=ini, fecha_fin=fin, activo=False)
        self.periodos[nombre] = p
        self.r.periodos_creados.append(nombre)
        return p

    def registrar_valor(self, ind, periodo, valor, fecha_corte=None, obs="", ref=""):
        if ind.unidad == "PORCENTAJE" and Decimal(0) < valor <= Decimal(1) and ind.meta > 1:
            self.r.avisos.append(f"{ref}: el valor {valor} de {ind.codigo} parece una fracción; "
                                 f"se interpretó como {valor * 100} %.")
            valor = valor * 100
        _, creado = RegistroIndicador.objects.update_or_create(
            indicador=ind, periodo=periodo,
            defaults={"valor": valor, "fecha_corte": fecha_corte or periodo.fecha_fin,
                      "observaciones": obs, "registrado_por": self.usuario},
        )
        if creado:
            self.r.valores_creados += 1
        else:
            self.r.valores_actualizados += 1

    # --- Hojas ------------------------------------------------------------------
    def hoja_indicadores(self, ws):
        enc, filas, base = _filas(ws)
        if enc is None:
            self.r.errores.append(f"Hoja «{ws.title}»: no se encontró la fila de encabezados (debe incluir «codigo»).")
            return
        faltan = [c for c in ("programa", "factor", "codigo", "nombre", "meta") if c not in enc]
        if faltan:
            self.r.errores.append(f"Hoja «{ws.title}»: faltan columnas {', '.join(faltan)}.")
            return
        idx = {c: i for i, c in enumerate(enc) if c}
        cols_periodo = {i: normalizar_periodo(c) for i, c in enumerate(enc)
                        if c and c not in COLUMNAS_INDICADOR and normalizar_periodo(c)}

        def val(fila, col):
            i = idx.get(col)
            return fila[i] if i is not None and i < len(fila) else None

        for n, fila in enumerate(filas, start=base + 1):
            if not any(_texto(c) for c in fila):
                continue
            ref = f"{ws.title}, fila {n}"
            codigo = _texto(val(fila, "codigo"))[:20]
            if not codigo:
                self.r.errores.append(f"{ref}: falta el código del indicador.")
                continue
            prog = self.programa(val(fila, "programa"), n, ws.title, limpiar=True)
            if not prog:
                continue
            fac = self.factores.get(_texto(val(fila, "factor")).lower())
            if not fac:
                self.r.errores.append(f"{ref}: el factor «{_texto(val(fila, 'factor'))}» no existe "
                                      f"(use el código: {', '.join(self.codigos_factor)}).")
                continue
            meta = _decimal(val(fila, "meta"))
            if meta is None:
                self.r.errores.append(f"{ref}: la meta «{_texto(val(fila, 'meta'))}» no es un número.")
                continue
            tipo = _texto(val(fila, "tipo_meta")).lower()
            periodicidad = _texto(val(fila, "periodicidad")).upper()
            correo = _texto(val(fila, "responsable")).lower()
            responsable = self.usuarios.get(correo)
            if correo and not responsable:
                self.r.avisos.append(f"{ref}: el responsable {correo} no está registrado; el indicador queda sin responsable.")
            unidad = UNIDADES.get(_texto(val(fila, "unidad")).lower(), "PORCENTAJE")
            if unidad == "PORCENTAJE" and Decimal(0) < meta <= Decimal(1):
                meta = meta * 100
            datos = {
                "factor": fac, "nombre": (_texto(val(fila, "nombre")) or codigo)[:200],
                "descripcion": _texto(val(fila, "descripcion")), "meta": meta,
                "tipo_meta": "MENOR_IGUAL" if tipo in ("<=", "≤", "menor", "menor o igual", "menor_igual") else "MAYOR_IGUAL",
                "unidad": unidad,
                "periodicidad": periodicidad if periodicidad in Indicador.Periodicidad.values else "SEMESTRAL",
                "fuente_verificacion": _texto(val(fila, "fuente"))[:255], "activo": True,
            }
            if responsable:
                datos["responsable"] = responsable
            ind, creado = Indicador.objects.update_or_create(programa=prog, codigo=codigo, defaults=datos)
            if creado:
                self.r.creados += 1
            else:
                self.r.actualizados += 1
            for i, nombre_periodo in cols_periodo.items():
                bruto = fila[i] if i < len(fila) else None
                if bruto is None or _texto(bruto) == "":
                    continue
                valor = _decimal(bruto)
                if valor is None:
                    self.r.errores.append(f"{ref}: el valor «{_texto(bruto)}» de {nombre_periodo} no es un número.")
                    continue
                self.registrar_valor(ind, self.periodo(nombre_periodo), valor, ref=ref)

    def hoja_valores(self, ws):
        enc, filas, base = _filas(ws)
        if enc is None or not {"programa", "periodo", "valor"} <= set(enc):
            self.r.errores.append(f"Hoja «{ws.title}»: se requieren las columnas programa, codigo, periodo y valor.")
            return
        idx = {c: i for i, c in enumerate(enc) if c}

        def val(fila, col):
            i = idx.get(col)
            return fila[i] if i is not None and i < len(fila) else None

        for n, fila in enumerate(filas, start=base + 1):
            if not any(_texto(c) for c in fila):
                continue
            ref = f"{ws.title}, fila {n}"
            prog = self.programa(val(fila, "programa"), n, ws.title)
            if not prog:
                continue
            codigo = _texto(val(fila, "codigo"))
            ind = Indicador.objects.filter(programa=prog, codigo=codigo).first()
            if not ind:
                self.r.errores.append(f"{ref}: el indicador {codigo} no existe en {prog.nombre_corto}.")
                continue
            periodo = self.periodo(val(fila, "periodo"))
            if not periodo:
                self.r.errores.append(f"{ref}: el periodo «{_texto(val(fila, 'periodo'))}» no es válido (use 2025-1, 2025-2…).")
                continue
            valor = _decimal(val(fila, "valor"))
            if valor is None:
                self.r.errores.append(f"{ref}: el valor «{_texto(val(fila, 'valor'))}» no es un número.")
                continue
            self.registrar_valor(ind, periodo, valor, _fecha(val(fila, "fecha_corte")),
                                 _texto(val(fila, "observaciones")), ref)


def importar_libro(wb, usuario, simular=False, reemplazar=False) -> Resultado:
    imp = Importador(usuario, reemplazar=reemplazar)
    imp.r.simulado = simular
    hoja_ind = _hoja(wb, "indicadores")
    hoja_val = _hoja(wb, "valores")
    if hoja_ind is None and hoja_val is None:
        hoja_ind = wb.worksheets[0]
    try:
        with transaction.atomic():
            if hoja_ind is not None:
                imp.hoja_indicadores(hoja_ind)
            if hoja_val is not None:
                imp.hoja_valores(hoja_val)
            if simular:
                raise _Simulacion
    except _Simulacion:
        pass
    return imp.r
