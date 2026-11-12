// Utilidades de las grillas de ArteSana: números al estilo paraguayo, pegar desde Excel y teclado.
"use strict";

const Grilla = {
  // '1.500' -> 1500, '2,5' -> 2.5, '0.5' -> 0.5 (igual que parse_num en Python)
  num(texto) {
    if (texto === null || texto === undefined) return null;
    let s = String(texto).trim().replace(/\s/g, "").replace(/gs/i, "");
    if (s === "") return null;
    if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
    else if (/^-?\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
    const n = Number(s);
    return Number.isFinite(n) ? n : NaN;
  },

  formato(n, decimales = 0) {
    if (n === null || n === undefined || Number.isNaN(n)) return "";
    return n.toLocaleString("es-PY", { minimumFractionDigits: 0, maximumFractionDigits: decimales });
  },

  normalizar(t) {
    return String(t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  },

  // Texto copiado de Excel -> filas de celdas (tabulador; si no hay, punto y coma)
  filasPegadas(texto) {
    const lineas = texto.replace(/\r/g, "").split("\n");
    while (lineas.length && lineas[lineas.length - 1].trim() === "") lineas.pop();
    const sep = texto.includes("\t") ? "\t" : (texto.includes(";") ? ";" : null);
    return lineas.map((l) => (sep ? l.split(sep) : [l]).map((c) => c.trim()));
  },

  esPegadoDeVarias(texto) {
    return texto.includes("\n") || texto.includes("\t");
  },

  // Flechas arriba/abajo y Enter para moverse entre filas como en una planilla
  teclado(tabla, alEnterUltima) {
    tabla.addEventListener("keydown", (e) => {
      const celda = e.target;
      if (!celda.matches("input, select")) return;
      const fila = celda.closest("tr");
      const col = Number(celda.dataset.col);
      const filas = [...tabla.tBodies[0].rows];
      const i = filas.indexOf(fila);
      const ir = (j) => {
        const destino = filas[j] && filas[j].querySelector(`[data-col="${col}"]`);
        if (destino) { destino.focus(); if (destino.select) destino.select(); return true; }
        return false;
      };
      if (e.key === "ArrowDown" && !e.altKey && celda.tagName !== "SELECT") { if (ir(i + 1)) e.preventDefault(); }
      else if (e.key === "ArrowUp" && !e.altKey && celda.tagName !== "SELECT") { if (ir(i - 1)) e.preventDefault(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const campos = [...fila.querySelectorAll("[data-col]")];
        const siguiente = campos[campos.indexOf(celda) + 1];
        if (siguiente) { siguiente.focus(); if (siguiente.select) siguiente.select(); return; }
        if (i === filas.length - 1) alEnterUltima();
        const primera = tabla.tBodies[0].rows[i + 1] && tabla.tBodies[0].rows[i + 1].querySelector('[data-col="0"]');
        if (primera) primera.focus();
      }
    });
  },

  avisoSinGuardar(estaSucio) {
    window.addEventListener("beforeunload", (e) => {
      if (estaSucio()) { e.preventDefault(); e.returnValue = ""; }
    });
  },

  async guardar(url, cuerpo, csrf) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(cuerpo),
    });
    let datos = {};
    try { datos = await r.json(); } catch (_) { datos = { ok: false, errores: [{ fila: -1, mensaje: "Error del servidor" }] }; }
    return datos;
  },
};
