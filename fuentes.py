"""Precios de supermercados (fuentes de precio).

Cada supermercado es una clase con `buscar(texto)` que devuelve productos con nombre,
precio normal, oferta y la presentación detectada del nombre. Para sumar otro súper,
se agrega su clase y se registra en FUENTES.

El precio que devuelve es una RECOMENDACIÓN: la app nunca lo guarda sin que el
usuario lo confirme.
"""
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser

AGENTE = "ArteSana/1.0 (calculo de costos de recetas; consultas puntuales)"
PAUSA_ENTRE_CONSULTAS = 1.0   # segundos, para no cargar el sitio


class ErrorDeFuente(Exception):
    """El súper no respondió o cambió su página."""


@dataclass
class Producto:
    fuente: str
    id: str
    nombre: str
    url: str
    precio: float | None              # precio normal (el recomendado)
    precio_oferta: float | None = None
    etiqueta: str = ""
    por_kilo: bool = False
    presentacion: float | None = None  # cantidad detectada en el nombre
    unidad: str | None = None          # g, kg, ml, l, u
    precio_unitario: float | None = None   # Gs por g / ml / u
    unidad_base: str | None = None
    extra: dict = field(default_factory=dict)

    def dict(self):
        return asdict(self)


# ------------------------------------------------------------------ presentación desde el nombre

_UNIDADES = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "k": "kg",
    "g": "g", "gr": "g", "grs": "g", "gramos": "g", "grm": "g",
    "ml": "ml", "cc": "ml", "cm3": "ml",
    "l": "l", "lt": "l", "lts": "l", "litro": "l", "litros": "l",
    "u": "u", "un": "u", "und": "u", "unid": "u", "unidades": "u", "uni": "u",
}
_UNIDAD_RE = r"(kgs?|kilos?|k|grs?|gramos|grm|g|ml|cc|cm3|lts?|litros?|l|unid(?:ades)?|und|uni|un|u)"
_NUM = r"(\d+(?:[.,]\d+)?)"
_PACK = re.compile(rf"(\d+)\s*[x×]\s*{_NUM}\s*{_UNIDAD_RE}\b\.?", re.I)
_CANT = re.compile(rf"(?<![\w.,]){_NUM}\s*{_UNIDAD_RE}\b\.?", re.I)
_X_UNIDADES = re.compile(r"\b[x×]\s*(\d+)\s*(?:u|un|unid|unidades)?\b", re.I)
_FACTOR = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "u": 1}
_BASE = {"g": "g", "kg": "g", "ml": "ml", "l": "ml", "u": "u"}


def _num(texto):
    return float(texto.replace(",", "."))


def presentacion_del_nombre(nombre, por_kilo=False):
    """'HARINA 000 HILDEBRAND 500 GR' -> (500, 'g'); 'GALLETITA 3 X 100 GR' -> (300, 'g');
    'HUEVO X 30' -> (30, 'u'); a granel por kilo -> (1, 'kg'). Si no la encuentra: (None, None)."""
    texto = " " + (nombre or "") + " "
    m = _PACK.search(texto)
    if m:
        return int(m.group(1)) * _num(m.group(2)), _UNIDADES[m.group(3).lower()]
    encontrados = list(_CANT.finditer(texto))
    if encontrados:
        m = encontrados[-1]          # la presentación suele ir al final del nombre
        return _num(m.group(1)), _UNIDADES[m.group(2).lower()]
    m = _X_UNIDADES.search(texto)
    if m:
        return float(m.group(1)), "u"
    if por_kilo or re.search(r"\b(kg|kilo)\b", texto, re.I):
        return 1.0, "kg"
    return None, None


def completar_unitario(p: Producto):
    p.presentacion, p.unidad = presentacion_del_nombre(p.nombre, p.por_kilo)
    if p.presentacion and p.unidad and p.precio:
        p.unidad_base = _BASE[p.unidad]
        p.precio_unitario = p.precio / (p.presentacion * _FACTOR[p.unidad])
    return p


def _precio(texto):
    digitos = re.sub(r"[^\d]", "", texto or "")
    return float(digitos) if digitos else None


# ------------------------------------------------------------------ Supermás

