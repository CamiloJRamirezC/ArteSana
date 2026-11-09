"""Lectura y formato de números al estilo paraguayo: 1.234.567 y 2,5."""
import re


def parse_num(texto, default=None):
    """Convierte '1.500', '2,5', '6000', '10.000 Gs' en número.

    - Con coma: la coma es decimal y los puntos son miles ('1.234,5' -> 1234.5).
    - Sin coma y con grupos de 3 dígitos tras el punto: los puntos son miles
      ('1.500' -> 1500).
    - En otro caso se lee normal ('0.5' -> 0.5).
    """
    if texto is None:
        return default
    s = str(texto).strip().replace(" ", "").replace("Gs", "").replace("gs", "")
    if s == "":
        return default
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"«{texto}» no es un número válido")


def _formatear(valor, decimales, recortar):
    if valor is None:
        return ""
    s = f"{valor:,.{decimales}f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    if recortar and "," in s:
        s = s.rstrip("0").rstrip(",")
    return s


def gs(valor):
    """Guaraníes sin decimales: 51250 -> '51.250'."""
    return _formatear(valor, 0, False)


def gs_unit(valor):
    """Precio por g/ml/u, hasta 2 decimales: 20.76 -> '20,76', 6.0 -> '6'."""
    return _formatear(valor, 2, True)


def cant(valor):
    """Cantidades, hasta 3 decimales: 2.4 -> '2,4', 1000 -> '1.000'."""
    return _formatear(valor, 3, True)


def pct(valor):
    if valor is None:
        return ""
    return _formatear(valor * 100, 1, True) + " %"
