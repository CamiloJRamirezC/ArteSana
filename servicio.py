"""Servicio local de ArteSana.

Corre la app en segundo plano, la vuelve a levantar si se cae y busca
actualizaciones en GitHub. Normalmente lo usás a través de artesana.bat.

Uso:
    python servicio.py iniciar [--abrir]   arranca en segundo plano
    python servicio.py detener
    python servicio.py reiniciar
    python servicio.py actualizar          busca en GitHub y aplica los cambios
    python servicio.py estado [--breve]
    python servicio.py abrir               abre la app en el navegador
    python servicio.py log                 muestra el registro
    python servicio.py autoinicio          activa/desactiva el inicio con Windows
    python servicio.py configurar          crea artesana.env y la clave secreta
    python servicio.py supervisar          (interno) proceso que queda en segundo plano
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
INST = RAIZ / "instance"
CONTROL = INST / "control"
ESTADO = INST / "estado.json"
PIDF = INST / "servicio.pid"
LOGS = INST / "logs"
LOG = LOGS / "servicio.log"
LOG_SERVIDOR = LOGS / "servidor.log"
RESPALDOS = INST / "respaldos"
ENV_FILE = RAIZ / "artesana.env"
ENV_EJEMPLO = RAIZ / "artesana.env.ejemplo"

ES_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
SIN_VENTANA = CREATE_NO_WINDOW if ES_WINDOWS else 0

DEFAULTS = {
    "PUERTO": "5000",
    "HOST": "127.0.0.1",
    "APP_PASSWORD": "",
    "AUTO_ACTUALIZAR": "1",
    "INTERVALO_MINUTOS": "30",
    "RAMA": "main",
    "DATABASE_URL": "",
}
LATIDO_MAX = 30          # segundos sin latido = servicio colgado o apagado
MAX_CAIDAS = 5           # caídas seguidas en 5 minutos antes de rendirse


# ------------------------------------------------------------------ utilidades

def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    try:
        if LOG.exists() and LOG.stat().st_size > 1_000_000:
            LOG.replace(LOG.with_suffix(".log.1"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{ahora()}] {msg}\n")
    except OSError:
        pass


def decir(msg=""):
    """print() que no falla con pythonw (sin consola)."""
    if sys.stdout is not None:
        try:
            print(msg, flush=True)
        except (OSError, UnicodeEncodeError):
            pass


def leer_config():
    cfg = dict(DEFAULTS)
    if ENV_FILE.exists():
        for linea in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            cfg[clave.strip().upper()] = valor.strip().strip('"').strip("'")
    return cfg


def url_app(cfg=None):
    cfg = cfg or leer_config()
    return f"http://127.0.0.1:{cfg['PUERTO']}"


def clave_secreta():
    archivo = INST / "secret_key"
    if not archivo.exists():
        INST.mkdir(parents=True, exist_ok=True)
        archivo.write_text(secrets.token_hex(32), encoding="utf-8")
    return archivo.read_text(encoding="utf-8").strip()


def python_consola():
    """python.exe (aunque el servicio corra con pythonw.exe)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidato = exe.with_name("python.exe")
        if candidato.exists():
            return str(candidato)
    return str(exe)


def python_sin_ventana():
    exe = Path(sys.executable)
    if ES_WINDOWS:
        candidato = exe.with_name("pythonw.exe")
        if candidato.exists():
            return str(candidato)
    return str(exe)


def leer_estado():
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def escribir_estado(estado):
    """Escritura atómica. En Windows el reemplazo falla si la app está leyendo el
    archivo en ese instante: se reintenta y, si no se puede, se deja para el próximo latido."""
    INST.mkdir(parents=True, exist_ok=True)
    tmp = ESTADO.with_suffix(".tmp")
    for _ in range(5):
        try:
            tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, ESTADO)
            return True
        except OSError:
            time.sleep(0.1)
    return False


