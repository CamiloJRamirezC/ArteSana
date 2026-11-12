"""Importar y exportar la lista de materiales en Excel.

Formato (el mismo que genera «Descargar Excel»):
  Ingredientes: Ingrediente | Presentación | Unidad | Precio (Gs) | Notas
  Productos:    Receta | Tipo | Rinde | Unidad del rinde | Multiplicador | Precio de venta real | Notas
  Recetas:      Receta | Ingrediente o subreceta | Cantidad

También acepta la planilla ArteSana_Lista_de_Materiales.xlsx (encabezados parecidos).
Los encabezados se reconocen aunque cambien mayúsculas, tildes o tengan texto extra.
"""
import io
import re
import unicodedata
from collections import OrderedDict
from datetime import date

import costeo
import db
from utils import parse_num

UNIDADES_ALIAS = {
    "g": "g", "gr": "g", "grs": "g", "gramo": "g", "gramos": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogramo": "kg", "kilogramos": "kg",
    "ml": "ml", "cc": "ml", "mililitro": "ml", "mililitros": "ml",
    "l": "l", "lt": "l", "lts": "l", "litro": "l", "litros": "l",
    "u": "u", "un": "u", "und": "u", "unid": "u", "unidad": "u", "unidades": "u", "pieza": "u", "piezas": "u",
}
UNIDADES_RINDE_ALIAS = {
    **{k: v for k, v in UNIDADES_ALIAS.items() if v in ("g", "kg", "ml", "u")},
    "porcion": "porción", "porciones": "porción", "caja": "caja", "cajas": "caja",
}


def normalizar(texto):
    """'  Harina  000 ' -> 'harina 000'; sin tildes ni mayúsculas, para comparar nombres."""
    t = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower()


def limpiar_nombre(texto):
    return re.sub(r"\s+", " ", str(texto or "")).strip()


def unidad(texto, alias=UNIDADES_ALIAS):
    return alias.get(normalizar(texto).rstrip("."))


def numero(valor):
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    return parse_num(valor)


# ------------------------------------------------------------------ lectura

HOJAS = {
    "ingredientes": {
        "nombre": lambda h: h.startswith("ingrediente") or h == "nombre",
        "tipo": lambda h: h.startswith("tipo"),
        "presentacion": lambda h: h.startswith("presentacion"),
        "unidad": lambda h: h.startswith("unidad"),
        "precio": lambda h: h.startswith("precio") and "por " not in h,
        "notas": lambda h: h.startswith("notas") or h.startswith("donde"),
    },
    "productos": {
        "nombre": lambda h: h.startswith("receta") or h.startswith("producto") or h == "nombre",
        "tipo": lambda h: h.startswith("tipo"),
        "rinde": lambda h: h.startswith("rinde"),
        "unidad": lambda h: h.startswith("unidad"),
        "multiplicador": lambda h: h.startswith("multiplicador"),
        "precio_real": lambda h: "real" in h and h.startswith("precio"),
        "notas": lambda h: h.startswith("notas"),
    },
    "recetas": {
        "receta": lambda h: h.startswith("receta"),
        "componente": lambda h: h.startswith("ingrediente") or h.startswith("componente"),
        "cantidad": lambda h: h.startswith("cantidad"),
    },
}
OBLIGATORIAS = {"ingredientes": ("nombre", "presentacion", "unidad", "precio"),
                "productos": ("nombre", "rinde"),
                "recetas": ("receta", "componente", "cantidad")}


def _buscar_columnas(ws, reglas):
    for fila in range(1, 6):
        columnas = {}
        for celda in ws[fila]:
            h = normalizar(celda.value)
            if not h:
                continue
            for campo, regla in reglas.items():
                if campo not in columnas and regla(h):
                    columnas[campo] = celda.column - 1
                    break
        if "nombre" in columnas or "receta" in columnas:
            return fila, columnas
    return None, {}


