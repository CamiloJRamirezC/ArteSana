"""Pruebas del lector de Supermás (con el HTML real recortado) y del flujo de precio recomendado."""
import os
import re
import sqlite3
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import fuentes  # noqa: E402
from app import crear_app  # noqa: E402

HTML = open(os.path.join(RAIZ, "tests", "datos", "supermas_harina.html"), encoding="utf-8").read()


# ------------------------------------------------------------ lector

def test_lee_el_listado_real():
    productos, total = fuentes.Supermas().leer_listado(HTML)
    assert total == 60
    por_id = {p.id: p for p in productos}
    assert list(por_id) == ["503", "11328", "4819", "2964"]

    oferta = por_id["503"]
    assert oferta.nombre == "HARINA 000 HILDEBRAND 500 GR"
    assert (oferta.precio, oferta.precio_oferta) == (3000, 2700)       # normal = el tachado
    assert (oferta.presentacion, oferta.unidad, oferta.unidad_base) == (500, "g", "g")
    assert oferta.precio_unitario == pytest.approx(6.0)
    assert oferta.url == "https://www.supermas.com.py/harina-000-hildebrand-500-gr-p503"

    normal = por_id["11328"]
    assert (normal.precio, normal.precio_oferta, normal.presentacion, normal.unidad) == (12100, None, 1, "kg")
    assert normal.precio_unitario == pytest.approx(12.1)

    granel = por_id["4819"]
    assert granel.por_kilo and (granel.presentacion, granel.unidad) == (1, "kg")
    assert granel.etiqueta == "Descuentos a partir de 50 Kg"
    assert por_id["2964"].precio == 4300 and por_id["2964"].precio_oferta == 3655


@pytest.mark.parametrize("nombre,esperado", [
    ("HARINA 000 HILDEBRAND 500 GR", (500, "g")),
    ("HARINA DE COCO WR 250GR.", (250, "g")),
    ("HARINA SOL BLANCA 000 5KG", (5, "kg")),
    ("HARINA TIPO 0000 HILDEBRAND 1KG", (1, "kg")),
    ("ACEITE NATURA 900ML", (900, "ml")),
    ("LECHE ENTERA 1 LT", (1, "l")),
    ("CREMA DE LECHE 200 CC", (200, "ml")),
    ("AZUCAR 1,5 KG", (1.5, "kg")),
    ("GALLETITA 3 X 118 GR", (354, "g")),
    ("HUEVO BLANCO X 30", (30, "u")),
    ("CAJITA 12 UNIDADES", (12, "u")),
    ("HARINA GRANEL KG", (1, "kg")),
    ("MAPLE DE HUEVO", (None, None)),
])
def test_presentacion_del_nombre(nombre, esperado):
    assert fuentes.presentacion_del_nombre(nombre) == esperado


def test_pagina_cambiada_avisa(monkeypatch):
    sm = fuentes.Supermas()
    monkeypatch.setattr(sm, "_descargar", lambda url: "<html><body>Mantenimiento</body></html>")
    with pytest.raises(fuentes.ErrorDeFuente, match="cambió"):
        sm.buscar("harina")


def test_busca_dos_paginas_con_pausa(monkeypatch):
    sm = fuentes.Supermas()
    pedidas = []
    monkeypatch.setattr(sm, "_descargar", lambda url: pedidas.append(url) or HTML)
    monkeypatch.setattr(fuentes, "PAUSA_ENTRE_CONSULTAS", 0)
    productos, total = sm.buscar("harina 000")
    assert pedidas == ["https://www.supermas.com.py/productos?q=harina+000&post_type=product",
                       "https://www.supermas.com.py/productos.2?q=harina+000&post_type=product"]
    assert total == 60 and len(productos) == 4          # la 2ª página trajo los mismos: no se repiten


# ------------------------------------------------------------ flujo en la app

