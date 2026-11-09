"""Lógica de costeo de ArteSana (sin dependencias de Flask ni de la base de datos).

Reglas:
- Cada ingrediente se compra en una presentación (ej. 1 kg a 6.000 Gs) y se usa en
  su unidad base: g, ml o u. Precio por unidad base = precio / cantidad en unidad base.
- Cada receta describe UNA TANDA. Sus líneas son ingredientes o subrecetas con una
  cantidad en la unidad base correspondiente.
- Costo por unidad = costo de la tanda / rinde (lo que se cuenta o pesa al terminar).
- Una subreceta (relleno, base) rinde en g, ml o u y se usa como ingrediente en otras.
- Recetas de venta: precio sugerido = costo por unidad x multiplicador, redondeado hacia
  arriba. Si se carga un precio de venta real, se usa ese para la ganancia.
"""
import math
from collections import defaultdict

# unidad de presentación -> (unidad base, factor)
UNIDADES_PRESENTACION = {
    "g": ("g", 1),
    "kg": ("g", 1000),
    "ml": ("ml", 1),
    "l": ("ml", 1000),
    "u": ("u", 1),
}
UNIDADES_BASE = ("g", "ml", "u")
UNIDADES_RINDE_VENTA = ("u", "porción", "caja", "kg", "g", "ml")
UNIDADES_RINDE_SUBRECETA = UNIDADES_BASE
TIPOS_RECETA = ("venta", "subreceta")


def unidad_base(unidad_presentacion):
    return UNIDADES_PRESENTACION.get(unidad_presentacion, ("u", 1))[0]


def cantidad_en_base(cantidad, unidad_presentacion):
    factor = UNIDADES_PRESENTACION.get(unidad_presentacion, ("u", 1))[1]
    return (cantidad or 0) * factor


def precio_por_unidad_base(ingrediente):
    base = cantidad_en_base(ingrediente.get("presentacion_cantidad"),
                            ingrediente.get("presentacion_unidad"))
    precio = ingrediente.get("precio") or 0
    if base > 0 and precio > 0:
        return precio / base
    return 0.0


def redondear_arriba(valor, multiplo):
    if valor is None:
        return None
    multiplo = multiplo if multiplo and multiplo > 0 else 1
    # round() evita que errores de coma flotante suban un escalón de más
    return math.ceil(round(valor / multiplo, 9)) * multiplo


def calcular(ingredientes, recetas, items, redondeo=100):
    """Calcula costos de todas las recetas.

    ingredientes: {id: {nombre, presentacion_cantidad, presentacion_unidad, precio}}
    recetas: {id: {nombre, tipo, rinde, unidad_rinde, multiplicador, precio_venta_real}}
    items: [{id, receta_id, ingrediente_id, subreceta_id, cantidad}]
    Devuelve {"ingredientes": {id: precio_unitario}, "recetas": {id: resultado}}
    """
    precios = {iid: precio_por_unidad_base(i) for iid, i in ingredientes.items()}

    items_por_receta = defaultdict(list)
    for it in items:
        items_por_receta[it["receta_id"]].append(it)

    resultados = {}
    visitando = set()

    def costo_receta(rid):
        if rid in resultados:
            return resultados[rid]
        if rid in visitando:
            return None  # ciclo: una receta que se contiene a sí misma
        visitando.add(rid)
        r = recetas[rid]
        lineas, alertas = [], []
        total = 0.0

        for it in items_por_receta[rid]:
            cantidad = it.get("cantidad") or 0
            if it.get("ingrediente_id"):
                ing = ingredientes.get(it["ingrediente_id"], {})
                nombre = ing.get("nombre", "¿?")
                unidad = unidad_base(ing.get("presentacion_unidad"))
                pu = precios.get(it["ingrediente_id"], 0.0)
                origen = "ingrediente"
                if pu == 0:
                    alertas.append(f"«{nombre}» no tiene precio cargado")
            else:
                sub = recetas.get(it["subreceta_id"], {})
                nombre = sub.get("nombre", "¿?")
                unidad = sub.get("unidad_rinde", "u")
                origen = "subreceta"
                res_sub = costo_receta(it["subreceta_id"])
                if res_sub is None:
                    pu = 0.0
                    alertas.append(f"«{nombre}» forma un ciclo (se usa a sí misma)")
                else:
                    pu = res_sub["costo_unitario"]
                    if res_sub["alertas"]:
                        alertas.append(f"La subreceta «{nombre}» tiene avisos")
            if cantidad <= 0:
                alertas.append(f"«{nombre}» tiene cantidad 0")
            costo = cantidad * pu
            total += costo
            lineas.append({
                "item_id": it.get("id"),
                "origen": origen,
                "ingrediente_id": it.get("ingrediente_id"),
                "subreceta_id": it.get("subreceta_id"),
                "nombre": nombre,
                "cantidad": cantidad,
                "unidad": unidad,
                "precio_unitario": pu,
                "costo": costo,
            })

        for ln in lineas:
            ln["porcentaje"] = (ln["costo"] / total) if total > 0 else 0

        rinde = r.get("rinde") or 0
        costo_unitario = total / rinde if rinde > 0 else 0.0
        if rinde <= 0:
            alertas.append("Falta el rinde de la tanda")
        if not lineas:
            alertas.append("No tiene ingredientes")

        res = {
            "costo_tanda": total,
            "costo_unitario": costo_unitario,
            "lineas": lineas,
            "alertas": alertas,
            "precio_sugerido": None,
            "precio_sugerido_redondeado": None,
            "precio_venta": None,
            "ganancia": None,
            "porcentaje_ingredientes": None,
            "multiplicador_efectivo": None,
        }
        if r.get("tipo") == "venta":
            mult = r.get("multiplicador") or 0
            if mult <= 0:
                alertas.append("Falta el multiplicador")
            sugerido = costo_unitario * mult
            redondeado = redondear_arriba(sugerido, redondeo)
            real = r.get("precio_venta_real")
            precio = real if real and real > 0 else redondeado
            res.update({
                "precio_sugerido": sugerido,
                "precio_sugerido_redondeado": redondeado,
                "precio_venta": precio,
                "ganancia": precio - costo_unitario,
                "porcentaje_ingredientes": (costo_unitario / precio) if precio else None,
                "multiplicador_efectivo": (precio / costo_unitario) if costo_unitario else None,
            })
            if real and real > 0 and real < costo_unitario:
                alertas.append("El precio de venta real es menor que el costo")

        visitando.discard(rid)
        resultados[rid] = res
        return res

    for rid in recetas:
        costo_receta(rid)

    return {"ingredientes": precios, "recetas": resultados}


def crearia_ciclo(receta_id, subreceta_id, items):
    """True si agregar subreceta_id dentro de receta_id genera un ciclo."""
    if receta_id == subreceta_id:
        return True
    hijos = defaultdict(set)
    for it in items:
        if it.get("subreceta_id"):
            hijos[it["receta_id"]].add(it["subreceta_id"])
    # ¿receta_id es alcanzable desde subreceta_id?
    pila, vistos = [subreceta_id], set()
    while pila:
        actual = pila.pop()
        if actual == receta_id:
            return True
        if actual in vistos:
            continue
        vistos.add(actual)
        pila.extend(hijos[actual])
    return False
