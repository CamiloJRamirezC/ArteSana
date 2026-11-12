import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import costeo  # noqa: E402
from app import crear_app  # noqa: E402
from utils import parse_num  # noqa: E402


# ------------------------------------------------------------ cálculo puro

def datos_ejemplo():
    ing = {
        1: dict(nombre="Leche entera", presentacion_cantidad=1, presentacion_unidad="l", precio=8000),
        2: dict(nombre="Azúcar", presentacion_cantidad=1, presentacion_unidad="kg", precio=7000),
        3: dict(nombre="Bicarbonato", presentacion_cantidad=100, presentacion_unidad="g", precio=3000),
        4: dict(nombre="Vainilla", presentacion_cantidad=100, presentacion_unidad="ml", precio=10000),
        5: dict(nombre="Maicena", presentacion_cantidad=1, presentacion_unidad="kg", precio=14000),
        6: dict(nombre="Harina", presentacion_cantidad=1, presentacion_unidad="kg", precio=6000),
        7: dict(nombre="Manteca", presentacion_cantidad=200, presentacion_unidad="g", precio=10000),
        8: dict(nombre="Huevo", presentacion_cantidad=30, presentacion_unidad="u", precio=30000),
        9: dict(nombre="Polvo", presentacion_cantidad=100, presentacion_unidad="g", precio=4000),
        10: dict(nombre="Coco", presentacion_cantidad=100, presentacion_unidad="g", precio=6000),
        11: dict(nombre="Bolsita", presentacion_cantidad=100, presentacion_unidad="u", precio=50000),
    }
    rec = {
        1: dict(nombre="DDL", tipo="subreceta", rinde=1000, unidad_rinde="g", multiplicador=0, precio_venta_real=None),
        2: dict(nombre="Alfajores", tipo="venta", rinde=30, unidad_rinde="u", multiplicador=3, precio_venta_real=None),
    }
    lineas = [(1, 1, 2000), (1, 2, 600), (1, 3, 2), (1, 4, 5),
              (2, 5, 300), (2, 6, 200), (2, 7, 200), (2, 2, 150), (2, 8, 3), (2, 9, 10), (2, 4, 5),
              (2, 10, 50), (2, 11, 30)]
    items = [dict(id=n, receta_id=r, ingrediente_id=i, subreceta_id=None, cantidad=c)
             for n, (r, i, c) in enumerate(lineas, 1)]
    items.append(dict(id=99, receta_id=2, ingrediente_id=None, subreceta_id=1, cantidad=600))
    return ing, rec, items


def test_mismos_numeros_que_la_planilla():
    ing, rec, items = datos_ejemplo()
    r = costeo.calcular(ing, rec, items, redondeo=100)["recetas"]
    assert r[1]["costo_tanda"] == pytest.approx(20760)
    assert r[1]["costo_unitario"] == pytest.approx(20.76)
    assert r[2]["costo_tanda"] == pytest.approx(50806)
    assert r[2]["costo_unitario"] == pytest.approx(1693.5333, rel=1e-6)
    assert r[2]["precio_venta"] == 5100
    assert r[2]["ganancia"] == pytest.approx(5100 - 1693.5333, rel=1e-6)
    assert r[2]["alertas"] == []


def test_precio_real_y_alertas():
    ing, rec, items = datos_ejemplo()
    rec[2]["precio_venta_real"] = 1000
    r = costeo.calcular(ing, rec, items)["recetas"][2]
    assert r["precio_venta"] == 1000
    assert "El precio de venta real es menor que el costo" in r["alertas"]


def test_redondeo_sin_escalon_de_mas():
    assert costeo.redondear_arriba(5100.0000000001, 100) == 5100
    assert costeo.redondear_arriba(5080.6, 100) == 5100
    assert costeo.redondear_arriba(5080.6, 500) == 5500


def test_ciclos():
    items = [dict(receta_id=1, subreceta_id=2), dict(receta_id=2, subreceta_id=3)]
    assert costeo.crearia_ciclo(3, 1, items)
    assert costeo.crearia_ciclo(1, 1, items)
    assert not costeo.crearia_ciclo(1, 3, items)