@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(fuentes, "PAUSA_ENTRE_CONSULTAS", 0)
    precios = {"html": HTML}
    monkeypatch.setattr(fuentes.Supermas, "_descargar", lambda self, url: precios["html"])
    app = crear_app({"DATABASE_URL": f"sqlite:///{tmp_path / 't.db'}", "APP_PASSWORD": ""})
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    c.precios = precios
    return c


def post(c, url, **datos):
    datos["csrf"] = "tok"
    return c.post(url, data=datos, follow_redirects=True)


def test_nuevo_ingrediente_con_precio_recomendado(cliente):
    html = cliente.get("/super/buscar?q=harina&nombre=Harina 000").get_data(as_text=True)
    assert "HARINA 000 HILDEBRAND 500 GR" in html and "oferta 2.700" in html
    assert "más barato por g" in html          # granel 4 Gs/g es el más barato
    elegir = re.search(r'href="(/ingredientes/nuevo\?[^"]*sup_id=503[^"]*)"', html).group(1).replace("&amp;", "&")

    form = cliente.get(elegir).get_data(as_text=True)
    assert "Precio recomendado por Supermás" in form and "Revisalo y tocá Guardar" in form
    assert 'value="3.000"' in form and 'value="500"' in form and 'value="Harina 000"' in form
    assert "/ingredientes" in cliente.get("/ingredientes").get_data(as_text=True)
    assert "Harina 000" not in cliente.get("/ingredientes").get_data(as_text=True)   # todavía no se guardó

    # el usuario elige la oferta (manual) y guarda
    campos = dict(re.findall(r'name="(sup_\w+)" value="([^"]*)"', form))
    r = post(cliente, "/ingredientes/nuevo", nombre="Harina 000", presentacion_cantidad="500",
             presentacion_unidad="g", precio="2.700", notas="", **campos)
    assert "vinculado a Supermás" in r.get_data(as_text=True)
    ficha = cliente.get("/ingredientes/1").get_data(as_text=True)
    assert "HARINA 000 HILDEBRAND 500 GR" in ficha and "supermas" in ficha     # historial con origen
    assert "3.000 Gs" in ficha                                                  # último recomendado


def test_vincular_existente_consultar_y_aplicar(cliente):
    post(cliente, "/ingredientes/nuevo", nombre="Harina 000", presentacion_cantidad="1",
         presentacion_unidad="kg", precio="5.000", notas="")
    # elegir desde la ficha: mantiene el nombre del usuario y muestra el precio anterior
    html = cliente.get("/super/buscar?q=harina&iid=1").get_data(as_text=True)
    assert "Tu precio actual" in html
    elegir = re.search(r'href="(/ingredientes/1\?[^"]*sup_id=2964[^"]*)"', html).group(1).replace("&amp;", "&")
    form = cliente.get(elegir).get_data(as_text=True)
    assert "Antes: 5.000 Gs" in form and 'value="4.300"' in form
    campos = dict(re.findall(r'name="(sup_\w+)" value="([^"]*)"', form))
    post(cliente, "/ingredientes/1", nombre="Harina 000", presentacion_cantidad="1", presentacion_unidad="kg",
         precio="4.300", notas="", **campos)
    assert "HARINA TRIGO HILDEBRAND 000 1 KG" in cliente.get("/ingredientes/1").get_data(as_text=True)

    # el súper subió el precio: la consulta lo recomienda, pero no lo guarda
    cliente.precios["html"] = HTML.replace("₲. 4.300", "₲. 4.730")
    assert cliente.get("/ingredientes/super").status_code == 200
    r = cliente.post("/ingredientes/1/super/consultar", headers={"X-CSRF-Token": "tok"}).get_json()
    assert r["encontrado"] and r["producto"]["precio"] == 4730 and r["producto"]["precio_oferta"] == 3655
    assert r["variacion"] == pytest.approx(0.10)
    lista = cliente.get("/ingredientes").get_data(as_text=True)
    assert "4.300" in lista and "4.730" in lista and "▲" in lista               # tu precio sigue igual

    # el usuario confirma (podría haber escrito otro precio)
    r = cliente.post("/ingredientes/super/aplicar", headers={"X-CSRF-Token": "tok"},
                     json={"items": [{"id": 1, "precio": "4.730", "presentacion": 1, "unidad": "kg"}]})
    assert r.get_json() == {"ok": True, "actualizados": 1}
    assert "4.730" in cliente.get("/ingredientes/1").get_data(as_text=True)

    # si cambia solo el código, lo reconoce por el nombre exacto
    cliente.precios["html"] = HTML.replace("-p2964", "-p9999")
    assert cliente.post("/ingredientes/1/super/consultar", headers={"X-CSRF-Token": "tok"}).get_json()["encontrado"]
    # producto que ya no aparece (otro código y otro nombre)
    cliente.precios["html"] = HTML.replace("-p2964", "-p9999").replace("HARINA TRIGO HILDEBRAND 000 1 KG", "OTRA COSA")
    r = cliente.post("/ingredientes/1/super/consultar", headers={"X-CSRF-Token": "tok"}).get_json()
    assert r["encontrado"] is False

    # desvincular deja el precio como estaba
    post(cliente, "/ingredientes/1/desvincular")
    ficha = cliente.get("/ingredientes/1").get_data(as_text=True)
    assert "Buscar en Supermás" in ficha and "4.730" in ficha


