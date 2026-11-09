"""Servidor web de ArteSana para correr en tu computadora.

Lo inicia servicio.py; no hace falta ejecutarlo a mano.
En Windows usa waitress; si no está instalado usa el servidor de Werkzeug.
"""
import os

from app import app

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", "5000"))

try:
    from waitress import serve
except ImportError:
    serve = None

if serve:
    print(f"ArteSana (waitress) en http://{host}:{port}", flush=True)
    serve(app, host=host, port=port, threads=6, ident="ArteSana")
else:
    from werkzeug.serving import run_simple
    print(f"ArteSana (werkzeug) en http://{host}:{port}", flush=True)
    run_simple(host, port, app, threaded=True, use_reloader=False)
