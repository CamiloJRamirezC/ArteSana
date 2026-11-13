"""ArteSana - lista de materiales y costeo (app web Flask).

Ejecutar en tu computadora:   flask --app app run --debug
En internet (Render):         gunicorn app:app
"""
import hmac
import json
import os
import secrets
from datetime import date
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

import costeo
import db
import fuentes
import importexport
from utils import cant, gs, gs_unit, parse_num, pct


def crear_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "solo-para-desarrollo-local",
        DATABASE_URL=os.environ.get("DATABASE_URL", ""),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # Excel de hasta 10 MB
        # Los define servicio.py cuando la app corre en tu computadora
        SERVICIO_CONTROL=os.environ.get("ARTESANA_CONTROL", ""),
        SERVICIO_ESTADO=os.environ.get("ARTESANA_ESTADO", ""),
    )
    if os.environ.get("RENDER") or os.environ.get("SESSION_COOKIE_SECURE") == "1":
        app.config["SESSION_COOKIE_SECURE"] = True
    if config:
        app.config.update(config)

    app.teardown_appcontext(db.cerrar)
    app.jinja_env.filters.update(gs=gs, gs_unit=gs_unit, cant=cant, pct=pct)

    with app.app_context():
        db.crear_tablas()

    registrar_rutas(app)
    return app


# ---------------------------------------------------------------- helpers

def _token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def _datos():
    """Carga todo y calcula. El volumen de un emprendimiento es chico, así que
    recalcular en cada pantalla es simple y siempre está al día."""
    ingredientes = {i["id"]: i for i in db.consultar("SELECT * FROM ingredientes ORDER BY nombre")}
    recetas = {r["id"]: r for r in db.consultar("SELECT * FROM recetas ORDER BY nombre")}
    items = db.consultar("SELECT * FROM receta_items ORDER BY receta_id, orden, id")
    redondeo = parse_num(db.obtener_config("redondeo", "100"), 100)
    calculo = costeo.calcular(ingredientes, recetas, items, redondeo)
    return ingredientes, recetas, items, calculo, redondeo


def _num(campo, obligatorio=True, minimo=None):
    valor = parse_num(request.form.get(campo))
    if valor is None and obligatorio:
        raise ValueError(f"Falta completar «{campo.replace('_', ' ')}»")
    if valor is not None and minimo is not None and valor < minimo:
        raise ValueError(f"«{campo.replace('_', ' ')}» no puede ser menor a {minimo}")
    return valor


def _texto(campo, obligatorio=True):
    valor = (request.form.get(campo) or "").strip()
    if obligatorio and not valor:
        raise ValueError(f"Falta completar «{campo}»")
    return valor


def _usos_ingrediente(iid, recetas, items):
    return sorted({recetas[it["receta_id"]]["nombre"] for it in items if it["ingrediente_id"] == iid})


def _usos_receta(rid, recetas, items):
    return sorted({recetas[it["receta_id"]]["nombre"] for it in items if it["subreceta_id"] == rid})


def _registrar_precio(iid, cantidad, unidad, precio, origen="manual"):
    db.ejecutar(
        "INSERT INTO precios_historial (ingrediente_id, fecha, presentacion_cantidad, "
        "presentacion_unidad, precio, origen) VALUES (?, ?, ?, ?, ?, ?)",
        (iid, date.today().isoformat(), cantidad, unidad, precio, origen),
    )


# ---------------------------------------------------------------- rutas

