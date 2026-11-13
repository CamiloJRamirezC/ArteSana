"""Pruebas del actualizador (estilo RiplayWEB) con un GitHub simulado: commit, árbol con huellas, zip y novedades."""
import io
import json
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import actualizador  # noqa: E402
import servicio  # noqa: E402

V1 = {
    "app.py": b"# app v1\n",
    "servicio.py": b"# servicio v1\n",
    "templates/productos.html": b"<h1>v1</h1>\n",
    "artesana.bat": b"@echo off\necho v1\n",            # en git queda con LF
    "requirements.txt": b"Flask\n",
    "static/iconos/icono.png": b"\x89PNG\r\n\x1a\nBINARIO",
    # nunca se tienen que escribir aunque vengan en el repo
    "artesana.env": b"HOST=hackeado\n",
    "instance/artesana.db": b"pisado",
    ".github/workflows/x.yml": b"on: push\n",
}


class GitHubFalso:
    def __init__(self):
        self.versiones, self.orden = {}, []
        self.error = None            # (código, cabeceras) para simular errores
        self.zip_roto = None         # path -> contenido distinto al del árbol
        falso = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def responder(self, cuerpo, tipo="application/json"):
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_GET(self):
                if falso.error:
                    codigo, cabeceras = falso.error
                    self.send_response(codigo)
                    for k, v in cabeceras.items():
                        self.send_header(k, v)
                    self.end_headers()
                    return
                ruta = self.path
                head = falso.orden[-1]
                if ruta == "/repos/yo/artesana/commits/main":
                    return self.responder(json.dumps(falso.commit(head)).encode())
                if ruta.startswith("/repos/yo/artesana/git/trees/"):
                    sha = ruta.split("/trees/")[1].split("?")[0]
                    arbol = [{"path": p, "type": "blob", "sha": actualizador.blob_sha(d, p)}
                             for p, d in falso.versiones[sha].items()]
                    return self.responder(json.dumps({"tree": arbol, "truncated": False}).encode())
                if ruta.startswith("/repos/yo/artesana/zipball/"):
                    return self.responder(falso.zip(ruta.rsplit("/", 1)[1]), "application/zip")
                if ruta.startswith("/repos/yo/artesana/compare/"):
                    a, b = ruta.split("/compare/")[1].split("...")
                    shas = falso.orden[falso.orden.index(a) + 1: falso.orden.index(b) + 1]
                    return self.responder(json.dumps({"commits": [falso.commit(s) for s in shas]}).encode())
                if ruta.startswith("/repos/yo/artesana/commits?"):
                    return self.responder(json.dumps([falso.commit(s) for s in reversed(falso.orden)]).encode())
                self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def commit(self, sha):
        return {"sha": sha, "commit": {"message": f"versión {sha[:4]}\n\ndetalle",
                                       "committer": {"date": "2026-11-12T12:00:00Z"}}}

    def publicar(self, sha, archivos):
        self.versiones[sha] = dict(archivos)
        self.orden.append(sha)

    def zip(self, sha):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for p, d in self.versiones[sha].items():
                z.writestr(f"yo-artesana-{sha[:7]}/{p}", (self.zip_roto or {}).get(p, d))
        return buf.getvalue()


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    raiz = tmp_path / "ArteSana"
    raiz.mkdir()
    for rel, datos in V1.items():
        if rel.startswith((".github", "instance/")) or rel == "artesana.env":
            continue
        destino = raiz / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        # en Windows los .bat y textos pueden estar con CRLF: tienen que contar como iguales
        destino.write_bytes(datos.replace(b"\n", b"\r\n") if rel.endswith(".bat") else datos)
    (raiz / "artesana.env").write_bytes(b"HOST=127.0.0.1\n")
    inst = raiz / "instance"
    inst.mkdir()
    (inst / "artesana.db").write_bytes(b"MIS DATOS")
    for nombre, valor in {"RAIZ": raiz, "INST": inst, "VERSION": inst / "version.json",
                          "RESPALDOS": inst / "respaldos", "LOGS": inst / "logs",
                          "LOG": inst / "logs" / "servicio.log"}.items():
        monkeypatch.setattr(servicio, nombre, valor)
    gh = GitHubFalso()
    gh.publicar("a" * 40, V1)
    monkeypatch.setenv("ARTESANA_GITHUB_API", gh.url)
    pip = []
    monkeypatch.setattr(servicio, "instalar_dependencias", lambda: pip.append(1) or True)
    cfg = dict(servicio.DEFAULTS, REPOSITORIO="yo/artesana")
    yield raiz, gh, cfg, pip
    gh.server.shutdown()


def test_reconoce_la_version_sin_reinstalar(entorno):
    raiz, gh, cfg, _ = entorno
    assert servicio.version_actual()["commit"] == ""
    r = servicio.revisar(False, cfg)
    assert r["actualizacion"] == "al_dia"           # mismos archivos (el .bat con CRLF cuenta igual)
    assert servicio.version_actual()["commit"] == "aaaaaaa"
    assert servicio.revisar(True, cfg)["actualizacion"] == "al_dia"
    assert not (raiz / "instance" / "respaldos_programa").exists()   # no tocó nada