def test_numeros_paraguayos():
    assert parse_num("1.500") == 1500
    assert parse_num("10.000 Gs") == 10000
    assert parse_num("2,4") == 2.4
    assert parse_num("1.234,5") == 1234.5
    assert parse_num("0.5") == 0.5
    with pytest.raises(ValueError):
        parse_num("abc")


# ------------------------------------------------------------ app web

@pytest.fixture
def cliente(tmp_path):
    app = crear_app({"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
                     "APP_PASSWORD": "clave123", "TESTING": True})
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    return c


def post(c, url, **datos):
    datos["csrf"] = "tok"
    return c.post(url, data=datos, follow_redirects=True)


def entrar(c):
    r = post(c, "/login", password="clave123")
    assert r.status_code == 200
    with c.session_transaction() as s:
        s["csrf"] = "tok"


def test_login_obligatorio(cliente):
    r = cliente.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert cliente.get("/salud").data == b"ok"
    r = post(cliente, "/login", password="mal")
    assert "Contraseña incorrecta" in r.get_data(as_text=True)


def test_csrf(cliente):
    entrar(cliente)
    r = cliente.post("/ejemplo", data={"csrf": "otro"})
    assert r.status_code == 400


def test_flujo_completo(cliente):
    entrar(cliente)
    html = cliente.get("/").get_data(as_text=True)
    assert "Cargar un ejemplo" in html

    html = post(cliente, "/ejemplo").get_data(as_text=True)
    assert "Alfajores de maicena" in html
    assert "5.100" in html and "50.806" in html and "1.694" in html

    # detalle de las dos recetas
    for rid in (1, 2):
        assert cliente.get(f"/recetas/{rid}").status_code == 200
    det = cliente.get("/recetas/2").get_data(as_text=True)
    assert "Dulce de leche casero" in det and "50.806" in det   # total de la tanda (servidor)

    # páginas de ingredientes y configuración
    assert "20,76" not in cliente.get("/ingredientes").get_data(as_text=True)  # subreceta no es ingrediente
    assert cliente.get("/ingredientes/1").status_code == 200
    assert cliente.get("/configuracion").status_code == 200
    assert cliente.get("/recetas/nueva").status_code == 200
    assert cliente.get("/recetas/2/editar").status_code == 200
    assert cliente.get("/ingredientes/nuevo").status_code == 200

    # nuevo ingrediente con formato paraguayo
    html = post(cliente, "/ingredientes/nuevo", nombre="Chocolate cobertura", presentacion_cantidad="1",
                presentacion_unidad="kg", precio="85.000").get_data(as_text=True)
    assert "Chocolate cobertura" in html and "85" in html

    # nuevo producto vendido por kg
    html = post(cliente, "/recetas/nueva", nombre="Pan dulce", tipo="venta", rinde="2,4",
                unidad_rinde="kg", multiplicador="3", notas="").get_data(as_text=True)
    assert "Pan dulce" in html
    rid = int(re.search(r'/recetas/(\d+)/grilla"', html).group(1))

    def grilla(rid, lineas):
        return cliente.post(f"/recetas/{rid}/grilla", json={"lineas": lineas}, headers={"X-CSRF-Token": "tok"})

    r = grilla(rid, [{"nombre": "chocolate COBERTURA", "cantidad": "300"}, {"nombre": "", "cantidad": ""}])
    assert r.get_json() == {"ok": True, "lineas": 1}
    html = cliente.get("/").get_data(as_text=True)
    # 300 g x 85 Gs/g = 25.500 / 2,4 kg = 10.625 por kg -> x3 = 31.875 -> 31.900
    assert "25.500" in html and "31.900" in html

    # la grilla reemplaza todo: cambiar cantidad y agregar otra línea
    r = grilla(rid, [{"nombre": "Chocolate cobertura", "cantidad": "600"}, {"nombre": "Huevo", "cantidad": "2"}])
    assert r.get_json()["lineas"] == 2
    assert "53.000" in cliente.get("/").get_data(as_text=True)    # 51.000 + 2 huevos

    # errores: no guarda nada y dice qué fila
    r = grilla(rid, [{"nombre": "Chocolate cobertura", "cantidad": "100"}, {"nombre": "Unicornio", "cantidad": "5"},
                     {"nombre": "Huevo", "cantidad": "abc"}])
    assert r.status_code == 400
    errores = r.get_json()["errores"]
    assert [(e["fila"], e["campo"]) for e in errores] == [(1, "nombre"), (2, "cantidad")]
    assert "53.000" in cliente.get("/").get_data(as_text=True)    # quedó como estaba

    # subreceta: no puede contenerse a sí misma
    r = grilla(1, [{"nombre": "Dulce de leche casero", "cantidad": "10"}])
    assert r.status_code == 400 and "ciclo" in r.get_json()["errores"][0]["mensaje"]
    # sin token CSRF no se acepta
    assert cliente.post(f"/recetas/{rid}/grilla", json={"lineas": []}).status_code == 400

    # no se borra lo que está en uso
    html = post(cliente, "/ingredientes/1/borrar").get_data(as_text=True)
    assert "No se puede borrar" in html
    html = post(cliente, "/recetas/1/borrar").get_data(as_text=True)
    assert "No se puede borrar" in html

    # cambio de precio -> historial y recálculo
    html = post(cliente, "/ingredientes/11", nombre="Bolsita para alfajor", presentacion_cantidad="100",
                presentacion_unidad="u", precio="60.000", notas="").get_data(as_text=True)
    hist = cliente.get("/ingredientes/11").get_data(as_text=True)
    assert "60.000" in hist and "50.000" in hist
    # 50.806 + 3.000 = 53.806 / 30 = 1.793,5 x3 = 5.380,6 -> 5.400
    assert "5.400" in cliente.get("/").get_data(as_text=True)

    # redondeo configurable
    post(cliente, "/configuracion", redondeo="500")
    assert "5.500" in cliente.get("/").get_data(as_text=True)

    # precio real
    html = post(cliente, "/recetas/2/editar", nombre="Alfajores de maicena", tipo="venta", rinde="30",
                unidad_rinde="u", multiplicador="3", precio_venta_real="6.000", notas="").get_data(as_text=True)
    assert "6.000" in html and "Precio real cargado" in html

    # duplicar y borrar
    html = post(cliente, "/recetas/2/duplicar").get_data(as_text=True)
    assert "(copia)" in html
    html = post(cliente, "/recetas/4/borrar").get_data(as_text=True)
    assert "borrada" in html

    # validaciones
    html = post(cliente, "/recetas/nueva", nombre="Mal", tipo="subreceta", rinde="1",
                unidad_rinde="kg").get_data(as_text=True)
    assert "g, ml o u" in html
    html = post(cliente, "/ingredientes/nuevo", nombre="X", presentacion_cantidad="0",
                presentacion_unidad="g", precio="1").get_data(as_text=True)
    assert "mayor a 0" in html
    html = post(cliente, "/ingredientes/nuevo", nombre="Maicena", presentacion_cantidad="1",
                presentacion_unidad="kg", precio="1").get_data(as_text=True)
    assert "Ya existe" in html

    # salir
    post(cliente, "/logout")
    assert cliente.get("/").status_code == 302


