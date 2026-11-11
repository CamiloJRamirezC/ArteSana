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
    python servicio.py red                 revisa y configura el acceso desde otros equipos
    python servicio.py accesos             crea accesos directos en el escritorio y el menú Inicio
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
    "REPOSITORIO": "CamiloJRamirezC/ArteSana",
    "GITHUB_TOKEN": "",
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


def es_local(host):
    return host in ("127.0.0.1", "localhost", "::1", "")


def ip_local():
    """IP de esta PC en la red (no envía datos: solo elige la interfaz de salida)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""
    finally:
        s.close()


def url_red(cfg=None):
    cfg = cfg or leer_config()
    if es_local(cfg["HOST"]):
        return ""
    ip = ip_local() if cfg["HOST"] in ("0.0.0.0", "::") else cfg["HOST"]
    return f"http://{ip}:{cfg['PUERTO']}" if ip else ""


def guardar_en_env(clave, valor):
    """Cambia (o agrega) una línea CLAVE=valor en artesana.env sin tocar el resto."""
    if not ENV_FILE.exists():
        cmd_configurar()
    lineas = ENV_FILE.read_text(encoding="utf-8-sig").splitlines() if ENV_FILE.exists() else []
    nueva, hecho = f"{clave}={valor}", False
    for i, linea in enumerate(lineas):
        if linea.strip().split("=", 1)[0].strip().upper() == clave and not linea.strip().startswith("#"):
            lineas[i], hecho = nueva, True
    if not hecho:
        lineas.append(nueva)
    ENV_FILE.write_text("\n".join(lineas) + "\n", encoding="utf-8")


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

def buscar_git():
    """Ruta a git.exe: en el PATH o donde lo dejan el instalador de Git y GitHub Desktop."""
    exe = shutil.which("git")
    if exe:
        return exe
    if ES_WINDOWS:
        candidatos = []
        for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(var):
                candidatos.append(Path(os.environ[var]) / "Git" / "cmd" / "git.exe")
        if os.environ.get("LOCALAPPDATA"):
            local = Path(os.environ["LOCALAPPDATA"])
            candidatos.append(local / "Programs" / "Git" / "cmd" / "git.exe")
            candidatos += sorted(local.glob("GitHubDesktop/app-*/resources/app/git/cmd/git.exe"), reverse=True)
        for c in candidatos:
            if c.exists():
                return str(c)
    return None


def modo_actualizacion():
    """'git' si la carpeta es un clon y hay git; si no, 'zip' (descarga directa de GitHub)."""
    return "git" if (RAIZ / ".git").exists() and buscar_git() else "zip"


def git(*args, timeout=60):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"   # nunca quedarse esperando una contraseña
    exe = buscar_git() or "git"
    try:
        r = subprocess.run([exe, *args], cwd=RAIZ, capture_output=True, text=True,
                           timeout=timeout, env=env, creationflags=SIN_VENTANA,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "").strip() + ("\n" + r.stderr.strip() if r.returncode and r.stderr else "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def version_actual():
    if modo_actualizacion() == "zip":
        v = leer_version_zip()
        return {"commit": (v.get("sha") or "")[:7], "fecha": v.get("fecha", ""),
                "mensaje": v.get("mensaje", "") if v.get("sha") else "Versión sin identificar: se completa en la próxima actualización"}
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
    res = {"ultima_revision": ahora(), "pendientes": 0, "cambios": [], "aplicada": False,
           "modo_actualizacion": modo_actualizacion()}
    if res["modo_actualizacion"] == "zip":
        return revisar_zip(aplicar, cfg, res)
    rama = cfg["RAMA"]
    code, salida = git("fetch", "--quiet", "origin", rama, timeout=120)
    if code:
        log("git fetch falló: " + salida[-400:])
        return {**res, "actualizacion": "error",
                "mensaje_actualizacion": "No se pudo conectar con GitHub (¿hay internet?)"}
    if VERSION_ZIP.exists():
        # Antes se actualizaba por descarga directa y ahora hay git: alinear git con esos archivos
        anterior = leer_version_zip().get("sha")
        if anterior and git("cat-file", "-e", anterior + "^{commit}")[0] == 0:
            git("reset", "--mixed", "--quiet", anterior)
            log(f"Se pasó de descarga directa a git (versión {anterior[:7]})")
        VERSION_ZIP.unlink(missing_ok=True)
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


# ------------------------------------------------------------------ actualización sin git

VERSION_ZIP = INST / "version.json"
PROTEGIDOS = {"instance", ".venv", "venv", ".git", "artesana.env", "__pycache__", ".pytest_cache"}


def _protegido(rel):
    partes = rel.replace("\\", "/").split("/")
    return partes[0] in PROTEGIDOS or "__pycache__" in partes


def _sha256(datos):
    import hashlib
    return hashlib.sha256(datos).hexdigest()


def leer_version_zip():
    try:
        return json.loads(VERSION_ZIP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _github(url, cfg):
    cabeceras = {"User-Agent": "ArteSana-actualizador", "Accept": "application/vnd.github+json"}
    if cfg.get("GITHUB_TOKEN"):
        cabeceras["Authorization"] = f"Bearer {cfg['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=cabeceras), timeout=120) as r:
        return r.read()


def _fecha_local(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return ""


def revisar_zip(aplicar, cfg, res):
    """Actualiza descargando el ZIP del repositorio: no necesita git."""
    import io
    import urllib.error
    import zipfile

    api = os.environ.get("ARTESANA_GITHUB_API", "https://api.github.com")
    descargas = os.environ.get("ARTESANA_GITHUB_ZIP", "https://codeload.github.com")
    repo, rama = cfg["REPOSITORIO"], cfg["RAMA"]
    try:
        datos = json.loads(_github(f"{api}/repos/{repo}/commits/{rama}", cfg))
        sha = datos["sha"]
        fecha = _fecha_local(datos["commit"]["committer"]["date"])
        mensaje = datos["commit"]["message"].splitlines()[0]
    except urllib.error.HTTPError as e:
        log(f"GitHub respondió {e.code} al consultar {repo}")
        texto = {404: f"No se encontró el repositorio {repo} (si es privado, cargá GITHUB_TOKEN en artesana.env)",
                 403: "GitHub limitó las consultas por un rato; se vuelve a intentar más tarde"}.get(
            e.code, f"GitHub respondió con error {e.code}")
        return {**res, "actualizacion": "error", "mensaje_actualizacion": texto}
    except Exception as e:
        log(f"No se pudo consultar GitHub: {e!r}")
        return {**res, "actualizacion": "error",
                "mensaje_actualizacion": "No se pudo conectar con GitHub (¿hay internet?)"}

    actual = leer_version_zip()
    if actual.get("sha") == sha:
        return {**res, "actualizacion": "al_dia", "mensaje_actualizacion": "Al día con GitHub"}
    res["pendientes"] = 1
    if not aplicar:
        return {**res, "actualizacion": "disponible",
                "mensaje_actualizacion": f"Hay una versión nueva en GitHub ({fecha}: {mensaje})"}

    # No pisar archivos que se modificaron a mano después de la última actualización
    modificados = [rel for rel, h in actual.get("archivos", {}).items()
                   if (RAIZ / rel).exists() and _sha256((RAIZ / rel).read_bytes()) != h]
    if modificados:
        log("No se actualiza; archivos modificados a mano: " + ", ".join(modificados[:10]))
        return {**res, "actualizacion": "cambios_locales",
                "mensaje_actualizacion": "Hay archivos modificados en esta computadora (" +
                                         ", ".join(modificados[:3]) + "); no se actualiza para no pisarlos"}

    try:
        contenido_zip = _github(f"{descargas}/{repo}/zip/{sha}", cfg)
        paquete = zipfile.ZipFile(io.BytesIO(contenido_zip))
        nuevos = {}
        for info in paquete.infolist():
            if info.is_dir() or "/" not in info.filename:
                continue
            rel = info.filename.split("/", 1)[1]
            if rel and not _protegido(rel):
                nuevos[rel] = paquete.read(info)
    except Exception as e:
        log(f"No se pudo descargar la actualización: {e!r}")
        return {**res, "actualizacion": "error",
                "mensaje_actualizacion": "No se pudo descargar la actualización (¿hay internet?)"}
    if "servicio.py" not in nuevos or "app.py" not in nuevos:
        log("El ZIP descargado no parece ser ArteSana; no se aplica")
        return {**res, "actualizacion": "error", "mensaje_actualizacion": "La descarga no es válida; no se aplicó"}

    respaldo = respaldar_bd()
    cambios, fallidos, registro = [], [], {}
    for rel, datos_archivo in nuevos.items():
        destino = RAIZ / rel
        registro[rel] = _sha256(datos_archivo)
        try:
            if destino.exists() and destino.read_bytes() == datos_archivo:
                continue
            destino.parent.mkdir(parents=True, exist_ok=True)
            temporal = destino.with_name(destino.name + ".nuevo")
            temporal.write_bytes(datos_archivo)
            os.replace(temporal, destino)
            cambios.append(rel)
        except OSError as e:  # ej. la planilla abierta en Excel
            fallidos.append(rel)
            registro[rel] = actual.get("archivos", {}).get(rel, "")
            log(f"No se pudo reemplazar {rel}: {e}")
    for rel in actual.get("archivos", {}):
        if rel not in nuevos and (RAIZ / rel).exists():
            try:
                (RAIZ / rel).unlink()
                cambios.append(rel)
            except OSError:
                pass

    INST.mkdir(parents=True, exist_ok=True)
    VERSION_ZIP.write_text(json.dumps({
        # Si algo no se pudo copiar, se guarda la versión anterior para reintentar en la próxima revisión
        "sha": actual.get("sha", "") if fallidos else sha,
        "fecha": fecha, "mensaje": mensaje, "instalado": ahora(), "archivos": registro,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    if "requirements.txt" in cambios:
        instalar_dependencias()
    log(f"Actualizado (descarga directa) a {sha[:7]} - {mensaje}; {len(cambios)} archivo(s); respaldo: {respaldo}")
    texto = f"Actualizado a la versión del {fecha}"
    if fallidos:
        texto += f" (faltó: {', '.join(fallidos[:3])}; se reintenta en la próxima revisión)"
    return {**res, "actualizacion": "aplicada", "cambios": cambios, "aplicada": True,
            "mensaje_actualizacion": texto}


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
                     url_red=url_red(cfg), modo_actualizacion=modo_actualizacion(),
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
            "modo_actualizacion": modo_actualizacion(),
            # No se arrastra el mensaje anterior: puede venir de una versión vieja del servicio
            "ultima_revision": anterior.get("ultima_revision", ""),
            "actualizacion": "pendiente",
            "mensaje_actualizacion": "Se revisa GitHub en unos segundos…",
        }
        # Si alguien reemplaza servicio.py en disco (actualización manual), el servicio se relanza solo
        archivo_servicio = RAIZ / "servicio.py"
        firma_servicio = archivo_servicio.stat().st_mtime if archivo_servicio.exists() else 0
        proxima_revision = time.time() + 8
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
                    # Se relanza el servicio completo (no solo la web) para cargar cualquier código nuevo
                    log("Orden recibida: reiniciar (servicio completo)")
                    salida = "relanzar"
                    break

                try:
                    if archivo_servicio.stat().st_mtime != firma_servicio:
                        log("servicio.py cambió en disco: se relanza el servicio con el código nuevo")
                        salida = "relanzar"
                        break
                except OSError:
                    pass

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
    if corriendo and url_red(cfg):
        decir(f"  Desde otros equipos: {url_red(cfg)}")
    if breve:
        return 0
    decir(f"  Actualización automática: {'sí' if cfg['AUTO_ACTUALIZAR'] == '1' else 'no'}, "
          f"cada {cfg['INTERVALO_MINUTOS']} min, rama {cfg['RAMA']}")
    decir(f"  Visible en: {'solo esta computadora' if es_local(cfg['HOST']) else 'la red local'}")
    modo = modo_actualizacion()
    decir(f"  Actualiza con: {'git (' + (buscar_git() or '') + ')' if modo == 'git' else 'descarga directa desde GitHub (no necesita git)'}")
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


# ------------------------------------------------------------------ accesos directos (Windows)

ICONO_ICO = RAIZ / "static" / "iconos" / "artesana.ico"


def _q(texto):
    """Escapa comillas simples para PowerShell."""
    return str(texto).replace("'", "''")


def carpeta_especial(nombre):
    """Desktop, Programs (menú Inicio) o Startup, respetando OneDrive y carpetas movidas."""
    if nombre == "Startup" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    r = _powershell(f"[Environment]::GetFolderPath('{nombre}')")
    ruta = (r.stdout or "").strip()
    return Path(ruta) if ruta else None


def crear_acceso(carpeta, argumentos, descripcion, minimizado=False):
    """Acceso directo «ArteSana» que abre el ícono de la bandeja (sin consola)."""
    acceso = carpeta / "ArteSana.lnk"
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{_q(acceso)}');"
        f"$s.TargetPath='{_q(python_sin_ventana())}';"
        f"$s.Arguments='\"{_q(RAIZ / 'bandeja.py')}\" {_q(argumentos)}';"
        f"$s.WorkingDirectory='{_q(RAIZ)}';$s.WindowStyle={7 if minimizado else 1};"
        f"$s.Description='{_q(descripcion)}';"
        + (f"$s.IconLocation='{_q(ICONO_ICO)},0';" if ICONO_ICO.exists() else "")
        + "$s.Save()"
    )
    r = _powershell(script)
    if r.returncode or not acceso.exists():
        log("No se pudo crear el acceso directo: " + (r.stderr or "")[-300:])
        return False
    return True


def autoinicio_activo():
    return ES_WINDOWS and (carpeta_especial("Startup") / "ArteSana.lnk").exists()


def cmd_autoinicio():
    if not ES_WINDOWS:
        decir("El inicio automático solo está disponible en Windows.")
        return 1
    acceso = carpeta_especial("Startup") / "ArteSana.lnk"
    if acceso.exists():
        acceso.unlink()
        decir("Inicio automático DESACTIVADO.")
        return 0
    if not crear_acceso(acceso.parent, "", "ArteSana: ícono en la bandeja", minimizado=True):
        decir("No se pudo crear el acceso directo (mirá el registro).")
        return 1
    decir("Inicio automático ACTIVADO: ArteSana arranca sola al entrar a Windows.")
    return 0


def cmd_accesos():
    """Accesos directos en el escritorio y en el menú Inicio."""
    if not ES_WINDOWS:
        decir("Los accesos directos solo están disponibles en Windows.")
        return 1
    ok = True
    for nombre in ("Desktop", "Programs"):
        carpeta = carpeta_especial(nombre)
        if carpeta and carpeta.exists():
            ok = crear_acceso(carpeta, "--abrir", "Abrir ArteSana") and ok
    if autoinicio_activo():  # que el inicio con Windows también abra el ícono
        crear_acceso(carpeta_especial("Startup"), "", "ArteSana: ícono en la bandeja", minimizado=True)
    decir("Accesos directos «ArteSana» creados en el escritorio y en el menú Inicio." if ok
          else "No se pudieron crear todos los accesos directos (mirá el registro).")
    return 0 if ok else 1


def _powershell(script):
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, errors="replace", creationflags=SIN_VENTANA)


def _como_admin(programa, argumentos):
    """Ejecuta un programa pidiendo permiso de administrador (ventana de UAC) y espera que termine."""
    argumentos = argumentos.replace("'", "''")
    return _powershell(f"Start-Process -FilePath '{programa}' -Verb RunAs -Wait -WindowStyle Hidden "
                       f"-ArgumentList '{argumentos}'")


def nombre_regla(puerto):
    return f"ArteSana-{puerto}"


def firewall_regla_existe(puerto):
    r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={nombre_regla(puerto)}"],
                       capture_output=True, text=True, errors="replace", creationflags=SIN_VENTANA)
    return r.returncode == 0


def perfiles_de_red():
    """[(alias, 'Public'|'Private'|'DomainAuthenticated', indice)] de las conexiones activas."""
    r = _powershell("Get-NetConnectionProfile | ForEach-Object { $_.InterfaceAlias + '|' + "
                    "$_.NetworkCategory + '|' + $_.InterfaceIndex }")
    perfiles = []
    for linea in (r.stdout or "").splitlines():
        partes = linea.strip().split("|")
        if len(partes) == 3:
            perfiles.append((partes[0], partes[1], partes[2]))
    return perfiles


def preguntar(texto, defecto="s"):
    try:
        resp = input(f"  {texto} [{'S/n' if defecto == 's' else 's/N'}]: ").strip().lower()
    except EOFError:
        return defecto == "s"
    return (resp or defecto).startswith("s")


def cmd_red():
    """Diagnostica y configura el acceso desde otros equipos de la misma red."""
    cmd_configurar()
    cfg = leer_config()
    puerto = cfg["PUERTO"]
    ip = ip_local()
    decir("  Acceso desde otros equipos (celular, otra PC) en la misma red")
    decir("  ------------------------------------------------------------")
    decir(f"  IP de esta PC: {ip or 'no se pudo detectar'}")

    problemas = []
    if es_local(cfg["HOST"]):
        decir("  [X] La app solo acepta conexiones de esta misma PC (HOST=127.0.0.1)")
        problemas.append("host")
    else:
        decir(f"  [OK] La app acepta conexiones de la red (HOST={cfg['HOST']})")
    if not cfg["APP_PASSWORD"]:
        decir("  [!] No tiene contraseña: cualquiera en tu red podría entrar")
        problemas.append("password")
    else:
        decir("  [OK] Tiene contraseña")

    perfiles = []
    if ES_WINDOWS:
        if firewall_regla_existe(puerto):
            decir(f"  [OK] El Firewall de Windows permite el puerto {puerto} (regla «{nombre_regla(puerto)}»)")
        else:
            decir(f"  [X] El Firewall de Windows no tiene permitido el puerto {puerto}")
            problemas.append("firewall")
        perfiles = perfiles_de_red()
        publicas = [p for p in perfiles if p[1] == "Public"]
        for alias, categoria, _ in perfiles:
            nombre = {"Public": "PÚBLICA", "Private": "privada", "DomainAuthenticated": "de dominio"}.get(categoria, categoria)
            decir(f"  [{'X' if categoria == 'Public' else 'OK'}] Red «{alias}»: {nombre}")
        if publicas:
            problemas.append("publica")

    corriendo, _ = servicio_corriendo()
    if corriendo and not es_local(cfg["HOST"]) and ip:
        try:
            with urllib.request.urlopen(f"http://{ip}:{puerto}/salud", timeout=3) as r:
                decir(f"  [OK] La app responde en http://{ip}:{puerto}")
        except Exception:
            decir(f"  [X] La app no responde en http://{ip}:{puerto}")
    elif not corriendo:
        decir("  [!] La app no está corriendo")

    if not problemas:
        decir("")
        decir(f"  Todo listo. Desde el otro equipo abrí:  http://{ip}:{puerto}")
        decir("  Si igual no abre: fijate que los dos equipos estén en la misma red (mismo Wi-Fi/router)")
        decir("  y que no haya un antivirus con firewall propio (ESET, Kaspersky, Avast...).")
        return 0

    decir("")
    if not preguntar("¿Configurar ahora para que se pueda entrar desde otros equipos?"):
        return 0
    reiniciar = False

    if "host" in problemas:
        guardar_en_env("HOST", "0.0.0.0")
        decir("  - HOST=0.0.0.0 guardado en artesana.env")
        reiniciar = True

    if "password" in problemas:
        import getpass
        decir("  Elegí una contraseña para entrar a la app (no se ve mientras escribís).")
        decir("  Dejala vacía y apretá Enter si no querés contraseña.")
        try:
            clave = getpass.getpass("  Contraseña: ").strip()
        except (EOFError, KeyboardInterrupt):
            clave = ""
        if clave:
            guardar_en_env("APP_PASSWORD", clave)
            decir("  - Contraseña guardada")
            reiniciar = True

    if "firewall" in problemas:
        decir("  - Windows va a pedir permiso de administrador para abrir el puerto en el Firewall…")
        _como_admin("netsh", f"advfirewall firewall add rule name={nombre_regla(puerto)} dir=in "
                             f"action=allow protocol=TCP localport={puerto} profile=private,domain")
        if firewall_regla_existe(puerto):
            decir(f"  - Puerto {puerto} permitido en redes privadas")
        else:
            decir("  - No se pudo crear la regla (¿se canceló el permiso?). Volvé a intentar con la opción R.")

    if "publica" in problemas:
        decir("")
        decir("  Tu conexión está marcada como red PÚBLICA. Windows bloquea las conexiones")
        decir("  entrantes en redes públicas. En tu casa o negocio conviene marcarla como PRIVADA.")
        if preguntar("¿Marcar la red como privada? (solo si es tu red de confianza)"):
            for alias, categoria, indice in perfiles:
                if categoria == "Public":
                    _como_admin("powershell", f"-NoProfile -Command Set-NetConnectionProfile "
                                              f"-InterfaceIndex {indice} -NetworkCategory Private")
            decir("  - Listo" if not any(p[1] == "Public" for p in perfiles_de_red())
                  else "  - No se pudo cambiar. Hacelo a mano: Configuración de Windows → Red e Internet → "
                       "Wi-Fi o Ethernet → tu red → Tipo de perfil de red: Privada")

    if reiniciar:
        decir("")
        cmd_reiniciar() if servicio_corriendo()[0] else cmd_iniciar()
    decir("")
    decir(f"  Desde el otro equipo abrí:  http://{ip_local()}:{leer_config()['PUERTO']}")
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
        "red": cmd_red,
        "accesos": cmd_accesos,
    }
    if cmd not in comandos:
        decir(f"Comando desconocido: {cmd}\n{__doc__}")
        return 2
    return comandos[cmd]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