def pid_vivo(pid):
    if not pid:
        return False
    if ES_WINDOWS:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        codigo = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(codigo))
        k32.CloseHandle(h)
        return codigo.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    try:  # un proceso zombi ya terminó aunque todavía exista
        estado = Path(f"/proc/{int(pid)}/stat").read_text().split(")")[-1].split()[0]
        return estado != "Z"
    except OSError:
        return True


def matar_pid(pid):
    if ES_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=SIN_VENTANA)
    else:
        import signal
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


def servicio_corriendo():
    try:
        pid = int(PIDF.read_text().strip())
    except (OSError, ValueError):
        return False, None
    estado = leer_estado()
    latido = estado.get("latido_ts", 0)
    return pid_vivo(pid) and time.time() - latido < LATIDO_MAX, pid


def app_responde(cfg=None, timeout=3):
    try:
        with urllib.request.urlopen(url_app(cfg) + "/salud", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def enviar_orden(accion):
    CONTROL.mkdir(parents=True, exist_ok=True)
    (CONTROL / accion).write_text(ahora(), encoding="utf-8")


# ------------------------------------------------------------------ git

def git(*args, timeout=60):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"   # nunca quedarse esperando una contraseña
    try:
        r = subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True,
                           timeout=timeout, env=env, creationflags=SIN_VENTANA,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "").strip() + ("\n" + r.stderr.strip() if r.returncode and r.stderr else "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def hay_git():
    return shutil.which("git") is not None and (RAIZ / ".git").exists()


def version_actual():
    if not hay_git():
        return {"commit": "", "fecha": "", "mensaje": ""}
    _, commit = git("rev-parse", "--short", "HEAD")
    _, fecha = git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M")
    _, mensaje = git("log", "-1", "--format=%s")
    return {"commit": commit, "fecha": fecha, "mensaje": mensaje}


def respaldar_bd():
    bd = INST / "artesana.db"
    if not bd.exists():
        return None
    RESPALDOS.mkdir(parents=True, exist_ok=True)
    destino = RESPALDOS / f"artesana-{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(bd, destino)
    for viejo in sorted(RESPALDOS.glob("artesana-*.db"))[:-10]:
        viejo.unlink(missing_ok=True)
    return destino


def instalar_dependencias():
    code = subprocess.run(
        [python_consola(), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
         str(RAIZ / "requirements.txt")],
        cwd=RAIZ, capture_output=True, text=True, creationflags=SIN_VENTANA)
    if code.returncode:
        log("Error instalando dependencias: " + (code.stderr or "")[-800:])
    return code.returncode == 0


def revisar(aplicar, cfg):
    """Busca cambios en GitHub y, si aplicar=True, los baja."""
    res = {"ultima_revision": ahora(), "pendientes": 0, "cambios": [], "aplicada": False}
    if shutil.which("git") is None:
        return {**res, "actualizacion": "sin_git",
                "mensaje_actualizacion": "Git no está instalado: no se pueden buscar actualizaciones"}
    if not (RAIZ / ".git").exists():
        return {**res, "actualizacion": "sin_repo",
                "mensaje_actualizacion": "La carpeta no se descargó con git clone: no se puede actualizar sola"}
    rama = cfg["RAMA"]
    code, salida = git("fetch", "--quiet", "origin", rama, timeout=120)
    if code:
        log("git fetch falló: " + salida[-400:])
        return {**res, "actualizacion": "error",
                "mensaje_actualizacion": "No se pudo conectar con GitHub (¿hay internet?)"}
    _, local = git("rev-parse", "HEAD")
    _, remoto = git("rev-parse", f"origin/{rama}")
    _, cuenta = git("rev-list", "--count", f"HEAD..origin/{rama}")
    pendientes = int(cuenta) if cuenta.isdigit() else 0
    res["pendientes"] = pendientes
    if local == remoto or pendientes == 0:
        return {**res, "actualizacion": "al_dia", "mensaje_actualizacion": "Al día con GitHub"}
    if not aplicar:
        return {**res, "actualizacion": "disponible",
                "mensaje_actualizacion": f"Hay {pendientes} cambio(s) nuevo(s) en GitHub"}
    _, modificados = git("status", "--porcelain", "--untracked-files=no")
    if modificados.strip():
        return {**res, "actualizacion": "cambios_locales",
                "mensaje_actualizacion": "Hay archivos modificados en esta computadora; no se actualiza para no pisarlos"}
    _, archivos = git("diff", "--name-only", "HEAD", f"origin/{rama}")
    cambios = [a for a in archivos.splitlines() if a.strip()]
    respaldo = respaldar_bd()
    code, salida = git("merge", "--ff-only", f"origin/{rama}")
    if code:
        log("git merge falló: " + salida[-400:])
        return {**res, "actualizacion": "error",
                "mensaje_actualizacion": "No se pudo aplicar la actualización (mirá el registro)"}
    if "requirements.txt" in cambios:
        instalar_dependencias()
    log(f"Actualizado {local[:7]} -> {remoto[:7]} ({pendientes} cambio(s)); respaldo: {respaldo}")
    return {**res, "actualizacion": "aplicada", "cambios": cambios, "aplicada": True,
            "mensaje_actualizacion": f"Actualizado: {pendientes} cambio(s) aplicados"}


# ------------------------------------------------------------------ supervisor

def lanzar_supervisor():
    kw = dict(cwd=RAIZ, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    args = [python_sin_ventana(), str(RAIZ / "servicio.py"), "supervisar"]
    if ES_WINDOWS:
        base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen(args, creationflags=base | CREATE_BREAKAWAY_FROM_JOB, **kw)
        except OSError:
            subprocess.Popen(args, creationflags=base, **kw)
    else:
        subprocess.Popen(args, start_new_session=True, **kw)


class Supervisor:
    def __init__(self):
        self.hijo = None
        self.log_hijo = None
        self.caidas = []
        self.estado = {}
        self.ultimo_guardado = 0

    def guardar(self, forzar=False, **cambios):
        self.estado.update(cambios)
        if forzar or cambios or time.time() - self.ultimo_guardado > 3:
            self.estado["latido"] = ahora()
            self.estado["latido_ts"] = time.time()
            escribir_estado(self.estado)
            self.ultimo_guardado = time.time()

    def lanzar_app(self):
        cfg = leer_config()
        LOGS.mkdir(parents=True, exist_ok=True)
        if LOG_SERVIDOR.exists() and LOG_SERVIDOR.stat().st_size > 2_000_000:
            LOG_SERVIDOR.replace(LOG_SERVIDOR.with_suffix(".log.1"))
        env = os.environ.copy()
        env.update({
            "PORT": cfg["PUERTO"], "HOST": cfg["HOST"], "APP_PASSWORD": cfg["APP_PASSWORD"],
            "SECRET_KEY": clave_secreta(), "ARTESANA_CONTROL": str(CONTROL),
            "ARTESANA_ESTADO": str(ESTADO), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        })
        if cfg["DATABASE_URL"]:
            env["DATABASE_URL"] = cfg["DATABASE_URL"]
        if cfg["HOST"] not in ("127.0.0.1", "localhost") and not cfg["APP_PASSWORD"]:
            log("AVISO: la app es visible en la red y no tiene APP_PASSWORD")
        self.log_hijo = LOG_SERVIDOR.open("a", encoding="utf-8")
        self.log_hijo.write(f"\n===== {ahora()} inicio del servidor =====\n")
        self.log_hijo.flush()
        self.hijo = subprocess.Popen([python_consola(), str(RAIZ / "servidor.py")], cwd=RAIZ, env=env,
                                     stdout=self.log_hijo, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=SIN_VENTANA)
        log(f"Servidor iniciado (pid {self.hijo.pid}) en {url_app(cfg)}")
        self.guardar(forzar=True, servidor="corriendo", servidor_pid=self.hijo.pid,
                     servidor_iniciado=ahora(), url=url_app(cfg), host=cfg["HOST"],
                     version=version_actual(), mensaje="")

    def parar_app(self):
        if self.hijo and self.hijo.poll() is None:
            self.hijo.terminate()
            try:
                self.hijo.wait(10)
            except subprocess.TimeoutExpired:
                self.hijo.kill()
                self.hijo.wait(5)
        self.hijo = None
        if self.log_hijo:
            self.log_hijo.close()
            self.log_hijo = None

    def reiniciar_app(self, motivo):
        log(f"Reiniciando servidor: {motivo}")
        self.guardar(forzar=True, servidor="reiniciando")
        self.parar_app()
        self.caidas.clear()
        self.lanzar_app()

    def procesar_revision(self, res):
        self.guardar(forzar=True, **{k: v for k, v in res.items() if k != "aplicada"})
        if not res.get("aplicada"):
            return "seguir"
        if "servicio.py" in res.get("cambios", []):
            log("Cambió servicio.py: se relanza el servicio completo")
            return "relanzar"
        self.reiniciar_app("actualización aplicada")
        return "seguir"

    def correr(self):
        INST.mkdir(parents=True, exist_ok=True)
        CONTROL.mkdir(parents=True, exist_ok=True)
        for orden in CONTROL.iterdir():
            try:
                orden.unlink(missing_ok=True)
            except OSError:
                pass
        PIDF.write_text(str(os.getpid()))
        cfg = leer_config()
        log(f"Servicio iniciado (pid {os.getpid()})")
        anterior = leer_estado()
        self.estado = {
            "pid": os.getpid(), "iniciado": ahora(),
            "auto_actualizar": cfg["AUTO_ACTUALIZAR"] == "1",
            "intervalo_minutos": int(cfg["INTERVALO_MINUTOS"] or 30),
            **{k: anterior[k] for k in ("ultima_revision", "actualizacion", "mensaje_actualizacion",
                                        "pendientes") if k in anterior},
        }
        proxima_revision = time.time() + 20
        salida = "detener"
        try:
            self.lanzar_app()
            while True:
                ordenes = set()
                for archivo in CONTROL.iterdir():
                    try:
                        archivo.unlink(missing_ok=True)
                        ordenes.add(archivo.name)
                    except OSError:
                        pass  # la app todavía lo está escribiendo; se toma en la próxima vuelta

                if "detener" in ordenes:
                    log("Orden recibida: detener")
                    break
                if "actualizar" in ordenes:
                    log("Orden recibida: buscar y aplicar actualizaciones")
                    self.guardar(forzar=True, actualizacion="buscando",
                                 mensaje_actualizacion="Buscando actualizaciones…")
                    if self.procesar_revision(revisar(True, leer_config())) == "relanzar":
                        salida = "relanzar"
                        break
                    proxima_revision = time.time() + int(cfg["INTERVALO_MINUTOS"] or 30) * 60
                elif "reiniciar" in ordenes:
                    self.reiniciar_app("pedido del usuario")

                if self.hijo is not None and self.hijo.poll() is not None:
                    codigo = self.hijo.returncode
                    self.parar_app()
                    self.caidas = [t for t in self.caidas if time.time() - t < 300] + [time.time()]
                    if len(self.caidas) >= MAX_CAIDAS:
                        log(f"El servidor se cayó {MAX_CAIDAS} veces seguidas; queda detenido. Ver servidor.log")
                        self.guardar(forzar=True, servidor="error",
                                     mensaje="El servidor no logra arrancar. Revisá el registro y reiniciá.")
                    else:
                        log(f"El servidor se cerró (código {codigo}); se vuelve a iniciar")
                        time.sleep(2)
                        self.lanzar_app()

                if time.time() >= proxima_revision:
                    cfg = leer_config()
                    aplicar = cfg["AUTO_ACTUALIZAR"] == "1"
                    if self.procesar_revision(revisar(aplicar, cfg)) == "relanzar":
                        salida = "relanzar"
                        break
                    proxima_revision = time.time() + int(cfg["INTERVALO_MINUTOS"] or 30) * 60

                self.guardar()
                time.sleep(1)
        except Exception as e:  # que quede registrado cualquier error inesperado
            log(f"Error inesperado en el servicio: {e!r}")
            raise
        finally:
            self.parar_app()
            if salida == "relanzar":
                lanzar_supervisor()
            else:
                self.guardar(forzar=True, servidor="detenido")
                log("Servicio detenido")
                try:
                    if PIDF.read_text().strip() == str(os.getpid()):
                        PIDF.unlink()
                except OSError:
                    pass


# ------------------------------------------------------------------ comandos

def esperar(condicion, segundos, puntos=True):
    fin = time.time() + segundos
    while time.time() < fin:
        if condicion():
            if puntos:
                decir("")
            return True
        if puntos and sys.stdout is not None:
            sys.stdout.write(".")
            sys.stdout.flush()
        time.sleep(1)
    if puntos:
        decir("")
    return False


def cmd_configurar():
    INST.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists() and ENV_EJEMPLO.exists():
        shutil.copy(ENV_EJEMPLO, ENV_FILE)
        decir("Se creó artesana.env con la configuración por defecto.")
    clave_secreta()
    return 0


def cmd_iniciar(abrir=False):
    cmd_configurar()
    cfg = leer_config()
    corriendo, _ = servicio_corriendo()
    if corriendo:
        decir(f"ArteSana ya está corriendo en {url_app(cfg)}")
    else:
        if app_responde(cfg, timeout=2):
            decir(f"El puerto {cfg['PUERTO']} ya está ocupado por otra copia de la app o por otro programa.")
            decir("Detenela primero o cambiá PUERTO en artesana.env.")
            return 1
        decir("Iniciando ArteSana en segundo plano", )
        lanzar_supervisor()
        if not esperar(lambda: app_responde(cfg, 2), 45):
            decir("No respondió a tiempo. Últimas líneas del registro:")
            mostrar_log(15)
            return 1
        decir(f"Listo: {url_app(cfg)}")
    if abrir:
        webbrowser.open(url_app(cfg))
    return 0


def cmd_detener():
    corriendo, pid = servicio_corriendo()
    if not corriendo and not (pid and pid_vivo(pid)):
        decir("ArteSana no está corriendo.")
        return 0
    decir("Deteniendo ArteSana")
    enviar_orden("detener")
    if not esperar(lambda: not pid_vivo(pid), 20):
        decir("No se detuvo solo; se fuerza el cierre.")
        estado = leer_estado()
        if estado.get("servidor_pid"):
            matar_pid(estado["servidor_pid"])
        matar_pid(pid)
        PIDF.unlink(missing_ok=True)
    decir("Detenido.")
    return 0


def cmd_reiniciar():
    corriendo, _ = servicio_corriendo()
    if not corriendo:
        return cmd_iniciar()
    cfg = leer_config()
    previo = leer_estado().get("servidor_iniciado")
    decir("Reiniciando")
    enviar_orden("reiniciar")
    ok = esperar(lambda: leer_estado().get("servidor_iniciado") != previo and app_responde(cfg, 2), 60)
    decir(f"Listo: {url_app(cfg)}" if ok else "No respondió a tiempo; revisá el registro (opción Ver registro).")
    return 0 if ok else 1


def cmd_actualizar():
    corriendo, _ = servicio_corriendo()
    cfg = leer_config()
    if not corriendo:
        decir("Buscando actualizaciones en GitHub…")
        res = revisar(True, cfg)
        estado = leer_estado()
        estado.update({k: v for k, v in res.items() if k != "aplicada"})
        escribir_estado(estado)
        decir(res["mensaje_actualizacion"])
        return 0 if res["actualizacion"] in ("al_dia", "aplicada") else 1
    previa = leer_estado().get("ultima_revision")
    decir("Buscando actualizaciones en GitHub")
    enviar_orden("actualizar")
    esperar(lambda: leer_estado().get("ultima_revision") != previa
            and leer_estado().get("actualizacion") != "buscando", 180)
    estado = leer_estado()
    decir(estado.get("mensaje_actualizacion", "Sin respuesta del servicio"))
    if estado.get("actualizacion") == "aplicada":
        decir("Reiniciando con la versión nueva")
        esperar(lambda: app_responde(cfg, 2), 60)
        v = leer_estado().get("version", {})
        decir(f"Versión actual: {v.get('commit', '')} {v.get('fecha', '')} - {v.get('mensaje', '')}")
    return 0


def cmd_estado(breve=False):
    corriendo, pid = servicio_corriendo()
    estado = leer_estado()
    cfg = leer_config()
    v = estado.get("version") or version_actual()
    if corriendo:
        linea = f"  Estado: CORRIENDO en {url_app(cfg)}"
        if estado.get("servidor") == "error":
            linea = "  Estado: ERROR - " + estado.get("mensaje", "")
    else:
        linea = "  Estado: DETENIDO"
    decir(linea)
    if v.get("commit"):
        decir(f"  Versión: {v['commit']} del {v.get('fecha', '')}")
    if estado.get("mensaje_actualizacion"):
        decir(f"  GitHub: {estado['mensaje_actualizacion']} (revisado {estado.get('ultima_revision', '')})")
    if breve:
        return 0
    decir(f"  Actualización automática: {'sí' if cfg['AUTO_ACTUALIZAR'] == '1' else 'no'}, "
          f"cada {cfg['INTERVALO_MINUTOS']} min, rama {cfg['RAMA']}")
    decir(f"  Visible en: {'solo esta computadora' if cfg['HOST'] in ('127.0.0.1', 'localhost') else 'la red local'}")
    decir(f"  Contraseña: {'sí' if cfg['APP_PASSWORD'] else 'no'}")
    if corriendo:
        decir(f"  Proceso del servicio: {pid} · iniciado {estado.get('iniciado', '')}")
    decir(f"  Registro: {LOG}")
    return 0


def mostrar_log(lineas=40):
    for archivo in (LOG, LOG_SERVIDOR):
        if archivo.exists():
            decir(f"--- {archivo.name} ---")
            contenido = archivo.read_text(encoding="utf-8", errors="replace").splitlines()
            for linea in contenido[-lineas:]:
                decir(linea)


def cmd_log():
    if ES_WINDOWS and LOG.exists():
        os.startfile(str(LOG))  # se abre con el Bloc de notas
        if LOG_SERVIDOR.exists():
            os.startfile(str(LOG_SERVIDOR))
    else:
        mostrar_log()
    return 0


def cmd_autoinicio():
    if not ES_WINDOWS:
        decir("El inicio automático solo está disponible en Windows.")
        return 1
    carpeta = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    acceso = carpeta / "ArteSana.lnk"
    if acceso.exists():
        acceso.unlink()
        decir("Inicio automático DESACTIVADO.")
        return 0

    def q(texto):
        return str(texto).replace("'", "''")

    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(acceso)}');"
        f"$s.TargetPath='{q(python_sin_ventana())}';"
        f"$s.Arguments='\"{q(RAIZ / 'servicio.py')}\" iniciar';"
        f"$s.WorkingDirectory='{q(RAIZ)}';$s.WindowStyle=7;"
        f"$s.Description='ArteSana en segundo plano';$s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True)
    if r.returncode or not acceso.exists():
        decir("No se pudo crear el acceso directo: " + (r.stderr or "")[-300:])
        return 1
    decir("Inicio automático ACTIVADO: ArteSana arranca sola al entrar a Windows.")
    return 0


def main(argv):
    if not argv:
        decir(__doc__)
        return 0
    cmd, opciones = argv[0], set(argv[1:])
    if cmd == "supervisar":
        Supervisor().correr()
        return 0
    comandos = {
        "iniciar": lambda: cmd_iniciar(abrir="--abrir" in opciones),
        "detener": cmd_detener,
        "reiniciar": cmd_reiniciar,
        "actualizar": cmd_actualizar,
        "estado": lambda: cmd_estado(breve="--breve" in opciones),
        "abrir": lambda: (webbrowser.open(url_app()), 0)[1],
        "log": cmd_log,
        "autoinicio": cmd_autoinicio,
        "configurar": cmd_configurar,
    }
    if cmd not in comandos:
        decir(f"Comando desconocido: {cmd}\n{__doc__}")
        return 2
    return comandos[cmd]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
