"""Ícono de ArteSana en la bandeja de Windows (junto al reloj).

Clic en el ícono: abre la app. Clic derecho: todas las opciones del menú.
Se inicia con artesana.bat o con el acceso directo «ArteSana» (sin consola).

    pythonw bandeja.py [--abrir]
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser

import servicio as sv

NOMBRE = "ArteSana"
CREATE_NEW_CONSOLE = 0x00000010


# ------------------------------------------------------------------ imágenes

def imagen(estado="ok"):
    """Ícono de 64 px: color si corre, gris si está detenida; punto rojo si hay error,
    punto ámbar si hay una acción en curso."""
    from PIL import Image, ImageDraw, ImageOps
    base = Image.open(sv.RAIZ / "static" / "iconos" / "icono-192.png").convert("RGBA").resize((64, 64))
    if estado == "detenida":
        gris = ImageOps.grayscale(base.convert("RGB")).convert("RGBA")
        gris.putalpha(base.getchannel("A").point(lambda a: int(a * 0.7)))
        base = gris
    color = {"error": (211, 47, 47, 255), "ocupada": (245, 166, 35, 255)}.get(estado)
    if color:
        dibujo = ImageDraw.Draw(base)
        dibujo.ellipse((40, 40, 63, 63), fill=color, outline=(255, 255, 255, 255), width=3)
    return base


# ------------------------------------------------------------------ lógica (sin dependencia de pystray)

class Controlador:
    """Todo lo que hace el ícono. Recibe el objeto icono de pystray (o uno simulado en las pruebas)."""

    def __init__(self, ejecutar_en_hilo=True):
        self.icon = None
        self.ocupada = None            # texto de la acción en curso
        self.lock = threading.Lock()
        self.ejecutar_en_hilo = ejecutar_en_hilo
        self.detener_hilos = threading.Event()
        self.relanzar = False
        self.anterior = self._foto()
        self.firmas = self._firmas_codigo()
        self._autoinicio = None

    # ---------- estado
    def _foto(self):
        corriendo, _ = sv.servicio_corriendo()
        e = sv.leer_estado()
        return {
            "corriendo": corriendo,
            "servidor": e.get("servidor") if corriendo else "detenido",
            "ultima_revision": e.get("ultima_revision"),
            "actualizacion": e.get("actualizacion"),
            "mensaje_actualizacion": e.get("mensaje_actualizacion", ""),
            "url_red": e.get("url_red", "") if corriendo else "",
            "version": e.get("version") or {},
        }

    def estado_icono(self, foto=None):
        foto = foto or self._foto()
        if self.ocupada:
            return "ocupada"
        if foto["servidor"] == "error":
            return "error"
        return "ok" if foto["corriendo"] else "detenida"

    def texto_estado(self, *_):
        if self.ocupada:
            return f"⏳ {self.ocupada}…"
        foto = self._foto()
        if foto["servidor"] == "error":
            return "⚠ Error: no logra arrancar (ver registro)"
        if foto["corriendo"]:
            return f"● Funcionando en {sv.url_app()}"
        return "○ Detenida"

    def texto_version(self, *_):
        v = self._foto()["version"]
        if not v.get("commit"):
            return "Versión: se identifica en la próxima actualización"
        return f"Versión {v['commit']} {('del ' + v['fecha']) if v.get('fecha') else ''}".strip()

    def texto_tooltip(self):
        foto = self._foto()
        estado = "funcionando" if foto["corriendo"] else "detenida"
        if self.ocupada:
            estado = self.ocupada.lower()
        return f"{NOMBRE} · {estado}"[:120]

    def corriendo(self, *_):
        return self._foto()["corriendo"]

    def detenida(self, *_):
        return not self._foto()["corriendo"] and not self.ocupada

    def libre(self, *_):
        return not self.ocupada

    def hay_url_red(self, *_):
        return bool(self._foto()["url_red"])

    def autoinicio(self, *_):
        if self._autoinicio is None:
            self._autoinicio = sv.autoinicio_activo()
        return self._autoinicio

    # ---------- utilidades
    def avisar(self, mensaje, titulo=NOMBRE):
        sv.log(f"[bandeja] {titulo}: {mensaje}")
        if self.icon is not None:
            try:
                self.icon.notify(mensaje, titulo)
            except Exception:
                pass

    def refrescar(self):
        if self.icon is None:
            return
        try:
            self.icon.icon = imagen(self.estado_icono())
            self.icon.title = self.texto_tooltip()
            self.icon.update_menu()
        except Exception as e:
            sv.log(f"[bandeja] no se pudo refrescar el ícono: {e!r}")

    def accion(self, nombre, funcion):
        """Corre una acción larga sin congelar el ícono; una por vez."""
        if not self.lock.acquire(blocking=False):
            self.avisar(f"Esperá: todavía estoy en «{self.ocupada}».")
            return

        def correr():
            self.ocupada = nombre
            self.refrescar()
            try:
                mensaje = funcion()
                if mensaje:
                    self.avisar(mensaje)
            except Exception as e:
                sv.log(f"[bandeja] error en «{nombre}»: {e!r}")
                self.avisar(f"Algo salió mal en «{nombre}». Revisá el registro.")
            finally:
                self.ocupada = None
                self.anterior = self._foto()   # lo que cambió lo provocamos nosotros: no avisar dos veces
                self.lock.release()
                self.refrescar()

        if self.ejecutar_en_hilo:
            threading.Thread(target=correr, daemon=True).start()
        else:
            correr()

    def consola(self, titulo, argumentos):
        """Abre una ventana de consola para lo que necesita escribir (asistente de red, pip)."""
        py = sv.python_consola()
        comando = f'cmd /c "title {titulo} & "{py}" {argumentos} & echo. & pause"'
        subprocess.Popen(comando, cwd=sv.RAIZ, creationflags=CREATE_NEW_CONSOLE if sv.ES_WINDOWS else 0,
                         shell=not sv.ES_WINDOWS)

    # ---------- acciones del menú
    def abrir(self, *_):
        if self._foto()["corriendo"]:
            sv.abrir_app()
        else:
            self.iniciar(abrir=True)

    def iniciar(self, *_, abrir=False):
        def hacer():
            if sv.cmd_iniciar(abrir=abrir) == 0:
                return "ArteSana está funcionando." + ("" if abrir else " Clic en el ícono para abrirla.")
            return "No se pudo iniciar. Revisá el registro (clic derecho → Ver registro)."
        self.accion("Iniciando", hacer)

    def detener(self, *_):
        self.accion("Deteniendo", lambda: "ArteSana se detuvo." if sv.cmd_detener() == 0 else
                    "No se pudo detener.")

    def reiniciar(self, *_):
        self.accion("Reiniciando", lambda: "ArteSana se reinició." if sv.cmd_reiniciar() == 0 else
                    "No respondió después de reiniciar. Revisá el registro.")

    def actualizar(self, *_):
        def hacer():
            sv.cmd_actualizar()
            e = sv.leer_estado()
            return e.get("mensaje_actualizacion") or "Sin respuesta del servicio."
        self.accion("Buscando actualizaciones", hacer)

    def puede_volver(self, *_):
        return not self.ocupada and sv.puede_volver()

    def volver(self, *_):
        def hacer():
            sv.cmd_volver()
            return sv.leer_estado().get("mensaje_actualizacion") or "Listo."
        self.accion("Volviendo a la versión anterior", hacer)

    def red(self, *_):
        self.consola("ArteSana - usar desde otros equipos", "servicio.py red")

    def copiar_url_red(self, *_):
        url = self._foto()["url_red"]
        if not url:
            return
        if sv.ES_WINDOWS:
            subprocess.run(["clip"], input=url, text=True, creationflags=sv.SIN_VENTANA)
        self.avisar(f"Dirección copiada: {url}\nPegala en el navegador del otro equipo.")

    def ver_registro(self, *_):
        sv.cmd_log()

    def carpeta_datos(self, *_):
        sv.INST.mkdir(parents=True, exist_ok=True)
        if sv.ES_WINDOWS:
            os.startfile(str(sv.INST))

    def alternar_autoinicio(self, *_):
        def hacer():
            sv.cmd_autoinicio()
            self._autoinicio = sv.autoinicio_activo()
            return ("ArteSana va a arrancar sola al prender la computadora." if self._autoinicio
                    else "ArteSana ya no arranca sola con Windows.")
        self.accion("Configurando inicio", hacer)

    def crear_accesos(self, *_):
        self.accion("Creando accesos directos",
                    lambda: "Accesos directos «ArteSana» en el escritorio y el menú Inicio." if sv.cmd_accesos() == 0
                    else "No se pudieron crear los accesos directos.")

    def dependencias(self, *_):
        self.consola("ArteSana - dependencias",
                     '-m pip install --disable-pip-version-check -r requirements.txt')

    def menu_consola(self, *_):
        bat = sv.RAIZ / "artesana.bat"
        subprocess.Popen(f'cmd /c ""{bat}" menu"', cwd=sv.RAIZ,
                         creationflags=CREATE_NEW_CONSOLE if sv.ES_WINDOWS else 0, shell=not sv.ES_WINDOWS)

    def salir(self, *_):
        self.detener_hilos.set()
        if self.icon is not None:
            self.icon.stop()

    def detener_y_salir(self, *_):
        def hacer():
            sv.cmd_detener()
            self.salir()
        self.accion("Deteniendo", hacer)

    # ---------- vigilancia
    def _firmas_codigo(self):
        firmas = {}
        for nombre in ("bandeja.py", "servicio.py"):
            try:
                firmas[nombre] = (sv.RAIZ / nombre).stat().st_mtime
            except OSError:
                firmas[nombre] = 0
        return firmas

    def revisar_cambios(self):
        """Compara con la última foto y avisa lo importante. Devuelve los avisos (para las pruebas)."""
        avisos = []
        if self.ocupada:
            return avisos
        ahora = self._foto()
        antes = self.anterior
        if antes["corriendo"] and not ahora["corriendo"]:
            avisos.append("ArteSana se detuvo.")
        if ahora["servidor"] == "error" and antes["servidor"] != "error":
            avisos.append("ArteSana no logra arrancar. Revisá el registro.")
        if (ahora["ultima_revision"] != antes["ultima_revision"]
                and ahora["actualizacion"] in ("aplicada", "restaurada")):
            avisos.append(ahora["mensaje_actualizacion"] or "ArteSana se actualizó.")
        cambio_visible = any(ahora[k] != antes[k] for k in ("corriendo", "servidor", "url_red", "version"))
        self.anterior = ahora
        for a in avisos:
            self.avisar(a)
        if avisos or cambio_visible:
            self.refrescar()
        if self._firmas_codigo() != self.firmas:
            sv.log("[bandeja] cambió el código del ícono: se relanza")
            self.relanzar = True
            self.salir()
        return avisos

    def vigilar(self):
        while not self.detener_hilos.wait(3):
            try:
                self.revisar_cambios()
            except Exception as e:
                sv.log(f"[bandeja] error vigilando: {e!r}")

    def al_iniciar(self, abrir):
        """Primera vez: accesos directos. Siempre: arrancar el servicio si no corre."""
        marca = sv.INST / "accesos_creados"
        if sv.ES_WINDOWS and not marca.exists():
            try:
                sv.INST.mkdir(parents=True, exist_ok=True)
                sv.cmd_accesos()
                marca.write_text(sv.ahora(), encoding="utf-8")
                (sv.INST / "autoinicio_bandeja").write_text(sv.ahora(), encoding="utf-8")
            except Exception as e:
                sv.log(f"[bandeja] accesos directos: {e!r}")
        elif sv.ES_WINDOWS and not (sv.INST / "autoinicio_bandeja").exists():
            # instalaciones viejas: el inicio con Windows apuntaba a la consola, no al ícono
            if sv.autoinicio_activo():
                sv.crear_acceso(sv.carpeta_especial("Startup"), "", "ArteSana: ícono en la bandeja", minimizado=True)
            (sv.INST / "autoinicio_bandeja").write_text(sv.ahora(), encoding="utf-8")
        if self._foto()["corriendo"]:
            if abrir:
                sv.abrir_app()
        else:
            self.iniciar(abrir=abrir)
        threading.Thread(target=self.vigilar, daemon=True).start()

    # ---------- menú
    def menu(self, pystray):
        Item, Menu = pystray.MenuItem, pystray.Menu
        return Menu(
            Item("Abrir ArteSana", self.abrir, default=True),
            Item(self.texto_estado, None, enabled=False),
            Item(self.texto_version, None, enabled=False),
            Menu.SEPARATOR,
            Item("Iniciar", self.iniciar, visible=self.detenida),
            Item("Detener", self.detener, visible=self.corriendo, enabled=self.libre),
            Item("Reiniciar", self.reiniciar, enabled=self.libre),
            Item("Buscar y aplicar actualizaciones", self.actualizar, enabled=self.libre),
            Menu.SEPARATOR,
            Item("Usar desde otros equipos de la red…", self.red),
            Item("Copiar dirección para otros equipos", self.copiar_url_red, visible=self.hay_url_red),
            Menu.SEPARATOR,
            Item("Ver registro", self.ver_registro),
            Item("Abrir carpeta de datos", self.carpeta_datos),
            Item("Más opciones", Menu(
                Item("Volver a la versión anterior", self.volver, enabled=self.puede_volver),
                Item("Iniciar con Windows", self.alternar_autoinicio, checked=self.autoinicio, enabled=self.libre),
                Item("Crear accesos directos (escritorio y menú Inicio)", self.crear_accesos, enabled=self.libre),
                Item("Reinstalar dependencias…", self.dependencias),
                Item("Menú en consola…", self.menu_consola),
            )),
            Menu.SEPARATOR,
            Item("Salir (la app sigue funcionando)", self.salir),
            Item("Detener y salir", self.detener_y_salir, enabled=self.libre),
        )


# ------------------------------------------------------------------ arranque

def una_sola_instancia():
    """True si es el único ícono abierto. Devuelve también el recurso que hay que mantener vivo."""
    if sv.ES_WINDOWS:
        import ctypes
        k32 = ctypes.windll.kernel32
        mutex = k32.CreateMutexW(None, False, "Local\\ArteSanaBandeja")
        if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            k32.CloseHandle(mutex)
            return False, None
        return True, mutex
    import fcntl
    sv.INST.mkdir(parents=True, exist_ok=True)
    archivo = open(sv.INST / "bandeja.lock", "w")
    try:
        fcntl.flock(archivo, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True, archivo
    except OSError:
        archivo.close()
        return False, None


def liberar(recurso):
    if recurso is None:
        return
    if sv.ES_WINDOWS:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(recurso)
    else:
        recurso.close()


def mensaje_windows(texto):
    if sv.ES_WINDOWS:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, texto, NOMBRE, 0x40)
    else:
        print(texto)


def main():
    abrir = "--abrir" in sys.argv
    unica, recurso = una_sola_instancia()
    if not unica:
        # Ya hay un ícono: solo abrir la app
        if sv.servicio_corriendo()[0]:
            sv.abrir_app()
        else:
            sv.cmd_iniciar(abrir=True)
        return 0
    try:
        import pystray
    except ImportError:
        liberar(recurso)
        mensaje_windows("Falta instalar el componente del ícono.\n\n"
                        "Abrí artesana.bat: lo instala solo y vuelve a abrir el ícono.\n"
                        "Mientras tanto, la app se inicia igual.")
        sv.cmd_iniciar(abrir=True)
        return 1

    ctrl = Controlador()
    icono = pystray.Icon(NOMBRE, imagen(ctrl.estado_icono()), ctrl.texto_tooltip(), menu=ctrl.menu(pystray))
    ctrl.icon = icono

    def preparar(icon):
        icon.visible = True
        ctrl.al_iniciar(abrir)

    sv.log("[bandeja] ícono iniciado")
    icono.run(setup=preparar)
    liberar(recurso)
    if ctrl.relanzar:
        time.sleep(1)
        subprocess.Popen([sv.python_sin_ventana(), str(sv.RAIZ / "bandeja.py")], cwd=sv.RAIZ,
                         creationflags=(sv.DETACHED_PROCESS | sv.CREATE_NEW_PROCESS_GROUP) if sv.ES_WINDOWS else 0,
                         close_fds=True)
    sv.log("[bandeja] ícono cerrado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
