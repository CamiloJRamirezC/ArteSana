"""Pruebas del ícono de la bandeja con un pystray simulado (la librería real solo corre en Windows)."""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bandeja  # noqa: E402
import servicio as sv  # noqa: E402


class ItemFalso:
    def __init__(self, text, action, checked=None, radio=False, default=False, visible=True, enabled=True):
        self.text, self.action, self.checked = text, action, checked
        self.default, self.visible, self.enabled = default, visible, enabled

    def valor(self, atributo):
        v = getattr(self, atributo)
        return v(self) if callable(v) else v


class MenuFalso:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


pystray_falso = types.SimpleNamespace(MenuItem=ItemFalso, Menu=MenuFalso)


class IconoFalso:
    def __init__(self):
        self.avisos, self.actualizaciones, self.detenido = [], 0, False
        self.icon = self.title = None

    def notify(self, mensaje, titulo=None):
        self.avisos.append(mensaje)

    def update_menu(self):
        self.actualizaciones += 1

    def stop(self):
        self.detenido = True


@pytest.fixture
def estado(monkeypatch):
    datos = {"corriendo": True, "estado": {"servidor": "corriendo", "ultima_revision": "1",
                                           "actualizacion": "al_dia", "url_red": "",
                                           "version": {"commit": "abc1234", "fecha": "2026-11-11 08:00"}}}
    monkeypatch.setattr(sv, "servicio_corriendo", lambda: (datos["corriendo"], 123))
    monkeypatch.setattr(sv, "leer_estado", lambda: dict(datos["estado"]))
    monkeypatch.setattr(sv, "autoinicio_activo", lambda: False)
    monkeypatch.setattr(sv, "log", lambda *_: None)
    return datos


def recorrer(menu):
    for it in menu.items:
        if it is MenuFalso.SEPARATOR:
            continue
        yield it
        if isinstance(it.action, MenuFalso):
            yield from recorrer(it.action)


def test_menu_completo_y_textos(estado):
    c = bandeja.Controlador(ejecutar_en_hilo=False)
    menu = c.menu(pystray_falso)
    textos = []
    for it in recorrer(menu):
        textos.append(it.valor("text"))
        for atributo in ("enabled", "visible", "checked"):
            it.valor(atributo)  # que ningún callable falle
    for esperado in ("Abrir ArteSana", "Reiniciar", "Buscar y aplicar actualizaciones",
                     "Usar desde otros equipos de la red…", "Ver registro", "Abrir carpeta de datos",
                     "Iniciar con Windows", "Salir (la app sigue funcionando)", "Detener y salir"):
        assert esperado in textos
    assert any(t.startswith("● Funcionando en http://127.0.0.1:") for t in textos)
    assert "Versión abc1234 del 2026-11-11 08:00" in textos
    assert [it for it in recorrer(menu) if it.default][0].text == "Abrir ArteSana"

    visibles = {it.valor("text"): it.valor("visible") for it in recorrer(menu)}
    assert visibles["Detener"] and not visibles["Iniciar"]
    assert not visibles["Copiar dirección para otros equipos"]
    estado["corriendo"] = False
    visibles = {it.valor("text"): it.valor("visible") for it in recorrer(menu)}
    assert visibles["Iniciar"] and not visibles["Detener"]
    assert c.texto_estado() == "○ Detenida"


def test_imagenes():
    for nombre in ("ok", "detenida", "error", "ocupada"):
        im = bandeja.imagen(nombre)
        assert im.size == (64, 64) and im.mode == "RGBA"
    assert bandeja.imagen("ok").getpixel((32, 32)) != bandeja.imagen("detenida").getpixel((32, 32))


def test_avisos_al_cambiar_el_estado(estado):
    c = bandeja.Controlador(ejecutar_en_hilo=False)
    c.icon = IconoFalso()
    assert c.revisar_cambios() == []

    estado["estado"].update(ultima_revision="2", actualizacion="aplicada",
                            mensaje_actualizacion="Actualizado a la versión del 2026-11-11 09:00")
    assert c.revisar_cambios() == ["Actualizado a la versión del 2026-11-11 09:00"]
    assert c.revisar_cambios() == []          # no se repite

    estado["corriendo"] = False
    assert c.revisar_cambios() == ["ArteSana se detuvo."]
    assert c.icon.actualizaciones >= 2
    assert c.estado_icono() == "detenida"


def test_una_accion_por_vez(estado, monkeypatch):
    c = bandeja.Controlador(ejecutar_en_hilo=False)
    c.icon = IconoFalso()
    monkeypatch.setattr(sv, "cmd_reiniciar", lambda: 0)
    c.lock.acquire()
    c.ocupada = "Buscando actualizaciones"
    c.reiniciar()
    assert "todavía estoy en «Buscando actualizaciones»" in c.icon.avisos[-1]
    assert c.estado_icono() == "ocupada" and c.texto_estado().startswith("⏳")
    c.ocupada = None
    c.lock.release()
    c.reiniciar()
    assert c.icon.avisos[-1] == "ArteSana se reinició."


def test_se_relanza_si_cambia_el_codigo(estado):
    c = bandeja.Controlador(ejecutar_en_hilo=False)
    c.icon = IconoFalso()
    c.firmas = {"bandeja.py": 0, "servicio.py": 0}
    c.revisar_cambios()
    assert c.relanzar and c.icon.detenido