def test_aplicar_rechaza_otra_unidad_si_se_usa_en_recetas(cliente):
    post(cliente, "/ejemplo")
    # Huevo (u) usado en recetas, vinculado a un producto en kg -> no se puede pasar a kg
    r = cliente.post("/ingredientes/super/aplicar", headers={"X-CSRF-Token": "tok"},
                     json={"items": [{"id": 5, "precio": "4000", "presentacion": 1, "unidad": "kg"}]})
    assert r.status_code == 400 and "otra unidad" in r.get_json()["errores"][0]["mensaje"]


def test_sin_conexion_y_probar(cliente, monkeypatch):
    def caido(self, url):
        raise fuentes.ErrorDeFuente("No se pudo conectar con Supermás (URLError). ¿Hay internet?")
    html = post(cliente, "/super/probar").get_data(as_text=True)
    assert "Conexión con Supermás OK: 60 resultados" in html
    monkeypatch.setattr(fuentes.Supermas, "_descargar", caido)
    assert "No se pudo conectar" in cliente.get("/super/buscar?q=harina").get_data(as_text=True)
    assert "No se pudo conectar" in post(cliente, "/super/probar").get_data(as_text=True)


def test_url_ajena_no_se_guarda(cliente):
    form = cliente.get("/ingredientes/nuevo?sup_id=1&sup_nombre=X&sup_url=https://malo.example/x&sup_precio=10").get_data(as_text=True)
    assert "malo.example" not in form


def test_migra_una_base_vieja(tmp_path):
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE ingredientes (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE, "
                "presentacion_cantidad DOUBLE PRECISION NOT NULL DEFAULT 0, presentacion_unidad TEXT NOT NULL DEFAULT 'g', "
                "precio DOUBLE PRECISION NOT NULL DEFAULT 0, fecha_precio TEXT, notas TEXT)")
    con.execute("INSERT INTO ingredientes (nombre, presentacion_cantidad, presentacion_unidad, precio) VALUES ('Sal', 1, 'kg', 3000)")
    con.commit()
    con.close()
    app = crear_app({"DATABASE_URL": f"sqlite:///{ruta}"})
    c = app.test_client()
    assert "Sal" in c.get("/ingredientes").get_data(as_text=True)
    columnas = {f[1] for f in sqlite3.connect(ruta).execute("PRAGMA table_info(ingredientes)")}
    assert {"fuente", "fuente_id", "fuente_precio", "fuente_oferta", "fuente_fecha"} <= columnas
