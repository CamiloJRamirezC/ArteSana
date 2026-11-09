# ArteSana · Lista de materiales y costos

App web para calcular el costo y el precio de venta de cada producto a partir de sus
recetas. Funciona desde cualquier navegador, en la computadora o el celular.

## Cómo funciona

| Concepto | Regla |
|---|---|
| **Ingrediente** | Se carga como lo comprás (harina 1 kg a 6.000 Gs). La app calcula el precio por **g, ml o u**. |
| **Receta** | Describe **una tanda**. Los ingredientes van en g, ml o u. |
| **Rinde** | Lo que contás o pesás **al terminar** (30 u, 12 porción, 2,4 kg). Ya incluye la merma. |
| **Costo por unidad** | Costo de la tanda ÷ rinde. |
| **Subreceta** | Relleno o base (dulce de leche, ganache) que rinde en g, ml o u y se usa como ingrediente en otras recetas. Si cambia su costo, se actualizan todas. |
| **Precio de venta** | Costo por unidad × multiplicador, redondeado hacia arriba (100 Gs por defecto). Si cargás un precio real, la app muestra la ganancia con ese precio. |

Los números se escriben al estilo paraguayo: `1.500` es mil quinientos y `2,5` es dos y medio.

Cada vez que cambiás el precio de un ingrediente queda guardado en su **historial de precios**.
Ese historial es la base para el paso siguiente: leer los precios del supermercado automáticamente.

## Probarla en tu computadora

Necesitás Python 3.10 o más nuevo.

```bash
cd artesana
python -m venv .venv
# Windows:  .venv\Scripts\activate      Mac/Linux:  source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --debug
```

Abrí <http://127.0.0.1:5000> y tocá **«Cargar un ejemplo»**. En tu computadora los datos se
guardan en `instance/artesana.db` y no pide contraseña.

Pruebas automáticas: `pip install pytest` y después `pytest`.

## Publicarla gratis en internet

Arquitectura: **Render** ejecuta la app y **Supabase** guarda los datos (PostgreSQL).
Hace falta separarlos porque el disco de Render gratuito se borra cada vez que la app se reinicia.

### 1. Subir el código a GitHub
1. Creá una cuenta en <https://github.com> y un repositorio **privado** llamado `artesana`.
2. Subí todos los archivos de esta carpeta, incluida `.github/`. Desde la web: *Add file → Upload files*.

### 2. Crear la base en Supabase
1. Entrá a <https://supabase.com> → *New project*. Elegí la región **São Paulo** (la más cercana)
   y anotá la contraseña de la base.
2. En el proyecto: botón **Connect** → pestaña **Session pooler** → copiá la URI.
3. Reemplazá `[YOUR-PASSWORD]` por tu contraseña. Esa es tu `DATABASE_URL`.

> Usá la del *Session pooler*, no la *Direct connection*: la directa usa IPv6 y Render no se puede conectar a ella.

### 3. Crear la app en Render
1. Entrá a <https://render.com> con tu cuenta de GitHub → **New → Blueprint** → elegí el repositorio.
2. Render lee `render.yaml` y te pide dos datos:
   - `APP_PASSWORD`: la contraseña para entrar a la app.
   - `DATABASE_URL`: la de Supabase del paso anterior.
3. **Apply**. A los pocos minutos tenés la app en `https://artesana-xxxx.onrender.com`.

Las tablas se crean solas la primera vez que arranca.

### 4. Evitar que la base se pause (recomendado)
Supabase gratuito pausa la base si pasa **7 días sin uso**. El workflow
`.github/workflows/mantener-activo.yml` visita la app cada 3 días para evitarlo:

En GitHub → *Settings → Secrets and variables → Actions → Variables* → **New variable**:
`APP_URL` = la dirección de tu app en Render, sin la `/` final.

### 5. Dominio propio (cuando quieras)
Render → tu servicio → *Settings → Custom Domains*. Agregás el dominio y copiás en tu
proveedor de dominio el registro DNS que Render te muestra.

## Límites del plan gratuito

| Servicio | Límite | Qué significa para vos |
|---|---|---|
| Render | Se duerme tras 15 min sin visitas | La primera visita después tarda **~1 minuto** en abrir; las siguientes son rápidas. |
| Supabase | 500 MB y pausa tras 7 días sin uso | Alcanza para años de recetas y precios. La pausa se evita con el paso 4. |
| GitHub Actions | 2.000 min/mes en repos privados | El aviso cada 3 días usa menos de 10 min por mes. |

## Archivos

```
app.py           rutas y pantallas
costeo.py        cálculo de costos (sin dependencias, fácil de probar)
db.py            base de datos: SQLite local / PostgreSQL en internet
utils.py         lectura y formato de números (1.500 · 2,5)
templates/       pantallas HTML
static/          estilos
tests/           pruebas automáticas
render.yaml      configuración de Render
.github/         visita automática para que Supabase no pause la base
```