class _LectorSupermas(HTMLParser):
    """Lee las tarjetas <div class="product"> del listado de búsqueda."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.productos = []
        self.total = None
        self._p = None            # producto en curso
        self._div = 0             # profundidad de <div> dentro del producto
        self._captura = None      # qué texto se está juntando
        self._span = 0            # profundidad de <span> desde que empezó la captura
        self._en_precio = 0       # profundidad de <span> dentro de span.price
        self._zona = "normal"     # ins / del / normal dentro del precio
        self._texto = []
        self._conteo = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        clases = set((a.get("class") or "").split())
        if tag == "p" and "woocommerce-result-count" in clases:
            self._conteo, self._texto = True, []
        if tag == "div":
            if self._p is not None:
                self._div += 1
            elif "product" in clases:
                self._p = {"href": "", "nombre": "", "etiqueta": "", "ins": "", "del": "", "normal": "", "modo": ""}
                self._div = 1
            return
        if self._p is None:
            return
        if tag == "a" and "woocommerce-LoopProduct-link" in clases:
            self._p["href"] = a.get("href", "")
        elif tag == "span":
            if self._captura:
                self._span += 1
            if self._en_precio:
                self._en_precio += 1
            if "onsale" in clases and not self._captura:
                self._empezar("etiqueta")
            elif "price" in clases and not self._en_precio:
                self._en_precio = 1
            elif "amount" in clases and self._en_precio and not self._captura:
                self._empezar(self._zona)
        elif tag in ("ins", "del") and self._en_precio:
            self._zona = tag
        elif tag == "h2" and "woocommerce-loop-product__title" in clases:
            self._empezar("nombre")
        elif tag == "input" and "inp-quantity" in clases:
            self._p["modo"] = a.get("data-modo_venta", "")

    def _empezar(self, campo):
        self._captura, self._span, self._texto = campo, 1 if campo != "nombre" else 0, []

    def _terminar(self):
        valor = re.sub(r"\s+", " ", "".join(self._texto)).strip()
        if self._captura in ("ins", "del", "normal"):
            if valor and not self._p[self._captura]:
                self._p[self._captura] = valor
        elif self._captura:
            self._p[self._captura] = valor
        self._captura = None

    def handle_endtag(self, tag):
        if tag == "p" and self._conteo:
            self._conteo = False
            m = re.search(r"de\s+(\d+)\s+resultado", "".join(self._texto))
            self.total = int(m.group(1)) if m else None
        if self._p is None:
            return
        if tag == "span":
            if self._captura and self._captura != "nombre":
                self._span -= 1
                if self._span == 0:
                    self._terminar()
            if self._en_precio:
                self._en_precio -= 1
                if self._en_precio == 0:
                    self._zona = "normal"
        elif tag in ("ins", "del"):
            self._zona = "normal"
        elif tag == "h2" and self._captura == "nombre":
            self._terminar()
        elif tag == "div":
            self._div -= 1
            if self._div == 0:
                self.productos.append(self._p)
                self._p = None

    def handle_data(self, data):
        if self._captura or self._conteo:
            self._texto.append(data)


class Supermas:
    id = "supermas"
    nombre = "Supermás"
    base = "https://www.supermas.com.py/"

    def url_busqueda(self, texto, pagina=1):
        ruta = "productos" if pagina == 1 else f"productos.{pagina}"
        return self.base + ruta + "?" + urllib.parse.urlencode({"q": texto, "post_type": "product"})

    def _descargar(self, url):
        pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept-Language": "es-PY,es"})
        try:
            with urllib.request.urlopen(pedido, timeout=25) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            raise ErrorDeFuente(f"No se pudo conectar con {self.nombre} ({e.__class__.__name__}). ¿Hay internet?") from e

    def leer_listado(self, html):
        lector = _LectorSupermas()
        lector.feed(html)
        productos = []
        for d in lector.productos:
            m = re.search(r"-p(\d+)(?:[/?#]|$)", d["href"])
            if not m or not d["nombre"]:
                continue
            oferta = _precio(d["ins"])
            normal = _precio(d["del"]) if d["del"] else (_precio(d["normal"]) or oferta)
            if d["del"] and not oferta:
                oferta = None
            p = Producto(
                fuente=self.id, id=m.group(1), nombre=d["nombre"],
                url=urllib.parse.urljoin(self.base, d["href"]),
                precio=normal, precio_oferta=oferta if (d["del"] and oferta and oferta != normal) else None,
                etiqueta=d["etiqueta"], por_kilo=d["modo"].lower().startswith("kilo"),
            )
            productos.append(completar_unitario(p))
        return productos, lector.total

    def buscar(self, texto, paginas=2):
        texto = (texto or "").strip()
        if len(texto) < 2:
            return [], 0
        todos, total = [], None
        for pagina in range(1, paginas + 1):
            if pagina > 1:
                time.sleep(PAUSA_ENTRE_CONSULTAS)
            html = self._descargar(self.url_busqueda(texto, pagina))
            productos, cantidad = self.leer_listado(html)
            if pagina == 1:
                total = cantidad
                if not productos and "woocommerce" not in html:
                    raise ErrorDeFuente(f"La página de {self.nombre} cambió y no se pudo leer. Avisá para actualizar el lector.")
            vistos = {p.id for p in todos}
            nuevos = [p for p in productos if p.id not in vistos]   # sin repetidos entre páginas
            todos += nuevos
            if not nuevos or (total is not None and len(todos) >= total):
                break
        return todos, total if total is not None else len(todos)

    def consultar(self, producto_id, nombre):
        """Precio actual de un producto ya vinculado: lo busca por su nombre y lo reconoce por su código."""
        productos, _ = self.buscar(nombre, paginas=1)
        for p in productos:
            if p.id == str(producto_id):
                return p
        clave = re.sub(r"\s+", " ", (nombre or "").strip().lower())
        for p in productos:
            if re.sub(r"\s+", " ", p.nombre.lower()) == clave:
                return p
        return None


FUENTES = {f.id: f for f in (Supermas(),)}


def fuente(id_fuente="supermas"):
    return FUENTES.get(id_fuente) or FUENTES["supermas"]