# ------------------------------------------------------------ app instalable y sistema

def test_pwa_sin_login(cliente):
    r = cliente.get("/sw.js")
    assert r.status_code == 200 and r.headers["Service-Worker-Allowed"] == "/"
    assert "javascript" in r.headers["Content-Type"]
    r = cliente.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.get_json()["short_name"] == "ArteSana"
    assert cliente.get("/offline").status_code == 200
    assert cliente.get("/static/iconos/icono-512.png").status_code == 200


def test_sistema_oculto_sin_servicio(cliente):
    entrar(cliente)
    assert "Sistema" not in cliente.get("/configuracion").get_data(as_text=True)
    assert cliente.get("/sistema/estado").status_code == 404
    assert post(cliente, "/sistema/reiniciar").status_code == 404


def test_sistema_con_servicio(tmp_path):
    import json
    control, estado = tmp_path / "control", tmp_path / "estado.json"
    estado.write_text(json.dumps({"servidor": "corriendo", "servidor_iniciado": "2026-11-09 10:00:00",
                                  "ultima_revision": "2026-11-09 10:01:00", "mensaje_actualizacion": "Al día con GitHub",
                                  "version": {"commit": "abc1234", "fecha": "2026-11-09 09:00", "mensaje": "prueba"}}))
    app = crear_app({"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}", "APP_PASSWORD": "",
                     "SERVICIO_CONTROL": str(control), "SERVICIO_ESTADO": str(estado)})
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    html = c.get("/configuracion").get_data(as_text=True)
    assert "abc1234" in html and "Al día con GitHub" in html
    assert c.get("/sistema/estado").get_json()["servidor"] == "corriendo"
    r = c.post("/sistema/reiniciar", data={"csrf": "tok"})
    assert r.status_code == 302 and (control / "reiniciar").exists()
    assert c.get(r.headers["Location"]).status_code == 200
    c.post("/sistema/actualizar", data={"csrf": "tok"})
    assert (control / "actualizar").exists()
    assert c.post("/sistema/borrar_todo", data={"csrf": "tok"}).status_code == 404