def registrar_rutas(app):

    @app.context_processor
    def globales():
        return {"csrf_token": _token_csrf, "con_login": bool(app.config["APP_PASSWORD"]),
                "UNIDADES_PRESENTACION": list(costeo.UNIDADES_PRESENTACION),
                "UNIDADES_RINDE_VENTA": costeo.UNIDADES_RINDE_VENTA,
                "UNIDADES_RINDE_SUBRECETA": costeo.UNIDADES_RINDE_SUBRECETA}

    @app.before_request
    def proteger():
        if request.endpoint in ("login", "static", "salud", "service_worker", "offline", "manifest"):
            return None
        if app.config["APP_PASSWORD"] and not session.get("ok"):
            return redirect(url_for("login", siguiente=request.path))
        if request.method == "POST":
            enviado = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(enviado, session.get("csrf", "")):
                abort(400, "El formulario venció. Volvé atrás y recargá la página.")
        return None

    # ---------- app instalable (PWA)
    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript", max_age=0)
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(app.static_folder, "manifest.webmanifest",
                                   mimetype="application/manifest+json", max_age=3600)

    @app.get("/offline")
    def offline():
        return render_template("offline.html")

    # ---------- servicio local (artesana.bat)
    def estado_servicio():
        ruta = app.config["SERVICIO_ESTADO"]
        if not ruta:
            return None
        try:
            return json.loads(Path(ruta).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @app.get("/sistema/estado")
    def sistema_estado():
        estado = estado_servicio()
        if estado is None:
            abort(404)
        return jsonify(estado)

    @app.post("/sistema/<accion>")
    def sistema_accion(accion):
        if accion not in ("actualizar", "reiniciar") or not app.config["SERVICIO_CONTROL"]:
            abort(404)
        estado = estado_servicio() or {}
        control = Path(app.config["SERVICIO_CONTROL"])
        control.mkdir(parents=True, exist_ok=True)
        (control / accion).write_text(date.today().isoformat(), encoding="utf-8")
        return redirect(url_for("sistema_espera", accion=accion,
                                rev=estado.get("ultima_revision", ""),
                                ini=estado.get("servidor_iniciado", "")))

    @app.get("/sistema/espera")
    def sistema_espera():
        if not app.config["SERVICIO_CONTROL"]:
            abort(404)
        return render_template("espera.html", accion=request.args.get("accion", "reiniciar"),
                               rev=request.args.get("rev", ""), ini=request.args.get("ini", ""))

    @app.get("/salud")
    def salud():
        # Toca la base para que Supabase no la pause por inactividad
        db.consultar("SELECT 1 AS ok")
        return "ok"

    # ---------- login
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not app.config["APP_PASSWORD"]:
            return redirect(url_for("productos"))
        if request.method == "POST":
            if hmac.compare_digest(request.form.get("password", ""), app.config["APP_PASSWORD"]):
                session.clear()
                session.permanent = True
                session["ok"] = True
                destino = request.args.get("siguiente") or ""
                return redirect(destino if destino.startswith("/") and not destino.startswith("//")
                                else url_for("productos"))
            flash("Contraseña incorrecta", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- productos / recetas
    @app.get("/")
    def productos():
        ingredientes, recetas, items, calculo, redondeo = _datos()
        filas = [{**r, **calculo["recetas"][rid]} for rid, r in recetas.items()]
        return render_template(
            "productos.html",
            venta=[f for f in filas if f["tipo"] == "venta"],
            subrecetas=[f for f in filas if f["tipo"] == "subreceta"],
            vacio=not ingredientes and not recetas,
            redondeo=redondeo,
            margen_minimo=_margen_minimo(),
        )

    def _margen_minimo():
        return (parse_num(db.obtener_config("margen_minimo", "60"), 60) or 0) / 100

    @app.post("/productos/precios")
    def productos_precios():
        """Guarda los precios de venta y de mercado editados en la tabla. Todo o nada."""
        recetas = {r["id"]: r for r in db.consultar("SELECT * FROM recetas WHERE tipo = 'venta'")}
        datos = request.get_json(silent=True) or {}
        hoy = date.today().isoformat()
        errores, cambios = [], []
        for it in datos.get("items", []):
            rid = it.get("id")
            r = recetas.get(rid)
            if not r:
                errores.append({"id": rid, "campo": "precio_venta", "mensaje": "Ese producto ya no existe"})
                continue
            valores = {}
            for campo in ("precio_venta", "precio_mercado"):
                texto = str(it.get(campo) or "").strip()
                try:
                    valor = parse_num(texto) if texto else None
                except ValueError as e:
                    errores.append({"id": rid, "campo": campo, "mensaje": str(e)})
                    break
                if valor is not None and valor < 0:
                    errores.append({"id": rid, "campo": campo, "mensaje": "No puede ser negativo"})
                    break
                valores[campo] = valor or None
            else:
                fecha = r.get("mercado_fecha")
                if valores["precio_mercado"] != r.get("precio_mercado"):
                    fecha = hoy if valores["precio_mercado"] else None
                cambios.append((valores["precio_venta"], valores["precio_mercado"], fecha, rid))
        if errores:
            return jsonify({"ok": False, "errores": errores}), 400
        with db.lote():
            for cambio in cambios:
                db.ejecutar("UPDATE recetas SET precio_venta_real=?, precio_mercado=?, mercado_fecha=? WHERE id=?", cambio)
        flash(f"Precios guardados: {len(cambios)} producto(s)", "ok")
        return jsonify({"ok": True, "guardados": len(cambios)})

    @app.route("/recetas/nueva", methods=["GET", "POST"])
    def receta_nueva():
        if request.method == "POST":
            try:
                datos = _form_receta()
                with db.lote():
                    rid = db.ejecutar(
                        "INSERT INTO recetas (nombre, tipo, rinde, unidad_rinde, multiplicador, "
                        "precio_venta_real, notas) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        datos, devolver_id=True)
                    _guardar_mercado(rid)
                flash("Receta creada. Ahora cargá sus ingredientes.", "ok")
                return redirect(url_for("receta", rid=rid))
            except ValueError as e:
                flash(str(e), "error")
            except db.ErrorDeIntegridad:
                flash("Ya existe una receta con ese nombre", "error")
        tipo = request.args.get("tipo", "venta")
        return render_template("receta_form.html", receta=None, tipo=tipo, form=request.form)

    def _guardar_mercado(rid, anterior=None):
        """Precio de mercado y su referencia, desde el formulario de receta."""
        if request.form.get("tipo", "venta") != "venta":
            db.ejecutar("UPDATE recetas SET precio_mercado=NULL, mercado_ref=NULL, mercado_fecha=NULL WHERE id=?", (rid,))
            return
        mercado = _num("precio_mercado", obligatorio=False, minimo=0) or None
        ref = _texto("mercado_ref", False) or None
        fecha = (anterior or {}).get("mercado_fecha")
        if mercado != (anterior or {}).get("precio_mercado"):
            fecha = date.today().isoformat() if mercado else None
        db.ejecutar("UPDATE recetas SET precio_mercado=?, mercado_ref=?, mercado_fecha=? WHERE id=?",
                    (mercado, ref, fecha, rid))

    def _form_receta():
        tipo = request.form.get("tipo", "venta")
        if tipo not in costeo.TIPOS_RECETA:
            raise ValueError("Tipo de receta inválido")
        unidad = request.form.get("unidad_rinde", "u")
        validas = costeo.UNIDADES_RINDE_VENTA if tipo == "venta" else costeo.UNIDADES_RINDE_SUBRECETA
        if unidad not in validas:
            raise ValueError("Una subreceta tiene que rendir en g, ml o u para poder usarla en otras recetas")
        rinde = _num("rinde", minimo=0)
        if rinde <= 0:
            raise ValueError("El rinde tiene que ser mayor a 0")
        mult = _num("multiplicador", obligatorio=(tipo == "venta"), minimo=0) or 0
        real = _num("precio_venta_real", obligatorio=False, minimo=0) if tipo == "venta" else None
        return (_texto("nombre"), tipo, rinde, unidad, mult, real or None, _texto("notas", False))

    @app.get("/recetas/<int:rid>")
    def receta(rid):
        ingredientes, recetas, items, calculo, redondeo = _datos()
        if rid not in recetas:
            abort(404)
        subrecetas_posibles = [r for r in recetas.values() if r["tipo"] == "subreceta"
                               and not costeo.crearia_ciclo(rid, r["id"], items)]
        componentes = [{"nombre": i["nombre"], "tipo": "ingrediente",
                        "unidad": costeo.unidad_base(i["presentacion_unidad"]),
                        "precio": calculo["ingredientes"][i["id"]]} for i in ingredientes.values()]
        componentes += [{"nombre": r["nombre"], "tipo": "subreceta", "unidad": r["unidad_rinde"],
                         "precio": calculo["recetas"][r["id"]]["costo_unitario"]} for r in subrecetas_posibles]
        return render_template(
            "receta.html",
            receta=recetas[rid],
            res=calculo["recetas"][rid],
            ingredientes=list(ingredientes.values()),
            precios=calculo["ingredientes"],
            subrecetas_posibles=subrecetas_posibles,
            costos_recetas=calculo["recetas"],
            usado_en=_usos_receta(rid, recetas, items),
            unidad_base=costeo.unidad_base,
            componentes=componentes,
            margen_minimo=_margen_minimo(),
            lineas_grilla=[{"nombre": ln["nombre"], "cantidad": ln["cantidad"]}
                           for ln in calculo["recetas"][rid]["lineas"]],
            redondeo=redondeo,
        )

    @app.route("/recetas/<int:rid>/editar", methods=["GET", "POST"])
    def receta_editar(rid):
        r = db.consultar_uno("SELECT * FROM recetas WHERE id = ?", (rid,))
        if not r:
            abort(404)
        if request.method == "POST":
            try:
                datos = _form_receta()
                if datos[1] == "venta" and r["tipo"] == "subreceta":
                    _, recetas, items, _, _ = _datos()
                    usos = _usos_receta(rid, recetas, items)
                    if usos:
                        raise ValueError("No se puede pasar a Venta: se usa como subreceta en " + ", ".join(usos))
                with db.lote():
                    db.ejecutar(
                        "UPDATE recetas SET nombre=?, tipo=?, rinde=?, unidad_rinde=?, multiplicador=?, "
                        "precio_venta_real=?, notas=? WHERE id=?", datos + (rid,))
                    _guardar_mercado(rid, r)
                flash("Receta guardada", "ok")
                return redirect(url_for("receta", rid=rid))
            except ValueError as e:
                flash(str(e), "error")
            except db.ErrorDeIntegridad:
                flash("Ya existe una receta con ese nombre", "error")
        return render_template("receta_form.html", receta=r, tipo=r["tipo"],
                               form=request.form if request.method == "POST" else None)

    @app.post("/recetas/<int:rid>/borrar")
    def receta_borrar(rid):
        _, recetas, items, _, _ = _datos()
        if rid not in recetas:
            abort(404)
        usos = _usos_receta(rid, recetas, items)
        if usos:
            flash("No se puede borrar: se usa en " + ", ".join(usos), "error")
            return redirect(url_for("receta", rid=rid))
        db.ejecutar("DELETE FROM receta_items WHERE receta_id = ?", (rid,))
        db.ejecutar("DELETE FROM recetas WHERE id = ?", (rid,))
        flash(f"Receta «{recetas[rid]['nombre']}» borrada", "ok")
        return redirect(url_for("productos"))

    @app.post("/recetas/<int:rid>/duplicar")
    def receta_duplicar(rid):
        r = db.consultar_uno("SELECT * FROM recetas WHERE id = ?", (rid,))
        if not r:
            abort(404)
        nombre, n = f"{r['nombre']} (copia)", 2
        while db.consultar_uno("SELECT id FROM recetas WHERE nombre = ?", (nombre,)):
            nombre, n = f"{r['nombre']} (copia {n})", n + 1
        nuevo = db.ejecutar(
            "INSERT INTO recetas (nombre, tipo, rinde, unidad_rinde, multiplicador, precio_venta_real, notas, "
            "precio_mercado, mercado_ref, mercado_fecha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (nombre, r["tipo"], r["rinde"], r["unidad_rinde"], r["multiplicador"],
             r["precio_venta_real"], r["notas"], r.get("precio_mercado"), r.get("mercado_ref"),
             r.get("mercado_fecha")), devolver_id=True)
        for it in db.consultar("SELECT * FROM receta_items WHERE receta_id = ? ORDER BY orden, id", (rid,)):
            db.ejecutar(
                "INSERT INTO receta_items (receta_id, ingrediente_id, subreceta_id, cantidad, orden) "
                "VALUES (?, ?, ?, ?, ?)",
                (nuevo, it["ingrediente_id"], it["subreceta_id"], it["cantidad"], it["orden"]))
        flash("Receta duplicada. Cambiale el nombre y ajustá lo que haga falta.", "ok")
        return redirect(url_for("receta_editar", rid=nuevo))

    # ---------- líneas de receta
    @app.post("/recetas/<int:rid>/lineas")
    def linea_agregar(rid):
        _, recetas, items, _, _ = _datos()
        if rid not in recetas:
            abort(404)
        try:
            elegido = request.form.get("componente", "")
            cantidad = _num("cantidad", minimo=0)
            if cantidad <= 0:
                raise ValueError("La cantidad tiene que ser mayor a 0")
            ing_id = sub_id = None
            if elegido.startswith("i:"):
                ing_id = int(elegido[2:])
            elif elegido.startswith("r:"):
                sub_id = int(elegido[2:])
                if sub_id not in recetas or recetas[sub_id]["tipo"] != "subreceta":
                    raise ValueError("Esa subreceta no existe")
                if costeo.crearia_ciclo(rid, sub_id, items):
                    raise ValueError("No se puede: esa subreceta ya contiene a esta receta")
            else:
                raise ValueError("Elegí un ingrediente o una subreceta")
            orden = max([it["orden"] for it in items if it["receta_id"] == rid], default=0) + 1
            db.ejecutar(
                "INSERT INTO receta_items (receta_id, ingrediente_id, subreceta_id, cantidad, orden) "
                "VALUES (?, ?, ?, ?, ?)", (rid, ing_id, sub_id, cantidad, orden))
        except ValueError as e:
            flash(str(e), "error")
        except db.ErrorDeIntegridad:
            flash("No se pudo agregar: el ingrediente ya no existe", "error")
        return redirect(url_for("receta", rid=rid) + "#lineas")

    @app.post("/recetas/<int:rid>/grilla")
    def receta_grilla(rid):
        """Guarda todas las líneas de la grilla juntas. Si alguna fila está mal, no guarda nada."""
        ingredientes, recetas, items, _, _ = _datos()
        if rid not in recetas:
            abort(404)
        datos = request.get_json(silent=True) or {}
        por_nombre_ing = {importexport.normalizar(i["nombre"]): i for i in ingredientes.values()}
        por_nombre_sub = {importexport.normalizar(r["nombre"]): r for r in recetas.values() if r["tipo"] == "subreceta"}
        otros = [it for it in items if it["receta_id"] != rid]
        errores, lineas = [], []
        for n, fila in enumerate(datos.get("lineas", [])):
            nombre = importexport.limpiar_nombre(fila.get("nombre"))
            texto_cant = str(fila.get("cantidad") or "").strip()
            if not nombre and not texto_cant:
                continue  # fila vacía
            if not nombre:
                errores.append({"fila": n, "campo": "nombre", "mensaje": "Falta el ingrediente"})
                continue
            clave = importexport.normalizar(nombre)
            ing, sub = por_nombre_ing.get(clave), por_nombre_sub.get(clave)
            if ing and sub:
                errores.append({"fila": n, "campo": "nombre",
                                "mensaje": f"«{nombre}» es a la vez ingrediente y subreceta: renombrá uno"})
                continue
            if not ing and not sub:
                errores.append({"fila": n, "campo": "nombre", "mensaje": f"«{nombre}» no existe: cargalo en Ingredientes"})
                continue
            if sub and costeo.crearia_ciclo(rid, sub["id"], otros + [
                    {"receta_id": rid, "subreceta_id": s} for _, s, _ in lineas if s]):
                errores.append({"fila": n, "campo": "nombre",
                                "mensaje": f"«{nombre}» ya contiene a esta receta (sería un ciclo)"})
                continue
            try:
                cantidad = parse_num(texto_cant)
            except ValueError as e:
                errores.append({"fila": n, "campo": "cantidad", "mensaje": str(e)})
                continue
            if not cantidad or cantidad <= 0:
                errores.append({"fila": n, "campo": "cantidad", "mensaje": "Falta la cantidad"})
                continue
            lineas.append((ing["id"] if ing else None, sub["id"] if sub else None, cantidad))
        if errores:
            return jsonify({"ok": False, "errores": errores}), 400
        with db.lote():
            db.ejecutar("DELETE FROM receta_items WHERE receta_id = ?", (rid,))
            for orden, (iid, sid, cantidad) in enumerate(lineas, start=1):
                db.ejecutar("INSERT INTO receta_items (receta_id, ingrediente_id, subreceta_id, cantidad, orden) "
                            "VALUES (?, ?, ?, ?, ?)", (rid, iid, sid, cantidad, orden))
        flash(f"Guardado: {len(lineas)} ingrediente(s)", "ok")
        return jsonify({"ok": True, "lineas": len(lineas)})

    @app.post("/recetas/<int:rid>/lineas/<int:lid>")
    def linea_editar(rid, lid):
        try:
            cantidad = _num("cantidad", minimo=0)
            if cantidad <= 0:
                raise ValueError("La cantidad tiene que ser mayor a 0")
            db.ejecutar("UPDATE receta_items SET cantidad = ? WHERE id = ? AND receta_id = ?",
                        (cantidad, lid, rid))
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("receta", rid=rid) + "#lineas")

    @app.post("/recetas/<int:rid>/lineas/<int:lid>/borrar")
    def linea_borrar(rid, lid):
        db.ejecutar("DELETE FROM receta_items WHERE id = ? AND receta_id = ?", (lid, rid))
        return redirect(url_for("receta", rid=rid) + "#lineas")

    # ---------- ingredientes
    @app.get("/ingredientes")
    def ingredientes():
        ingredientes, recetas, items, calculo, _ = _datos()
        filas = [{**i,
                  "unidad_base": costeo.unidad_base(i["presentacion_unidad"]),
                  "precio_unitario": calculo["ingredientes"][iid],
                  "usos": len(_usos_ingrediente(iid, recetas, items))}
                 for iid, i in ingredientes.items()]
        return render_template("ingredientes.html", filas=filas)

    @app.get("/ingredientes/grilla")
    def ingredientes_grilla():
        ingredientes, recetas, items, calculo, _ = _datos()
        filas = [{"id": iid, "nombre": i["nombre"], "presentacion": i["presentacion_cantidad"],
                  "unidad": i["presentacion_unidad"], "precio": i["precio"], "notas": i.get("notas") or "",
                  "usos": len(_usos_ingrediente(iid, recetas, items))}
                 for iid, i in ingredientes.items()]
        return render_template("ingredientes_grilla.html", filas=filas)

    @app.post("/ingredientes/grilla")
    def ingredientes_grilla_guardar():
        """Guarda filas nuevas y cambiadas. Si alguna está mal, no guarda ninguna."""
        ingredientes, recetas, items, _, _ = _datos()
        datos = request.get_json(silent=True) or {}
        hoy = date.today().isoformat()
        nombres = {importexport.normalizar(i["nombre"]): iid for iid, i in ingredientes.items()}
        errores, cambios, vistos = [], [], {}
        for fila in datos.get("filas", []):
            n = fila.get("indice")
            iid = fila.get("id")
            nombre = importexport.limpiar_nombre(fila.get("nombre"))
            if iid is not None and iid not in ingredientes:
                errores.append({"fila": n, "campo": "nombre", "mensaje": "Ese ingrediente ya no existe (recargá la página)"})
                continue
            if not nombre:
                errores.append({"fila": n, "campo": "nombre", "mensaje": "Falta el nombre"})
                continue
            clave = importexport.normalizar(nombre)
            otro = nombres.get(clave)
            if (otro is not None and otro != iid) or (clave in vistos and vistos[clave] != iid):
                errores.append({"fila": n, "campo": "nombre", "mensaje": f"«{nombre}» está repetido"})
                continue
            vistos[clave] = iid
            unidad = importexport.unidad(fila.get("unidad"))
            if not unidad:
                errores.append({"fila": n, "campo": "unidad", "mensaje": "Unidad no válida (g, kg, ml, l o u)"})
                continue
            try:
                presentacion = parse_num(fila.get("presentacion"))
                precio = parse_num(fila.get("precio"))
            except ValueError as e:
                errores.append({"fila": n, "campo": "precio", "mensaje": str(e)})
                continue
            if not presentacion or presentacion <= 0:
                errores.append({"fila": n, "campo": "presentacion", "mensaje": "Falta la presentación (cuánto trae)"})
                continue
            if precio is None or precio < 0:
                errores.append({"fila": n, "campo": "precio", "mensaje": "Falta el precio"})
                continue
            if iid is not None:
                actual = ingredientes[iid]
                if (costeo.unidad_base(unidad) != costeo.unidad_base(actual["presentacion_unidad"])
                        and _usos_ingrediente(iid, recetas, items)):
                    errores.append({"fila": n, "campo": "unidad",
                                    "mensaje": "Ya se usa en recetas: no se puede pasar de "
                                               f"{costeo.unidad_base(actual['presentacion_unidad'])} a {costeo.unidad_base(unidad)}"})
                    continue
            cambios.append((iid, nombre, presentacion, unidad, precio,
                            importexport.limpiar_nombre(fila.get("notas")) or None))
        if errores:
            return jsonify({"ok": False, "errores": errores}), 400
        nuevos = actualizados = 0
        with db.lote():
            for iid, nombre, presentacion, unidad, precio, notas in cambios:
                if iid is None:
                    nuevo = db.ejecutar("INSERT INTO ingredientes (nombre, presentacion_cantidad, presentacion_unidad, "
                                        "precio, fecha_precio, notas) VALUES (?, ?, ?, ?, ?, ?)",
                                        (nombre, presentacion, unidad, precio, hoy, notas), devolver_id=True)
                    _registrar_precio(nuevo, presentacion, unidad, precio)
                    nuevos += 1
                    continue
                actual = ingredientes[iid]
                cambio_precio = (presentacion, unidad, precio) != (
                    actual["presentacion_cantidad"], actual["presentacion_unidad"], actual["precio"])
                db.ejecutar("UPDATE ingredientes SET nombre=?, presentacion_cantidad=?, presentacion_unidad=?, precio=?, "
                            "fecha_precio=?, notas=? WHERE id=?",
                            (nombre, presentacion, unidad, precio, hoy if cambio_precio else actual["fecha_precio"],
                             notas, iid))
                if cambio_precio:
                    _registrar_precio(iid, presentacion, unidad, precio)
                actualizados += 1
        flash(f"Guardado: {nuevos} nuevo(s), {actualizados} actualizado(s)", "ok")
        return jsonify({"ok": True, "nuevos": nuevos, "actualizados": actualizados})

    # ---------- precio sugerido por el súper
    CAMPOS_SUGERENCIA = ("fuente", "id", "nombre", "url", "precio", "oferta", "cant", "unidad")

    def _sugerencia(origen):
        """Producto del súper elegido en la búsqueda (viene en la URL o en campos ocultos del formulario)."""
        if not origen.get("sup_id"):
            return None
        s = {c: origen.get(f"sup_{c}", "") for c in CAMPOS_SUGERENCIA}
        s["fuente"] = s["fuente"] if s["fuente"] in fuentes.FUENTES else "supermas"
        s["fuente_nombre"] = fuentes.fuente(s["fuente"]).nombre
        for c in ("precio", "oferta", "cant"):
            try:
                s[c] = float(s[c]) if s[c] not in ("", None) else None
            except ValueError:
                s[c] = None
        if s["unidad"] not in costeo.UNIDADES_PRESENTACION:
            s["unidad"] = None
        if s["url"] and not s["url"].startswith(fuentes.fuente(s["fuente"]).base):
            s["url"] = ""
        return s

    def _prellenar(ing, sug, nombre=None):
        """Valores del formulario: los del ingrediente con la presentación y el precio sugeridos encima."""
        base = dict(ing or {})
        if nombre and not base.get("nombre"):
            base["nombre"] = nombre
        if sug:
            if sug["cant"] and sug["unidad"]:
                base["presentacion_cantidad"], base["presentacion_unidad"] = sug["cant"], sug["unidad"]
            if sug["precio"]:
                base["precio"] = sug["precio"]
        return base

    def _vincular(iid, sug, precio_guardado):
        hoy = date.today().isoformat()
        db.ejecutar("UPDATE ingredientes SET fuente=?, fuente_id=?, fuente_nombre=?, fuente_url=?, fuente_precio=?, "
                    "fuente_oferta=?, fuente_fecha=? WHERE id=?",
                    (sug["fuente"], str(sug["id"]), sug["nombre"], sug["url"], sug["precio"], sug["oferta"], hoy, iid))
        return "supermas" if precio_guardado in (sug["precio"], sug["oferta"]) else "manual"

    def _form_ingrediente():
        unidad = request.form.get("presentacion_unidad", "g")
        if unidad not in costeo.UNIDADES_PRESENTACION:
            raise ValueError("Unidad inválida")
        cantidad = _num("presentacion_cantidad", minimo=0)
        precio = _num("precio", minimo=0)
        if cantidad <= 0:
            raise ValueError("La presentación tiene que ser mayor a 0")
        return _texto("nombre"), cantidad, unidad, precio, _texto("notas", False)

    @app.route("/ingredientes/nuevo", methods=["GET", "POST"])
    def ingrediente_nuevo():
        if request.method == "POST":
            try:
                nombre, cantidad, unidad, precio, notas = _form_ingrediente()
                sug = _sugerencia(request.form)
                with db.lote():
                    iid = db.ejecutar(
                        "INSERT INTO ingredientes (nombre, presentacion_cantidad, presentacion_unidad, "
                        "precio, fecha_precio, notas) VALUES (?, ?, ?, ?, ?, ?)",
                        (nombre, cantidad, unidad, precio, date.today().isoformat(), notas),
                        devolver_id=True)
                    origen = _vincular(iid, sug, precio) if sug else "manual"
                    _registrar_precio(iid, cantidad, unidad, precio, origen)
                flash(f"Ingrediente «{nombre}» agregado" + (f", vinculado a {sug['fuente_nombre']}" if sug else ""), "ok")
                if request.form.get("otro"):
                    return redirect(url_for("ingrediente_nuevo"))
                return redirect(url_for("ingredientes"))
            except ValueError as e:
                flash(str(e), "error")
            except db.ErrorDeIntegridad:
                flash("Ya existe un ingrediente con ese nombre", "error")
        sug = _sugerencia(request.form if request.method == "POST" else request.args)
        form = request.form if request.method == "POST" else _prellenar(None, sug, request.args.get("nombre"))
        return render_template("ingrediente_form.html", ing=None, form=form, historial=[], usos=[], sugerencia=sug)

    @app.route("/ingredientes/<int:iid>", methods=["GET", "POST"])
    def ingrediente_editar(iid):
        ing = db.consultar_uno("SELECT * FROM ingredientes WHERE id = ?", (iid,))
        if not ing:
            abort(404)
        if request.method == "POST":
            try:
                nombre, cantidad, unidad, precio, notas = _form_ingrediente()
                if costeo.unidad_base(unidad) != costeo.unidad_base(ing["presentacion_unidad"]):
                    _, recetas, items, _, _ = _datos()
                    if _usos_ingrediente(iid, recetas, items):
                        raise ValueError("No se puede cambiar de g/ml/u: el ingrediente ya se usa en recetas "
                                         "y las cantidades quedarían mal. Creá uno nuevo.")
                cambio_precio = (cantidad != ing["presentacion_cantidad"] or unidad != ing["presentacion_unidad"]
                                 or precio != ing["precio"])
                fecha = date.today().isoformat() if cambio_precio else ing["fecha_precio"]
                sug = _sugerencia(request.form)
                with db.lote():
                    db.ejecutar(
                        "UPDATE ingredientes SET nombre=?, presentacion_cantidad=?, presentacion_unidad=?, "
                        "precio=?, fecha_precio=?, notas=? WHERE id=?",
                        (nombre, cantidad, unidad, precio, fecha, notas, iid))
                    origen = _vincular(iid, sug, precio) if sug else "manual"
                    if cambio_precio:
                        _registrar_precio(iid, cantidad, unidad, precio, origen)
                flash("Ingrediente guardado" + (f" y vinculado a {sug['fuente_nombre']}" if sug else ""), "ok")
                return redirect(url_for("ingredientes"))
            except ValueError as e:
                flash(str(e), "error")
            except db.ErrorDeIntegridad:
                flash("Ya existe un ingrediente con ese nombre", "error")
        _, recetas, items, _, _ = _datos()
        historial = db.consultar(
            "SELECT * FROM precios_historial WHERE ingrediente_id = ? ORDER BY fecha DESC, id DESC LIMIT 12",
            (iid,))
        for h in historial:
            h["precio_unitario"] = costeo.precio_por_unidad_base(h)
        sug = _sugerencia(request.form if request.method == "POST" else request.args)
        form = request.form if request.method == "POST" else (_prellenar(ing, sug) if sug else None)
        return render_template("ingrediente_form.html", ing=ing, form=form, sugerencia=sug,
                               historial=historial, usos=_usos_ingrediente(iid, recetas, items),
                               precio_unitario=costeo.precio_por_unidad_base(ing),
                               unidad_base=costeo.unidad_base(ing["presentacion_unidad"]))

    @app.post("/ingredientes/<int:iid>/borrar")
    def ingrediente_borrar(iid):
        ingredientes, recetas, items, _, _ = _datos()
        if iid not in ingredientes:
            abort(404)
        usos = _usos_ingrediente(iid, recetas, items)
        if usos:
            flash("No se puede borrar: se usa en " + ", ".join(usos), "error")
            return redirect(url_for("ingrediente_editar", iid=iid))
        db.ejecutar("DELETE FROM ingredientes WHERE id = ?", (iid,))
        flash(f"Ingrediente «{ingredientes[iid]['nombre']}» borrado", "ok")
        return redirect(url_for("ingredientes"))

    # ---------- supermercado: buscar, vincular, consultar
    @app.get("/super/buscar")
    def super_buscar():
        q = (request.args.get("q") or "").strip()
        iid = request.args.get("iid", type=int)
        ing = db.consultar_uno("SELECT * FROM ingredientes WHERE id = ?", (iid,)) if iid else None
        fuente = fuentes.fuente(request.args.get("fuente", "supermas"))
        productos, total, error = [], 0, None
        if q:
            try:
                productos, total = fuente.buscar(q)
            except fuentes.ErrorDeFuente as e:
                error = str(e)
        # el más barato por g / ml / u entre los que tienen la misma unidad que el ingrediente
        base_ing = costeo.unidad_base(ing["presentacion_unidad"]) if ing else None
        comparables = [p for p in productos if p.precio_unitario and (not base_ing or p.unidad_base == base_ing)]
        mas_barato = min(comparables, key=lambda p: p.precio_unitario).id if comparables else None
        return render_template("super_buscar.html", q=q, ing=ing, productos=productos, total=total, error=error,
                               fuente=fuente, mas_barato=mas_barato, base_ing=base_ing,
                               precio_unitario_ing=costeo.precio_por_unidad_base(ing) if ing else None,
                               nombre_nuevo=request.args.get("nombre", ""))

    @app.post("/ingredientes/<int:iid>/desvincular")
    def ingrediente_desvincular(iid):
        db.ejecutar("UPDATE ingredientes SET fuente=NULL, fuente_id=NULL, fuente_nombre=NULL, fuente_url=NULL, "
                    "fuente_precio=NULL, fuente_oferta=NULL, fuente_fecha=NULL WHERE id=?", (iid,))
        flash("Se quitó el vínculo con el súper. El precio quedó como estaba.", "ok")
        return redirect(url_for("ingrediente_editar", iid=iid))

    @app.get("/ingredientes/super")
    def super_actualizar():
        ingredientes, recetas, items, calculo, _ = _datos()
        vinculados = [{**i, "unidad_base": costeo.unidad_base(i["presentacion_unidad"]),
                       "precio_unitario": calculo["ingredientes"][iid],
                       "usos": len(_usos_ingrediente(iid, recetas, items))}
                      for iid, i in ingredientes.items() if i.get("fuente_id")]
        solo = request.args.get("solo", type=int)
        if solo:
            vinculados = [v for v in vinculados if v["id"] == solo]
        sin_vincular = sum(1 for i in ingredientes.values() if not i.get("fuente_id"))
        return render_template("super_actualizar.html", vinculados=vinculados, sin_vincular=sin_vincular,
                               pausa=fuentes.PAUSA_ENTRE_CONSULTAS)

    @app.post("/ingredientes/<int:iid>/super/consultar")
    def super_consultar(iid):
        """Consulta el precio actual de un ingrediente vinculado. Guarda la recomendación, no el precio."""
        ing = db.consultar_uno("SELECT * FROM ingredientes WHERE id = ?", (iid,))
        if not ing or not ing.get("fuente_id"):
            return jsonify({"ok": False, "mensaje": "No está vinculado a un súper"}), 404
        fuente = fuentes.fuente(ing["fuente"])
        try:
            p = fuente.consultar(ing["fuente_id"], ing["fuente_nombre"])
        except fuentes.ErrorDeFuente as e:
            return jsonify({"ok": False, "mensaje": str(e)}), 502
        if not p:
            return jsonify({"ok": True, "encontrado": False,
                            "mensaje": f"No aparece en {fuente.nombre} (¿lo sacaron o cambió el nombre?)"})
        db.ejecutar("UPDATE ingredientes SET fuente_precio=?, fuente_oferta=?, fuente_url=?, fuente_fecha=? WHERE id=?",
                    (p.precio, p.precio_oferta, p.url, date.today().isoformat(), iid))
        actual_unit = costeo.precio_por_unidad_base(ing)
        compara = (p.precio_unitario and actual_unit and p.unidad_base == costeo.unidad_base(ing["presentacion_unidad"]))
        return jsonify({"ok": True, "encontrado": True, "producto": p.dict(),
                        "variacion": (p.precio_unitario / actual_unit - 1) if compara else None})

    @app.post("/ingredientes/super/aplicar")
    def super_aplicar():
        """Guarda los precios que el usuario confirmó (y ajustó) en la pantalla de consulta."""
        ingredientes, recetas, items, _, _ = _datos()
        datos = request.get_json(silent=True) or {}
        errores, cambios = [], []
        for it in datos.get("items", []):
            iid = it.get("id")
            ing = ingredientes.get(iid)
            if not ing:
                continue
            try:
                precio = parse_num(it.get("precio"))
                cant = parse_num(it.get("presentacion")) if it.get("presentacion") not in (None, "") else ing["presentacion_cantidad"]
            except ValueError as e:
                errores.append({"id": iid, "mensaje": str(e)})
                continue
            unidad = it.get("unidad") or ing["presentacion_unidad"]
            if unidad not in costeo.UNIDADES_PRESENTACION or not precio or precio <= 0 or not cant or cant <= 0:
                errores.append({"id": iid, "mensaje": f"«{ing['nombre']}»: precio o presentación inválidos"})
                continue
            if costeo.unidad_base(unidad) != costeo.unidad_base(ing["presentacion_unidad"]) and \
                    _usos_ingrediente(iid, recetas, items):
                errores.append({"id": iid, "mensaje": f"«{ing['nombre']}»: el producto se vende en otra unidad "
                                                      f"({unidad}) y el ingrediente ya se usa en recetas"})
                continue
            cambios.append((iid, cant, unidad, precio, ing))
        if errores:
            return jsonify({"ok": False, "errores": errores}), 400
        hoy = date.today().isoformat()
        with db.lote():
            for iid, cant, unidad, precio, ing in cambios:
                db.ejecutar("UPDATE ingredientes SET presentacion_cantidad=?, presentacion_unidad=?, precio=?, "
                            "fecha_precio=? WHERE id=?", (cant, unidad, precio, hoy, iid))
                origen = "supermas" if precio in (ing.get("fuente_precio"), ing.get("fuente_oferta")) else "manual"
                _registrar_precio(iid, cant, unidad, precio, origen)
        flash(f"Precios actualizados: {len(cambios)} ingrediente(s)", "ok")
        return jsonify({"ok": True, "actualizados": len(cambios)})

    @app.post("/super/probar")
    def super_probar():
        fuente = fuentes.fuente("supermas")
        try:
            productos, total = fuente.buscar("harina", paginas=1)
        except fuentes.ErrorDeFuente as e:
            flash(str(e), "error")
            return redirect(url_for("configuracion"))
        if not productos:
            flash(f"{fuente.nombre} respondió pero no se pudo leer ningún producto: puede que haya cambiado su página.",
                  "error")
        else:
            ej = productos[0]
            flash(f"Conexión con {fuente.nombre} OK: {total} resultados para «harina». "
                  f"Ejemplo: {ej.nombre} a {gs(ej.precio)} Gs", "ok")
        return redirect(url_for("configuracion"))

    # ---------- Excel: importar y exportar
    def _carpeta_importaciones():
        carpeta = Path(app.instance_path) / "importaciones"
        carpeta.mkdir(parents=True, exist_ok=True)
        import time as _t
        for viejo in carpeta.glob("*.json"):  # limpiar vistas previas de más de un día
            if _t.time() - viejo.stat().st_mtime > 86400:
                viejo.unlink(missing_ok=True)
        return carpeta

    @app.get("/importar")
    def importar():
        return render_template("importar.html")

    @app.post("/importar")
    def importar_vista():
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename:
            flash("Elegí un archivo de Excel", "error")
            return redirect(url_for("importar"))
        if not archivo.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("El archivo tiene que ser de Excel (.xlsx). Si es .xls viejo, abrilo en Excel y guardalo como .xlsx", "error")
            return redirect(url_for("importar"))
        import io as _io
        datos = importexport.leer_excel(_io.BytesIO(archivo.read()))
        res = importexport.procesar(datos, aplicar=False)
        token = secrets.token_hex(12)
        (_carpeta_importaciones() / f"{token}.json").write_text(
            json.dumps(datos, ensure_ascii=False, default=str), encoding="utf-8")
        return render_template("importar_vista.html", res=res, token=token, archivo=archivo.filename)

    @app.post("/importar/aplicar")
    def importar_aplicar():
        token = request.form.get("token", "")
        ruta = _carpeta_importaciones() / f"{token}.json"
        if not token.isalnum() or not ruta.exists():
            flash("La vista previa venció. Volvé a subir el archivo.", "error")
            return redirect(url_for("importar"))
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        try:
            res = importexport.importar(datos)
        except db.ErrorDeIntegridad as e:
            flash(f"No se importó nada: {e}", "error")
            return redirect(url_for("importar"))
        ruta.unlink(missing_ok=True)
        partes = []
        if res["ing_nuevos"] or res["ing_actualizados"]:
            partes.append(f"{len(res['ing_nuevos'])} ingrediente(s) nuevo(s) y {len(res['ing_actualizados'])} actualizado(s)")
        if res["rec_nuevas"] or res["rec_actualizadas"]:
            partes.append(f"{len(res['rec_nuevas'])} receta(s) nueva(s) y {len(res['rec_actualizadas'])} actualizada(s)")
        if res["lineas"]:
            partes.append(f"ingredientes cargados en {len(res['lineas'])} receta(s)")
        flash("Importado: " + "; ".join(partes) if partes else "No había cambios para importar", "ok")
        if res["errores"]:
            flash(f"{len(res['errores'])} fila(s) con problemas no se importaron (los viste en la vista previa)", "error")
        return redirect(url_for("productos"))

    def _excel(contenido, nombre):
        from flask import Response
        return Response(contenido, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})

    @app.get("/exportar.xlsx")
    def exportar_excel():
        return _excel(importexport.exportar(), f"ArteSana_{date.today().isoformat()}.xlsx")

    @app.get("/plantilla.xlsx")
    def plantilla_excel():
        return _excel(importexport.exportar(vacio=True), "ArteSana_plantilla.xlsx")

    # ---------- configuración y ejemplo
    @app.route("/configuracion", methods=["GET", "POST"])
    def configuracion():
        if request.method == "POST":
            try:
                redondeo = _num("redondeo", minimo=1)
                margen = _num("margen_minimo", obligatorio=False, minimo=0)
                if margen is not None and margen >= 100:
                    raise ValueError("El margen mínimo tiene que ser menor a 100 %")
                db.guardar_config("redondeo", int(redondeo))
                if margen is not None:
                    db.guardar_config("margen_minimo", margen)
                flash("Configuración guardada", "ok")
                return redirect(url_for("productos"))
            except ValueError as e:
                flash(str(e), "error")
        return render_template("configuracion.html", redondeo=db.obtener_config("redondeo", "100"),
                               margen_minimo=cant(_margen_minimo() * 100), sistema=estado_servicio())

    @app.post("/ejemplo")
    def cargar_ejemplo():
        if db.consultar("SELECT id FROM ingredientes LIMIT 1") or db.consultar("SELECT id FROM recetas LIMIT 1"):
            flash("El ejemplo solo se carga con la base vacía", "error")
        else:
            cargar_datos_ejemplo()
            flash("Ejemplo cargado. Los precios son inventados: reemplazalos por los tuyos.", "ok")
        return redirect(url_for("productos"))