def leer_excel(archivo):
    """Lee el Excel y devuelve {'ingredientes': [...], 'productos': [...], 'recetas': [...], 'errores': [...]}.
    Cada fila trae 'fila' (número en Excel) para poder señalar errores."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(archivo, data_only=True)
    except Exception:
        return {"ingredientes": [], "productos": [], "recetas": [],
                "errores": [("Archivo", None, "No es un Excel válido (.xlsx)")]}
    datos = {"ingredientes": [], "productos": [], "recetas": [], "errores": []}
    hojas = {normalizar(n): n for n in wb.sheetnames}
    encontradas = 0
    for clave, reglas in HOJAS.items():
        nombre_hoja = hojas.get(clave)
        if not nombre_hoja:
            continue
        ws = wb[nombre_hoja]
        fila_enc, cols = _buscar_columnas(ws, reglas)
        faltan = [c for c in OBLIGATORIAS[clave] if c not in cols]
        if fila_enc is None or faltan:
            datos["errores"].append((nombre_hoja, None, "No se reconocen las columnas: falta " +
                                     ", ".join(faltan or OBLIGATORIAS[clave])))
            continue
        encontradas += 1
        for n, valores in enumerate(ws.iter_rows(min_row=fila_enc + 1, values_only=True), start=fila_enc + 1):
            registro = {campo: (valores[i] if i < len(valores) else None) for campo, i in cols.items()}
            clave_fila = registro.get("nombre") if clave != "recetas" else registro.get("receta")
            if clave_fila is None or str(clave_fila).strip() == "":
                continue
            registro["fila"] = n
            datos[clave].append(registro)
    wb.close()
    if not encontradas:
        datos["errores"].append(("Archivo", None, "No tiene hojas «Ingredientes», «Productos» o «Recetas»"))
    return datos


# ------------------------------------------------------------------ validar y aplicar

def procesar(datos, aplicar=False):
    """Compara el Excel con la base. Con aplicar=True además guarda (todo junto o nada).
    Las filas con errores se saltan; una receta con alguna línea mal no se modifica."""
    errores = list(datos.get("errores", []))
    res = {"ing_nuevos": [], "ing_actualizados": [], "ing_iguales": 0,
           "rec_nuevas": [], "rec_actualizadas": [], "rec_iguales": 0,
           "lineas": [], "errores": errores, "omitidas": 0}
    hoy = date.today().isoformat()

    ingredientes = {normalizar(i["nombre"]): i for i in db.consultar("SELECT * FROM ingredientes")}
    recetas = {normalizar(r["nombre"]): r for r in db.consultar("SELECT * FROM recetas")}
    items = db.consultar("SELECT * FROM receta_items")
    usados = {it["ingrediente_id"] for it in items if it["ingrediente_id"]}
    temporal = [0]

    def nuevo_id():
        temporal[0] -= 1
        return temporal[0]

    # ---------- ingredientes
    vistos = set()
    for r in datos.get("ingredientes", []):
        hoja, fila = "Ingredientes", r["fila"]
        nombre = limpiar_nombre(r.get("nombre"))
        if normalizar(r.get("tipo")).startswith("subreceta"):
            continue  # en la planilla anterior las subrecetas también figuraban como ingrediente
        try:
            cant = numero(r.get("presentacion"))
            precio = numero(r.get("precio"))
        except ValueError as e:
            errores.append((hoja, fila, str(e)))
            continue
        u = unidad(r.get("unidad"))
        if not u:
            errores.append((hoja, fila, f"«{nombre}»: unidad «{r.get('unidad') or ''}» no válida (usá g, kg, ml, l o u)"))
            continue
        if not cant or cant <= 0:
            errores.append((hoja, fila, f"«{nombre}»: falta la presentación (cuánto trae lo que comprás)"))
            continue
        if precio is None or precio < 0:
            errores.append((hoja, fila, f"«{nombre}»: falta el precio"))
            continue
        clave = normalizar(nombre)
        if clave in vistos:
            errores.append((hoja, fila, f"«{nombre}» está repetido en el Excel; se usa la primera fila"))
            continue
        vistos.add(clave)
        notas = limpiar_nombre(r.get("notas")) or None
        actual = ingredientes.get(clave)
        if actual:
            if costeo.unidad_base(u) != costeo.unidad_base(actual["presentacion_unidad"]) and actual["id"] in usados:
                errores.append((hoja, fila, f"«{nombre}»: no se puede pasar de {costeo.unidad_base(actual['presentacion_unidad'])} "
                                            f"a {costeo.unidad_base(u)} porque ya se usa en recetas"))
                continue
            cambio_precio = (cant, u, precio) != (actual["presentacion_cantidad"], actual["presentacion_unidad"], actual["precio"])
            cambio = cambio_precio or (notas is not None and notas != (actual.get("notas") or None)) or nombre != actual["nombre"]
            if not cambio:
                res["ing_iguales"] += 1
                continue
            res["ing_actualizados"].append(nombre)
            if aplicar:
                db.ejecutar("UPDATE ingredientes SET nombre=?, presentacion_cantidad=?, presentacion_unidad=?, precio=?, "
                            "fecha_precio=?, notas=? WHERE id=?",
                            (nombre, cant, u, precio, hoy if cambio_precio else actual["fecha_precio"],
                             notas if notas is not None else actual.get("notas"), actual["id"]))
                if cambio_precio:
                    _historial(actual["id"], cant, u, precio, hoy)
            actual.update(nombre=nombre, presentacion_cantidad=cant, presentacion_unidad=u, precio=precio)
        else:
            res["ing_nuevos"].append(nombre)
            iid = nuevo_id()
            if aplicar:
                iid = db.ejecutar("INSERT INTO ingredientes (nombre, presentacion_cantidad, presentacion_unidad, precio, "
                                  "fecha_precio, notas) VALUES (?, ?, ?, ?, ?, ?)",
                                  (nombre, cant, u, precio, hoy, notas), devolver_id=True)
                _historial(iid, cant, u, precio, hoy)
            ingredientes[clave] = {"id": iid, "nombre": nombre, "presentacion_cantidad": cant,
                                   "presentacion_unidad": u, "precio": precio}

    # ---------- productos y subrecetas
    vistos = set()
    for r in datos.get("productos", []):
        hoja, fila = "Productos", r["fila"]
        nombre = limpiar_nombre(r.get("nombre"))
        tipo_txt = normalizar(r.get("tipo"))
        tipo = "subreceta" if tipo_txt.startswith("sub") else "venta"
        try:
            rinde = numero(r.get("rinde"))
            mult = numero(r.get("multiplicador"))
            real = numero(r.get("precio_real"))
        except ValueError as e:
            errores.append((hoja, fila, str(e)))
            continue
        u = unidad(r.get("unidad") or "u", UNIDADES_RINDE_ALIAS)
        if not u:
            errores.append((hoja, fila, f"«{nombre}»: unidad del rinde «{r.get('unidad')}» no válida"))
            continue
        if tipo == "subreceta" and u not in costeo.UNIDADES_RINDE_SUBRECETA:
            if u == "kg" and rinde:
                rinde, u = rinde * 1000, "g"     # una subreceta que rinde 1,2 kg -> 1200 g
            else:
                errores.append((hoja, fila, f"«{nombre}»: una subreceta tiene que rendir en g, ml o u"))
                continue
        if not rinde or rinde <= 0:
            errores.append((hoja, fila, f"«{nombre}»: falta el rinde de la tanda"))
            continue
        clave = normalizar(nombre)
        if clave in vistos:
            errores.append((hoja, fila, f"«{nombre}» está repetido en el Excel; se usa la primera fila"))
            continue
        vistos.add(clave)
        actual = recetas.get(clave)
        mult = mult if mult is not None else (actual["multiplicador"] if actual else (3 if tipo == "venta" else 0))
        real = real if (real and tipo == "venta") else None
        notas = limpiar_nombre(r.get("notas")) or None
        if actual:
            if actual["tipo"] == "subreceta" and tipo == "venta" and any(
                    it["subreceta_id"] == actual["id"] for it in items):
                errores.append((hoja, fila, f"«{nombre}» se usa como subreceta en otras recetas: no puede pasar a Venta"))
                continue
            nuevos_valores = (nombre, tipo, rinde, u, mult, real)
            viejos = (actual["nombre"], actual["tipo"], actual["rinde"], actual["unidad_rinde"],
                      actual["multiplicador"], actual["precio_venta_real"])
            if nuevos_valores == viejos and (notas is None or notas == actual.get("notas")):
                res["rec_iguales"] += 1
                continue
            res["rec_actualizadas"].append(nombre)
            if aplicar:
                db.ejecutar("UPDATE recetas SET nombre=?, tipo=?, rinde=?, unidad_rinde=?, multiplicador=?, "
                            "precio_venta_real=?, notas=? WHERE id=?",
                            nuevos_valores + (notas if notas is not None else actual.get("notas"), actual["id"]))
            actual.update(nombre=nombre, tipo=tipo, rinde=rinde, unidad_rinde=u)
        else:
            res["rec_nuevas"].append(nombre)
            rid = nuevo_id()
            if aplicar:
                rid = db.ejecutar("INSERT INTO recetas (nombre, tipo, rinde, unidad_rinde, multiplicador, "
                                  "precio_venta_real, notas) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                  (nombre, tipo, rinde, u, mult, real, notas), devolver_id=True)
            recetas[clave] = {"id": rid, "nombre": nombre, "tipo": tipo, "rinde": rinde, "unidad_rinde": u}

    # ---------- líneas de receta (se reemplazan completas)
    grupos = OrderedDict()
    for r in datos.get("recetas", []):
        grupos.setdefault(normalizar(r.get("receta")), []).append(r)

    planificadas = {}
    for clave, filas in grupos.items():
        receta = recetas.get(clave)
        nombre_receta = limpiar_nombre(filas[0].get("receta"))
        if not receta:
            errores.append(("Recetas", filas[0]["fila"], f"La receta «{nombre_receta}» no existe: agregala en la hoja Productos"))
            res["omitidas"] += 1
            continue
        lineas, mal = [], False
        for r in filas:
            comp = limpiar_nombre(r.get("componente"))
            try:
                cant = numero(r.get("cantidad"))
            except ValueError as e:
                errores.append(("Recetas", r["fila"], str(e)))
                mal = True
                continue
            if not cant or cant <= 0:
                errores.append(("Recetas", r["fila"], f"«{comp}» en «{nombre_receta}»: falta la cantidad"))
                mal = True
                continue
            ing = ingredientes.get(normalizar(comp))
            sub = recetas.get(normalizar(comp))
            sub = sub if sub and sub["tipo"] == "subreceta" else None
            if ing and sub:
                errores.append(("Recetas", r["fila"], f"«{comp}» es a la vez ingrediente y subreceta: renombrá uno"))
                mal = True
            elif sub and sub["id"] == receta["id"]:
                errores.append(("Recetas", r["fila"], f"«{nombre_receta}» no puede llevarse a sí misma"))
                mal = True
            elif ing:
                lineas.append((ing["id"], None, cant))
            elif sub:
                lineas.append((None, sub["id"], cant))
            else:
                errores.append(("Recetas", r["fila"], f"«{comp}» (en «{nombre_receta}») no existe como ingrediente ni subreceta"))
                mal = True
        if mal:
            errores.append(("Recetas", None, f"La receta «{nombre_receta}» no se modificó porque tiene filas con error"))
            res["omitidas"] += 1
            continue
        planificadas[receta["id"]] = (nombre_receta, lineas)

    # ciclos: una subreceta que termina conteniéndose a sí misma
    grafo = [it for it in items if it["receta_id"] not in planificadas]
    for rid, (_, lineas) in planificadas.items():
        grafo += [{"receta_id": rid, "subreceta_id": s} for _, s, _ in lineas if s]
    for rid, (nombre_receta, lineas) in list(planificadas.items()):
        otros = [g for g in grafo if not (g["receta_id"] == rid)]
        if any(s and costeo.crearia_ciclo(rid, s, otros) for _, s, _ in lineas):
            errores.append(("Recetas", None, f"«{nombre_receta}» forma un ciclo con sus subrecetas: no se modificó"))
            res["omitidas"] += 1
            del planificadas[rid]

    res["lineas_iguales"] = 0
    for rid, (nombre_receta, lineas) in planificadas.items():
        actuales = [(it["ingrediente_id"], it["subreceta_id"], it["cantidad"])
                    for it in sorted((it for it in items if it["receta_id"] == rid), key=lambda x: (x["orden"], x["id"]))]
        if actuales == lineas:
            res["lineas_iguales"] += 1
            continue
        res["lineas"].append((nombre_receta, len(lineas)))
        if aplicar:
            db.ejecutar("DELETE FROM receta_items WHERE receta_id = ?", (rid,))
            for orden, (iid, sid, cant) in enumerate(lineas, start=1):
                db.ejecutar("INSERT INTO receta_items (receta_id, ingrediente_id, subreceta_id, cantidad, orden) "
                            "VALUES (?, ?, ?, ?, ?)", (rid, iid, sid, cant, orden))
    res["hay_cambios"] = bool(res["ing_nuevos"] or res["ing_actualizados"] or res["rec_nuevas"]
                              or res["rec_actualizadas"] or res["lineas"])
    return res


def _historial(iid, cant, u, precio, fecha):
    db.ejecutar("INSERT INTO precios_historial (ingrediente_id, fecha, presentacion_cantidad, presentacion_unidad, "
                "precio, origen) VALUES (?, ?, ?, ?, ?, 'excel')", (iid, fecha, cant, u, precio))


def importar(datos):
    with db.lote():
        return procesar(datos, aplicar=True)


# ------------------------------------------------------------------ exportar

def exportar(vacio=False):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    cabecera = Font(name="Arial", bold=True, color="FFFFFF")
    relleno = PatternFill("solid", fgColor="7A3E1D")
    normal = Font(name="Arial")
    wb = Workbook()

    def hoja(titulo, columnas, filas):
        ws = wb.create_sheet(titulo)
        for i, (nombre, ancho) in enumerate(columnas, start=1):
            c = ws.cell(row=1, column=i, value=nombre)
            c.font, c.fill = cabecera, relleno
            c.alignment = Alignment(vertical="center")
            ws.column_dimensions[c.column_letter].width = ancho
        for fila in filas:
            ws.append(list(fila))
        for fila in ws.iter_rows(min_row=2):
            for c in fila:
                c.font = normal
        ws.freeze_panes = "A2"
        return ws

    if vacio:
        ingredientes, recetas, lineas = [], [], []
    else:
        ingredientes = db.consultar("SELECT * FROM ingredientes ORDER BY nombre")
        recetas = db.consultar("SELECT * FROM recetas ORDER BY tipo DESC, nombre")
        lineas = db.consultar(
            "SELECT r.nombre AS receta, COALESCE(i.nombre, s.nombre) AS componente, it.cantidad "
            "FROM receta_items it JOIN recetas r ON r.id = it.receta_id "
            "LEFT JOIN ingredientes i ON i.id = it.ingrediente_id LEFT JOIN recetas s ON s.id = it.subreceta_id "
            "ORDER BY r.nombre, it.orden, it.id")

    ins = wb.active
    ins.title = "Instrucciones"
    ins.column_dimensions["A"].width = 110
    textos = [
        ("ArteSana · lista de materiales", True),
        ("Editá las hojas y subí el archivo en la app: Ingredientes → Importar Excel. Antes de aplicar vas a ver qué cambia.", False),
        ("", False),
        ("Ingredientes: lo que comprás. Presentación + Unidad + Precio = cómo lo comprás (ej.: 1 | kg | 6000).", False),
        ("   Unidades: g, kg, ml, l o u (unidades). En las recetas se usan en g, ml o u.", False),
        ("Productos: cada receta es UNA TANDA. Tipo = Venta o Subreceta (rellenos y bases que se usan en otras recetas).", False),
        ("   Rinde = lo que contás o pesás al terminar. Una subreceta rinde en g, ml o u. Multiplicador vacío = 3.", False),
        ("Recetas: una fila por ingrediente de cada receta, con la cantidad en g, ml o u.", False),
        ("   Si una receta aparece en esta hoja, sus ingredientes se REEMPLAZAN por los del Excel.", False),
        ("   Las recetas que no aparecen en esta hoja quedan como están.", False),
        ("", False),
        ("Se busca por nombre (sin importar mayúsculas ni tildes): si ya existe se actualiza, si no se crea.", False),
        ("Nada se borra desde el Excel: para borrar, usá la app.", False),
    ]
    for i, (t, negrita) in enumerate(textos, start=1):
        c = ins.cell(row=i, column=1, value=t)
        c.font = Font(name="Arial", bold=negrita, size=13 if negrita else 10)

    ws_i = hoja("Ingredientes", [("Ingrediente", 30), ("Presentación", 14), ("Unidad", 10), ("Precio (Gs)", 14), ("Notas", 34)],
                [(i["nombre"], i["presentacion_cantidad"], i["presentacion_unidad"], i["precio"], i.get("notas"))
                 for i in ingredientes])
    ws_p = hoja("Productos", [("Receta", 30), ("Tipo", 12), ("Rinde", 10), ("Unidad del rinde", 16), ("Multiplicador", 14),
                              ("Precio de venta real", 20), ("Notas", 34)],
                [(r["nombre"], "Venta" if r["tipo"] == "venta" else "Subreceta", r["rinde"], r["unidad_rinde"],
                  r["multiplicador"] if r["tipo"] == "venta" else None, r["precio_venta_real"], r.get("notas"))
                 for r in recetas])
    ws_r = hoja("Recetas", [("Receta", 30), ("Ingrediente o subreceta", 30), ("Cantidad", 12)],
                [(l["receta"], l["componente"], l["cantidad"]) for l in lineas])

    for ws, rango, lista in ((ws_i, "C2:C2000", '"g,kg,ml,l,u"'), (ws_p, "B2:B500", '"Venta,Subreceta"'),
                             (ws_p, "D2:D500", '"u,porción,caja,kg,g,ml"')):
        dv = DataValidation(type="list", formula1=lista, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(rango)
    for ws, col in ((ws_i, "D"), (ws_p, "F")):
        for c in ws[col][1:]:
            c.number_format = "#,##0"

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()