def test_actualiza_solo_lo_que_cambio(entorno):
    raiz, gh, cfg, pip = entorno
    servicio.revisar(False, cfg)
    v2 = dict(V1)
    v2["templates/productos.html"] = b"<h1>v2</h1>\n"
    v2["static/nuevo.js"] = b"// nuevo\n"
    v2["requirements.txt"] = b"Flask\nwaitress\n"
    v2["artesana.bat"] = b"@echo off\necho v2\n"
    gh.publicar("b" * 40, v2)

    r = servicio.revisar(False, cfg)
    assert r["actualizacion"] == "disponible" and r["pendientes"] == 4
    assert [n["mensaje"] for n in r["novedades"]] == ["versión bbbb"]

    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "aplicada" and r["aplicada"]
    assert sorted(r["cambios"]) == ["artesana.bat", "requirements.txt", "static/nuevo.js", "templates/productos.html"]
    assert (raiz / "templates/productos.html").read_bytes() == b"<h1>v2</h1>\n"
    assert (raiz / "artesana.bat").read_bytes() == b"@echo off\r\necho v2\r\n"     # CRLF para Windows
    assert pip == [1]
    assert (raiz / "artesana.env").read_bytes() == b"HOST=127.0.0.1\n"             # protegido
    assert (raiz / "instance" / "artesana.db").read_bytes() == b"MIS DATOS"         # protegido
    assert not (raiz / ".github").exists()
    assert len(list((raiz / "instance" / "respaldos").glob("*.db"))) == 1          # respaldo de datos
    assert servicio.version_actual()["commit"] == "bbbbbbb"
    assert servicio.puede_volver()

    # volver a la versión anterior
    r = servicio.volver_version_anterior()
    assert r["actualizacion"] == "restaurada"
    assert (raiz / "templates/productos.html").read_bytes() == b"<h1>v1</h1>\n"
    assert not (raiz / "static/nuevo.js").exists()
    assert servicio.version_actual()["commit"] == "aaaaaaa"
    assert not servicio.puede_volver()


def test_archivo_editado_a_mano_se_reemplaza_con_copia(entorno):
    raiz, gh, cfg, _ = entorno
    (raiz / "app.py").write_bytes(b"# lo toque yo\n")
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "aplicada" and r["cambios"] == ["app.py"]
    assert (raiz / "app.py").read_bytes() == V1["app.py"]
    copia = next((raiz / "instance" / "respaldos_programa").glob("*/files/app.py"))
    assert copia.read_bytes() == b"# lo toque yo\n"


def test_descarga_danada_o_con_errores_no_cambia_nada(entorno):
    raiz, gh, cfg, _ = entorno
    v2 = dict(V1, **{"app.py": b"# app v2\n", "templates/productos.html": b"<h1>v2</h1>\n"})
    gh.publicar("c" * 40, v2)
    gh.zip_roto = {"templates/productos.html": b"<h1>otra cosa</h1>\n"}
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "error" and "no coincide" in r["mensaje_actualizacion"]
    assert (raiz / "app.py").read_bytes() == V1["app.py"]

    gh.zip_roto = None
    gh.publicar("d" * 40, dict(V1, **{"app.py": b"def roto(:\n"}))
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "error" and "tiene un error" in r["mensaje_actualizacion"]
    assert (raiz / "app.py").read_bytes() == V1["app.py"]


def test_si_no_puede_escribir_vuelve_todo_atras(entorno):
    raiz, gh, cfg, _ = entorno
    (raiz / "static" / "bloqueado.css").mkdir(parents=True)        # un directorio donde iría un archivo
    gh.publicar("e" * 40, dict(V1, **{"app.py": b"# app v2\n", "static/bloqueado.css": b"x{}\n"}))
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "error" and "Se volvió a la versión anterior" in r["mensaje_actualizacion"]
    assert (raiz / "app.py").read_bytes() == V1["app.py"]
    assert not servicio.puede_volver()


def test_errores_de_github(entorno, monkeypatch):
    raiz, gh, cfg, _ = entorno
    gh.error = (404, {})
    assert "GITHUB_TOKEN" in servicio.revisar(True, cfg)["mensaje_actualizacion"]
    import time
    gh.error = (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 600)})
    assert "minutos" in servicio.revisar(True, cfg)["mensaje_actualizacion"]
    gh.error = None
    monkeypatch.setenv("ARTESANA_GITHUB_API", "http://127.0.0.1:9")
    assert "internet" in servicio.revisar(True, cfg)["mensaje_actualizacion"]


def test_repo_que_no_es_artesana(entorno):
    raiz, gh, cfg, _ = entorno
    gh.publicar("f" * 40, {"README.md": b"otro proyecto"})
    r = servicio.revisar(True, cfg)
    assert r["actualizacion"] == "error" and "no parece ser ArteSana" in r["mensaje_actualizacion"]


def test_archivos_protegidos():
    assert not actualizador.wanted("artesana.env")
    assert not actualizador.wanted("instance/artesana.db")
    assert not actualizador.wanted(".venv/Lib/x.py")
    assert not actualizador.wanted("../afuera.py")
    assert not actualizador.wanted(".github/workflows/x.yml")
    assert actualizador.wanted("templates/productos.html") and actualizador.wanted(".gitattributes")