# ------------------------------------------------------------ grilla de ingredientes e importación

def test_grilla_de_ingredientes(cliente):
    entrar(cliente)
    post(cliente, "/ejemplo")
    assert cliente.get("/ingredientes/grilla").status_code == 200

    def guardar(filas):
        return cliente.post("/ingredientes/grilla", json={"filas": filas}, headers={"X-CSRF-Token": "tok"})

    r = guardar([{"indice": 0, "id": 1, "nombre": "Harina 000", "presentacion": "1", "unidad": "Kg", "precio": "6.500"},
                 {"indice": 20, "id": None, "nombre": "Chocolate", "presentacion": "1", "unidad": "kilos", "precio": "85.000"}])
    assert r.get_json() == {"ok": True, "nuevos": 1, "actualizados": 1}
    html = cliente.get("/ingredientes").get_data(as_text=True)
    assert "6.500" in html and "Chocolate" in html
    assert "6.500" in cliente.get("/ingredientes/1").get_data(as_text=True)      # quedó en el historial

    r = guardar([{"indice": 3, "id": None, "nombre": "harina 000", "presentacion": "1", "unidad": "kg", "precio": "1"},
                 {"indice": 4, "id": 5, "nombre": "Huevo", "presentacion": "30", "unidad": "kg", "precio": "1"},
                 {"indice": 5, "id": None, "nombre": "Sal", "presentacion": "1", "unidad": "taza", "precio": "1"}])
    assert r.status_code == 400
    assert [(e["fila"], e["campo"]) for e in r.get_json()["errores"]] == [(3, "nombre"), (4, "unidad"), (5, "unidad")]
    assert "<strong>Sal</strong>" not in cliente.get("/ingredientes").get_data(as_text=True)  # no guardó nada


