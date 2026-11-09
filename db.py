"""Acceso a datos: SQLite en tu computadora, PostgreSQL (Supabase) en internet.

Se elige con la variable DATABASE_URL:
- vacía o 'sqlite:///archivo.db'  -> SQLite local
- 'postgresql://...'               -> PostgreSQL
Las consultas usan '?' como marcador; para PostgreSQL se traduce a '%s'.
"""
import os
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredientes (
    id {PK},
    nombre TEXT NOT NULL UNIQUE,
    presentacion_cantidad DOUBLE PRECISION NOT NULL DEFAULT 0,
    presentacion_unidad TEXT NOT NULL DEFAULT 'g',
    precio DOUBLE PRECISION NOT NULL DEFAULT 0,
    fecha_precio TEXT,
    notas TEXT
);
CREATE TABLE IF NOT EXISTS precios_historial (
    id {PK},
    ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id) ON DELETE CASCADE,
    fecha TEXT NOT NULL,
    presentacion_cantidad DOUBLE PRECISION NOT NULL,
    presentacion_unidad TEXT NOT NULL,
    precio DOUBLE PRECISION NOT NULL,
    origen TEXT NOT NULL DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS recetas (
    id {PK},
    nombre TEXT NOT NULL UNIQUE,
    tipo TEXT NOT NULL DEFAULT 'venta',
    rinde DOUBLE PRECISION NOT NULL DEFAULT 1,
    unidad_rinde TEXT NOT NULL DEFAULT 'u',
    multiplicador DOUBLE PRECISION NOT NULL DEFAULT 3,
    precio_venta_real DOUBLE PRECISION,
    notas TEXT
);
CREATE TABLE IF NOT EXISTS receta_items (
    id {PK},
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    ingrediente_id INTEGER REFERENCES ingredientes(id),
    subreceta_id INTEGER REFERENCES recetas(id),
    cantidad DOUBLE PRECISION NOT NULL DEFAULT 0,
    orden INTEGER NOT NULL DEFAULT 0,
    CHECK ((ingrediente_id IS NULL) <> (subreceta_id IS NULL))
);
CREATE TABLE IF NOT EXISTS configuracion (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
)
"""


class ErrorDeIntegridad(Exception):
    """Nombre repetido o dato referenciado por otro registro."""


def _es_postgres(url):
    return url.startswith("postgres://") or url.startswith("postgresql://")


def _url():
    return current_app.config.get("DATABASE_URL") or ""


def _conectar():
    url = _url()
    if _es_postgres(url):
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(url, row_factory=dict_row, prepare_threshold=None)
        return conn, "postgres"
    ruta = url.replace("sqlite:///", "") if url else os.path.join(current_app.instance_path, "artesana.db")
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn, "sqlite"


def conexion():
    if "db_conn" not in g:
        g.db_conn, g.db_tipo = _conectar()
    return g.db_conn


def cerrar(_exc=None):
    conn = g.pop("db_conn", None)
    g.pop("db_tipo", None)
    if conn is not None:
        conn.close()


def _sql(sql):
    return sql.replace("?", "%s") if g.get("db_tipo") == "postgres" else sql


def _errores_integridad():
    errores = [sqlite3.IntegrityError]
    try:
        import psycopg
        errores.append(psycopg.IntegrityError)
    except ImportError:
        pass
    return tuple(errores)


def consultar(sql, params=()):
    conn = conexion()
    cur = conn.cursor()
    cur.execute(_sql(sql), params)
    filas = [dict(f) for f in cur.fetchall()]
    cur.close()
    if g.get("db_tipo") == "postgres":
        conn.commit()  # cierra la transacción de lectura
    return filas


def consultar_uno(sql, params=()):
    filas = consultar(sql, params)
    return filas[0] if filas else None


def ejecutar(sql, params=(), devolver_id=False):
    conn = conexion()
    if devolver_id:
        sql = sql.rstrip().rstrip(";") + " RETURNING id"
    try:
        cur = conn.cursor()
        cur.execute(_sql(sql), params)
        nuevo_id = None
        if devolver_id:
            fila = cur.fetchone()
            nuevo_id = fila["id"] if isinstance(fila, dict) else fila[0]
        cur.close()
        conn.commit()
        return nuevo_id
    except _errores_integridad() as e:
        conn.rollback()
        raise ErrorDeIntegridad(str(e)) from e


def crear_tablas():
    conn = conexion()
    pk = "SERIAL PRIMARY KEY" if g.get("db_tipo") == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    cur = conn.cursor()
    for sentencia in SCHEMA.format(PK=pk).split(";"):
        if sentencia.strip():
            cur.execute(sentencia)
    cur.close()
    conn.commit()


def obtener_config(clave, default=None):
    fila = consultar_uno("SELECT valor FROM configuracion WHERE clave = ?", (clave,))
    return fila["valor"] if fila else default


def guardar_config(clave, valor):
    ejecutar(
        "INSERT INTO configuracion (clave, valor) VALUES (?, ?) "
        "ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor",
        (clave, str(valor)),
    )
