"""Pruebas de la actualización sin git (descarga directa del ZIP de GitHub),
con un servidor falso que imita a GitHub."""
import io
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import servicio  # noqa: E402

BASE = {
    "servicio.py": b"# servicio v1\n",
    "app.py": b"# app v1\n",
    "templates/productos.html": b"<h1>v1</h1>\n",
    "viejo.txt": b"se borra en v2\n",
    "requirements.txt": b"Flask\n",
    # Lo que nunca se debe tocar aunque venga en el ZIP
    "artesana.env": b"HOST=hackeado\n",
    "instance/artesana.db": b"pisado",
}


class GitHubFalso:
    def __init__(self):
        self.versiones = {}
        self.actual = None
        self.estado_http = 200
        falso = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if falso.estado_http != 200:
                    self.send_error(falso.estado_http)
                    return
                if self.path == "/repos/yo/artesana/commits/main":
                    cuerpo = json.dumps({"sha": falso.actual, "commit": {
                        "message": f"versión {falso.actual}\n\ndetalle",
                        "committer": {"date": "2026-11-10T12:00:00Z"}}}).encode()
                elif self.path.startswith("/yo/artesana/zip/"):
                    cuerpo = falso.zip(self.path.rsplit("/", 1)[1])
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def publicar(self, sha, archivos):
        self.versiones[sha] = archivos
        self.actual = sha

    def zip(self, sha):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for rel, datos in self.versiones[sha].items():
                z.writestr(f"ArteSana-{sha}/{rel}", datos)
        return buf.getvalue()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    raiz = tmp_path / "ArteSana-main"
    raiz.mkdir()
    for rel, datos in BASE.items():
        if not rel.startswith(("instance/", "artesana.env")):
            (raiz / rel).parent.mkdir(parents=True, exist_ok=True)
            (raiz / rel).write_bytes(datos)
    (raiz / "artesana.env").write_bytes(b"HOST=127.0.0.1\n")
    inst = raiz / "instance"
    inst.mkdir()
    (inst / "artesana.db").write_bytes(b"MIS DATOS")
    for nombre, valor in {"RAIZ": raiz, "INST": inst, "VERSION_ZIP": inst / "version.json",
                          "RESPALDOS": inst / "respaldos", "LOGS": inst / "logs",
                          "LOG": inst / "logs" / "servicio.log"}.items():
        monkeypatch.setattr(servicio, nombre, valor)
    gh = GitHubFalso()
    monkeypatch.setenv("ARTESANA_GITHUB_API", gh.url)
    monkeypatch.setenv("ARTESANA_GITHUB_ZIP", gh.url)
    pip = []
    monkeypatch.setattr(servicio, "instalar_dependencias", lambda: pip.append(1) or True)
    cfg = dict(servicio.DEFAULTS, REPOSITORIO="yo/artesana")
    yield raiz, gh, cfg, pip
    gh.server.shutdown()


def test_sin_git_usa_descarga_directa(entorno):
    raiz, gh, cfg, _ = entorno
    assert servicio.modo_actualizacion() == "zip"


def test_flujo_de_actualizacion_sin_git(entorno):
    raiz, gh, cfg, pip = entorno
    gh.publicar("a" * 40, dict(BASE))

    # 1ª vez: sin versión registrada -> instala la de GitHub
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "aplicada" and r["modo_actualizacion"] == "zip"
    assert (raiz / "artesana.env").read_bytes() == b"HOST=127.0.0.1\n"           # protegido
    assert (raiz / "instance" / "artesana.db").read_bytes() == b"MIS DATOS"       # protegido
    assert json.loads((raiz / "instance" / "version.json").read_text())["sha"] == "a" * 40
    assert len(list((raiz / "instance" / "respaldos").glob("*.db"))) == 1
    assert servicio.version_actual()["commit"] == "aaaaaaa"

    # 2ª vez: nada nuevo
    assert servicio.revisar(True, cfg)["actualizacion"] == "al_dia"

    # Nueva versión: cambia una plantilla, borra un archivo, agrega otro, cambia requirements
    v2 = dict(BASE)
    v2["templates/productos.html"] = b"<h1>v2</h1>\n"
    del v2["viejo.txt"]
    v2["static/nuevo.js"] = b"// nuevo\n"
    v2["requirements.txt"] = b"Flask\nwaitress\n"
    gh.publicar("b" * 40, v2)

    r = servicio.revisar(False, cfg)
    assert r["actualizacion"] == "disponible" and "versión bbbb" in r["mensaje_actualizacion"]

    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "aplicada"
    assert (raiz / "templates/productos.html").read_bytes() == b"<h1>v2</h1>\n"
    assert not (raiz / "viejo.txt").exists()
    assert (raiz / "static/nuevo.js").exists()
    assert pip == [1]                                   # reinstaló dependencias
    assert "servicio.py" not in r["cambios"]            # no hace falta relanzar el servicio

    # Un archivo editado a mano bloquea la actualización
    (raiz / "app.py").write_bytes(b"# lo toque yo\n")
    v3 = dict(v2)
    v3["servicio.py"] = b"# servicio v3\n"
    gh.publicar("c" * 40, v3)
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "cambios_locales" and "app.py" in r["mensaje_actualizacion"]

    # Al deshacer el cambio, actualiza y avisa que cambió servicio.py (se relanza)
    (raiz / "app.py").write_bytes(BASE["app.py"])
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "aplicada" and "servicio.py" in r["cambios"]


def test_errores_de_github(entorno):
    raiz, gh, cfg, _ = entorno
    gh.publicar("a" * 40, dict(BASE))
    gh.estado_http = 404
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "error" and "GITHUB_TOKEN" in r["mensaje_actualizacion"]
    gh.estado_http = 403
    assert "limitó" in servicio.revisar(True, cfg)["mensaje_actualizacion"]
    cfg["REPOSITORIO"] = "yo/artesana"
    os.environ["ARTESANA_GITHUB_API"] = "http://127.0.0.1:9"   # nadie escucha
    assert "internet" in servicio.revisar(True, cfg)["mensaje_actualizacion"]


def test_zip_que_no_es_artesana_no_se_aplica(entorno):
    raiz, gh, cfg, _ = entorno
    gh.publicar("d" * 40, {"README.md": b"otro proyecto"})
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "error"
    assert (raiz / "app.py").read_bytes() == BASE["app.py"]