def test_importar_y_exportar_excel(cliente):
    import io
    from openpyxl import Workbook
    entrar(cliente)
    post(cliente, "/ejemplo")

    # exportar y volver a importar sin cambios
    r = cliente.get("/exportar.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    html = cliente.post("/importar", data={"csrf": "tok", "archivo": (io.BytesIO(r.data), "a.xlsx")},
                        content_type="multipart/form-data").get_data(as_text=True)
    assert "No hay nada nuevo para importar" in html

    # Excel armado a mano, con encabezados y unidades "a mi manera"
    wb = Workbook()
    ws = wb.active
    ws.title = "ingredientes"
    ws.append(["INGREDIENTE", "Presentacion", "unidad", "Precio Gs", "Dónde compro"])
    ws.append(["Maicena", 500, "gr", "8.000", "Superseis"])           # actualiza (cambia de 1 kg a 500 g)
    ws.append(["Chocolate semiamargo", "1", "Kilo", 85000, None])     # nuevo
    ws.append(["Sal", 1, "taza", 1000, None])                         # error de unidad
    ws2 = wb.create_sheet("Productos")
    ws2.append(["Receta", "Tipo", "Rinde", "Unidad del rinde", "Multiplicador"])
    ws2.append(["Bombones", "venta", 4, "cajas", 4])
    ws2.append(["Ganache", "Subreceta", "0,8", "kg", None])           # 0,8 kg -> 800 g
    ws3 = wb.create_sheet("Recetas")
    ws3.append(["Receta", "Ingrediente", "Cantidad"])
    ws3.append(["Ganache", "Chocolate semiamargo", 500])
    ws3.append(["Ganache", "Leche entera", 300])
    ws3.append(["Bombones", "chocolate semiamargo", 400])
    ws3.append(["Bombones", "GANACHE", 600])
    ws3.append(["Alfajores de maicena", "Harina 000", 200])
    ws3.append(["Alfajores de maicena", "Polvo de unicornio", 1])     # error -> no toca los alfajores
    buf = io.BytesIO()
    wb.save(buf)

    html = cliente.post("/importar", data={"csrf": "tok", "archivo": (io.BytesIO(buf.getvalue()), "mio.xlsx")},
                        content_type="multipart/form-data").get_data(as_text=True)
    assert "Vista previa" in html and "Importar lo que está bien" in html
    assert "unidad «taza» no válida" in html and "Polvo de unicornio" in html
    assert "Alfajores de maicena» no se modificó" in html
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    assert "Bombones" not in cliente.get("/").get_data(as_text=True)          # todavía nada

    html = post(cliente, "/importar/aplicar", token=token).get_data(as_text=True)
    assert "Importado" in html and "Bombones" in html and "Ganache" in html
    # Ganache: 500 g x 85 + 300 ml x 8 = 42.500 + 2.400 = 44.900 / 800 g = 56,125 Gs/g
    # Bombones: 400 x 85 + 600 x 56,125 = 34.000 + 33.675 = 67.675 / 4 cajas = 16.918,75 x4 = 67.675 -> 67.700
    assert "44.900" in html and "67.700" in html
    assert "5.100" not in html            # los alfajores cambiaron de precio por la maicena (8.000 x 500 g = 16 Gs/g)
    det = cliente.get("/recetas/2").get_data(as_text=True)
    assert '"cantidad": 200.0, "nombre": "Harina 000"' in det or '"nombre": "Harina 000", "cantidad": 200.0' in det
    # la receta de alfajores no se tocó: sigue con sus 10 ingredientes
    assert det.count('"nombre": ') >= 10
    # el token ya se usó
    assert "venció" in post(cliente, "/importar/aplicar", token=token).get_data(as_text=True)


def test_importar_planilla_original(cliente):
    import os
    entrar(cliente)
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "planilla",
                        "ArteSana_Lista_de_Materiales.xlsx")
    with open(ruta, "rb") as f:
        html = cliente.post("/importar", data={"csrf": "tok", "archivo": (f, "planilla.xlsx")},
                            content_type="multipart/form-data").get_data(as_text=True)
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    html = post(cliente, "/importar/aplicar", token=token).get_data(as_text=True)
    assert "5.100" in html and "50.806" in html        # mismos números que la planilla


def test_archivo_que_no_es_excel(cliente):
    import io
    entrar(cliente)
    html = cliente.post("/importar", data={"csrf": "tok", "archivo": (io.BytesIO(b"hola"), "x.xlsx")},
                        content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "No es un Excel válido" in html
    html = cliente.post("/importar", data={"csrf": "tok", "archivo": (io.BytesIO(b"a;b"), "x.csv")},
                        content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "tiene que ser de Excel" in html
