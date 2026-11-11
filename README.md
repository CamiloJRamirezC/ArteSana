# ArteSana · Lista de materiales y costos

App web para calcular el costo y el precio de venta de cada producto a partir de sus
recetas. Funciona desde cualquier navegador, en la computadora o el celular.

Se puede usar de dos formas:
- **En tu computadora con Windows**: con `artesana.bat`, corre en segundo plano y se actualiza sola desde GitHub.
- **Publicada en internet**: en Render + Supabase, gratis (ver más abajo).

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

## Usarla en tu computadora con Windows

### Instalación (una sola vez)
1. Instalá **Python 3.10+** y **Git**. Desde una terminal:
   ```
   winget install --id Python.Python.3.12 -e
   winget install --id Git.Git -e
   ```
2. Descargá el proyecto **con git**, así puede actualizarse solo:
   ```
   git clone https://github.com/CamiloJRamirezC/ArteSana.git
   ```
3. Abrí la carpeta `ArteSana` y hacé doble clic en **`artesana.bat`**. La primera vez instala todo.

### El menú de `artesana.bat`

| Opción | Qué hace |
|---|---|
| 1 · Iniciar | Arranca la app **en segundo plano** y la abre en el navegador. Podés cerrar la ventana negra: la app sigue funcionando. |
| 2 · Abrir | Abre <http://127.0.0.1:5000> en el navegador. |
| 3 · Reiniciar | Detiene y vuelve a arrancar la app (por ejemplo, después de cambiar `artesana.env`). |
| 4 · Actualizar | Busca cambios en GitHub y los aplica. |
| 5 · Detener | Apaga la app. |
| 6 · Estado | Versión, si está corriendo y cuándo revisó GitHub por última vez. |
| 7 · Registro | Abre los archivos de log en el Bloc de notas. |
| 8 · Iniciar con Windows | Activa o desactiva que la app arranque sola al prender la computadora. |
| 9 · Reinstalar dependencias | Por si algo quedó mal instalado. |
| R · Usar desde otros equipos | Revisa y configura todo para entrar desde el celular u otra PC de la misma red (ver abajo). |

También funciona sin menú: `artesana.bat iniciar`, `artesana.bat detener`, `artesana.bat actualizar`, etc.

### Actualizaciones automáticas
Mientras está corriendo, el servicio revisa GitHub **cada 30 minutos**. Si hay cambios:
1. guarda una copia de los datos en `instance\respaldos` (quedan las últimas 10),
2. baja los cambios,
3. reinstala dependencias si cambió `requirements.txt`,
4. reinicia la app.

Si en esa computadora hay archivos del proyecto modificados a mano, **no actualiza** (para no pisarlos) y lo avisa.
También podés buscar actualizaciones y reiniciar desde la app: **Configuración → Sistema**.
Si la app se cierra por un error, el servicio la vuelve a levantar sola.

### Instalarla desde el navegador
Con la app abierta en Chrome o Edge, tocá **«⤓ Instalar app»** arriba a la derecha, o el ícono de instalar
de la barra de direcciones. Queda como un programa más, con su ícono en el menú Inicio y el escritorio.
Si la abrís con el servicio detenido, te muestra cómo iniciarlo.

### Configuración (`artesana.env`)
Se crea sola en la primera instalación. Después de cambiarla, elegí **Reiniciar**.

| Variable | Por defecto | Para qué |
|---|---|---|
| `PUERTO` | `5000` | Puerto de la app. |
| `HOST` | `127.0.0.1` | `0.0.0.0` para entrar también desde el celular en tu Wi-Fi (con `http://IP-de-la-PC:5000`). Windows va a pedir permiso en el firewall. |
| `APP_PASSWORD` | vacío | Contraseña para entrar. **Ponela si usás `HOST=0.0.0.0`.** |
| `AUTO_ACTUALIZAR` | `1` | `0` = solo avisa, no aplica. |
| `INTERVALO_MINUTOS` | `30` | Cada cuánto revisar GitHub. |
| `DATABASE_URL` | vacío | Vacío = datos en esta PC. Con la de Supabase, comparte los datos con la versión de Render. |

### Usarla desde otros equipos de la red
Por defecto la app solo se abre en la PC donde corre. Para entrar desde el celular u otra PC,
abrí `artesana.bat` y elegí **R**. Revisa y arregla, una por una, las cuatro cosas que pueden impedirlo:

1. **`HOST`**: la cambia a `0.0.0.0` para aceptar conexiones de la red.
2. **Contraseña**: te pide una, para que no entre cualquiera conectado a tu red.
3. **Firewall de Windows**: abre el puerto (Windows pide permiso de administrador).
   Como el servicio corre oculto, Windows muchas veces no muestra su aviso y bloquea en silencio.
4. **Tipo de red**: si tu Wi-Fi está marcado como *red pública*, Windows bloquea todo lo que entra.
   Te ofrece marcarlo como *privada* (hacelo solo en tu red de confianza).

Al final te muestra la dirección para usar en el otro equipo, por ejemplo `http://192.168.1.20:5000`.
También la ves en la app, en **Configuración → Sistema**.

En el otro equipo **no** uses `127.0.0.1`: esa dirección siempre apunta al propio equipo.
Si la IP de la PC cambia (pasa al reiniciar el router), conviene fijarla en el router (reserva DHCP).

Desde el celular por Wi-Fi la app funciona, pero el celular no la instala como app completa:
eso exige una conexión segura (https), que sí tiene la versión publicada en Render. Lo que sí podés hacer
es agregarla a la pantalla de inicio como acceso directo.

### Archivos en esta computadora
Todo lo que es tuyo queda en la carpeta `instance` y GitHub nunca lo toca: `artesana.db` (tus datos),
`respaldos\`, `logs\` y `secret_key`.

## Desarrollo

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/flask --app app run --debug     # modo desarrollo, se recarga al guardar
.venv/bin/pytest                          # pruebas automáticas
```

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
artesana.bat           menú para Windows: instalar, iniciar, actualizar, reiniciar, detener
servicio.py            servicio en segundo plano: corre la app, la vigila y la actualiza desde GitHub
servidor.py            servidor web local (waitress)
artesana.env.ejemplo   configuración local por defecto
app.py                 rutas y pantallas
costeo.py              cálculo de costos (sin dependencias, fácil de probar)
db.py                  base de datos: SQLite local / PostgreSQL en internet
utils.py               lectura y formato de números (1.500 · 2,5)
templates/             pantallas HTML
static/                estilos, íconos, manifest y service worker (app instalable)
tests/                 pruebas automáticas
render.yaml            configuración de Render
.github/               visita automática para que Supabase no pause la base
planilla/              planilla Excel de referencia
```