def cargar_datos_ejemplo():
    hoy = date.today().isoformat()
    ingredientes = [
        ("Harina 000", 1, "kg", 6000), ("Maicena", 1, "kg", 14000), ("Manteca", 200, "g", 10000),
        ("Azúcar", 1, "kg", 7000), ("Huevo", 30, "u", 30000), ("Leche entera", 1, "l", 8000),
        ("Bicarbonato de sodio", 100, "g", 3000), ("Polvo de hornear", 100, "g", 4000),
        ("Esencia de vainilla", 100, "ml", 10000), ("Coco rallado", 100, "g", 6000),
        ("Bolsita para alfajor", 100, "u", 50000),
    ]
    ids = {}
    for nombre, c, u, p in ingredientes:
        ids[nombre] = db.ejecutar(
            "INSERT INTO ingredientes (nombre, presentacion_cantidad, presentacion_unidad, precio, "
            "fecha_precio, notas) VALUES (?, ?, ?, ?, ?, ?)",
            (nombre, c, u, p, hoy, "Precio de ejemplo"), devolver_id=True)
        _registrar_precio(ids[nombre], c, u, p, "ejemplo")

    ddl = db.ejecutar(
        "INSERT INTO recetas (nombre, tipo, rinde, unidad_rinde, multiplicador, notas) VALUES (?, ?, ?, ?, ?, ?)",
        ("Dulce de leche casero", "subreceta", 1000, "g", 0, "Rinde 1 kg después de reducir"),
        devolver_id=True)
    alf = db.ejecutar(
        "INSERT INTO recetas (nombre, tipo, rinde, unidad_rinde, multiplicador, notas) VALUES (?, ?, ?, ?, ?, ?)",
        ("Alfajores de maicena", "venta", 30, "u", 3, "Rinde 30 alfajores armados"), devolver_id=True)

    lineas = [
        (ddl, "Leche entera", None, 2000), (ddl, "Azúcar", None, 600),
        (ddl, "Bicarbonato de sodio", None, 2), (ddl, "Esencia de vainilla", None, 5),
        (alf, "Maicena", None, 300), (alf, "Harina 000", None, 200), (alf, "Manteca", None, 200),
        (alf, "Azúcar", None, 150), (alf, "Huevo", None, 3), (alf, "Polvo de hornear", None, 10),
        (alf, "Esencia de vainilla", None, 5), (alf, None, ddl, 600), (alf, "Coco rallado", None, 50),
        (alf, "Bolsita para alfajor", None, 30),
    ]
    for orden, (rid, ing, sub, cantidad) in enumerate(lineas, start=1):
        db.ejecutar(
            "INSERT INTO receta_items (receta_id, ingrediente_id, subreceta_id, cantidad, orden) "
            "VALUES (?, ?, ?, ?, ?)", (rid, ids.get(ing) if ing else None, sub, cantidad, orden))


app = crear_app()
