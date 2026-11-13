@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title ArteSana

rem ============================================================
rem  ArteSana - inicio (basado en iniciar.bat de RiplayWEB)
rem  1. Busca Python 3.10 o mas nuevo. Si no hay, lo instala
rem     (solo para tu usuario, sin permisos de administrador).
rem  2. Prepara el entorno (.venv) y las librerias.
rem  3. Abre el icono junto al reloj, que inicia la app en segundo
rem     plano, y espera a que responda.
rem
rem  artesana.bat menu      menu clasico en esta ventana
rem  artesana.bat consola   corre la app en esta ventana, para ver errores
rem  artesana.bat iniciar / detener / reiniciar / actualizar / volver / red / estado
rem ============================================================

set "PY_VERSION=3.13.1"
set "PY_WINGET_ID=Python.Python.3.13"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "servicio.py" (
  echo No encuentro servicio.py en esta carpeta.
  echo Si abriste este archivo desde adentro del .zip, primero descomprimilo:
  echo clic derecho en el .zip, "Extraer todo", y abri artesana.bat desde la carpeta extraida.
  goto :fail
)

set "PUERTO=5000"
if exist "artesana.env" for /f "usebackq eol=# tokens=1,* delims==" %%a in ("artesana.env") do if /i "%%a"=="PUERTO" set "PUERTO=%%b"

rem ---------------------------------------------------- 1. Python y entorno
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 goto :venv_ok
  echo El entorno guardado no sirve - Python viejo o desinstalado. Lo vuelvo a crear...
  rmdir /s /q ".venv"
)

call :find_python
if defined PY goto :python_ok

echo.
echo No encontre Python 3.10 o mas nuevo en esta compu.
echo Lo instalo ahora, solo para tu usuario. Tarda un par de minutos...
echo.
where winget >nul 2>nul
if errorlevel 1 goto :install_direct
echo Instalando Python con winget...
winget install --id %PY_WINGET_ID% -e --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
call :find_python
if defined PY goto :python_installed

:install_direct
echo Descargando Python %PY_VERSION% desde python.org...
set "PY_ARCH=amd64"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PY_ARCH=arm64"
set "PY_URL=https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-%PY_ARCH%.exe"
set "PY_SETUP=%TEMP%\artesana-python-%PY_VERSION%.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri '%PY_URL%' -OutFile '%PY_SETUP%'; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
  echo No se pudo descargar Python. Revisa tu conexion a internet.
  goto :manual_python
)
echo Instalando Python %PY_VERSION%...
start "" /wait "%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0 Include_doc=0
del "%PY_SETUP%" >nul 2>nul
call :find_python
if defined PY goto :python_installed

:manual_python
echo.
echo No pude instalar Python automaticamente.
echo Instalalo a mano desde https://www.python.org/downloads/
echo y marca la casilla "Add python.exe to PATH". Despues abri de nuevo este archivo.
start "" "https://www.python.org/downloads/"
goto :fail

:python_installed
echo Python instalado.

:python_ok
<nul set /p "=Usando " & "%PY%" --version
echo Preparando ArteSana por primera vez, tarda un minuto...
"%PY%" -m venv .venv
if errorlevel 1 (
  echo No se pudo crear el entorno de Python.
  goto :fail
)
call :dependencias
if errorlevel 1 goto :fail
"%VENV_PY%" servicio.py configurar

:venv_ok
rem ---------------------------------------------------- 2. Librerias que falten
"%VENV_PY%" -c "import flask, openpyxl" >nul 2>nul
if errorlevel 1 call :dependencias
if /i "%~1"=="menu" goto :menu
if not "%~1"=="" goto :comando

rem ---------------------------------------------------- 3. Icono junto al reloj + app
if not exist "%VENV_PYW%" goto :sin_icono
"%VENV_PY%" -c "import pystray, PIL" >nul 2>nul
if errorlevel 1 (
  echo Instalando el componente del icono de la bandeja...
  call :dependencias
)
"%VENV_PY%" -c "import pystray, PIL" >nul 2>nul
if errorlevel 1 goto :sin_icono

start "" "%VENV_PYW%" "%~dp0bandeja.py" --abrir
call :is_running 60
if errorlevel 1 goto :bg_failed
echo Listo. ArteSana queda funcionando junto al reloj: clic derecho en su icono para ver las opciones.
timeout /t 3 >nul
exit /b 0

:sin_icono
echo No se pudo usar el icono de la bandeja; inicio ArteSana igual.
"%VENV_PY%" servicio.py iniciar --abrir
if errorlevel 1 goto :fail
timeout /t 4 >nul
exit /b 0

:bg_failed
echo.
echo ArteSana no respondio. Ultimas lineas del registro:
echo.
"%VENV_PY%" -c "import servicio; servicio.mostrar_log(15)"
echo.
echo Para ver el error completo, abri:  artesana.bat consola
goto :fail

:comando
rem Con argumentos se ejecuta directo. Ej: artesana.bat iniciar
"%VENV_PY%" servicio.py %*
exit /b %errorlevel%

:fail
echo.
pause
exit /b 1


rem ============================================================
rem  Menu clasico
rem ============================================================
:menu
cls
echo.
echo   ==================================================
echo                ArteSana - Costos
echo   ==================================================
echo.
"%VENV_PY%" servicio.py estado --breve
echo.
echo   [1] Iniciar y abrir
echo   [2] Abrir
echo   [3] Reiniciar
echo   [4] Buscar y aplicar actualizaciones de GitHub
echo   [5] Detener
echo   [6] Ver estado completo
echo   [7] Ver registro (log)
echo   [8] Iniciar con Windows (activar / desactivar)
echo   [9] Reinstalar dependencias
echo   [R] Usar desde otros equipos de la red (celular, otra PC)
echo   [V] Volver a la version anterior a la ultima actualizacion
echo   [C] Correr en esta ventana (para ver errores)
echo   [0] Salir (la app sigue corriendo en segundo plano)
echo.
choice /c 1234567890RVC /n /m "  Elegi una opcion: "
set "OP=%errorlevel%"
echo.
if "%OP%"=="10" exit /b 0
if "%OP%"=="1" "%VENV_PY%" servicio.py iniciar --abrir
if "%OP%"=="2" "%VENV_PY%" servicio.py abrir
if "%OP%"=="3" "%VENV_PY%" servicio.py reiniciar
if "%OP%"=="4" "%VENV_PY%" servicio.py actualizar
if "%OP%"=="5" "%VENV_PY%" servicio.py detener
if "%OP%"=="6" "%VENV_PY%" servicio.py estado
if "%OP%"=="7" "%VENV_PY%" servicio.py log
if "%OP%"=="8" "%VENV_PY%" servicio.py autoinicio
if "%OP%"=="9" call :dependencias
if "%OP%"=="11" "%VENV_PY%" servicio.py red
if "%OP%"=="12" "%VENV_PY%" servicio.py volver
if "%OP%"=="13" "%VENV_PY%" servicio.py consola
echo.
pause
goto :menu


rem ============================================================
rem  :dependencias  ->  instala lo de requirements.txt
rem ============================================================
:dependencias
echo Instalando librerias, puede tardar un minuto...
"%VENV_PY%" -m pip install --disable-pip-version-check -q --upgrade pip >nul 2>nul
"%VENV_PY%" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo No se pudieron instalar las librerias. Revisa tu conexion a internet
  echo o si un antivirus o proxy bloquea pip.
  exit /b 1
)
exit /b 0


rem ============================================================
rem  :find_python  ->  deja en PY la ruta de un python.exe 3.10+
rem ============================================================
:find_python
set "PY="
rem Lanzador "py" (el instalador oficial lo agrega)
call :try_cmd py -3
if defined PY exit /b 0
rem "python" en el PATH (se descarta el acceso directo de Microsoft Store)
call :try_cmd python
if defined PY exit /b 0
rem Lanzador recien instalado que todavia no esta en el PATH de esta ventana
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" call :try_cmd "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3
if defined PY exit /b 0
rem Instalaciones conocidas
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY call :try_exe "%%~fd\python.exe"
if defined PY exit /b 0
for /d %%d in ("%ProgramFiles%\Python3*") do if not defined PY call :try_exe "%%~fd\python.exe"
exit /b 0

:try_cmd
rem Pregunta a ese comando donde esta su python.exe (sin for /f: rompe rutas con comillas)
set "CAND="
set "CAND_FILE=%TEMP%\artesana-python-path.txt"
del "%CAND_FILE%" >nul 2>nul
%* -c "import sys; print(sys.executable)" > "%CAND_FILE%" 2>nul
if exist "%CAND_FILE%" set /p CAND=<"%CAND_FILE%"
del "%CAND_FILE%" >nul 2>nul
if not defined CAND exit /b 0
call :try_exe "%CAND%"
exit /b 0

:try_exe
if not exist "%~1" exit /b 0
echo %~1 | find /i "\WindowsApps\" >nul && exit /b 0
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY=%~1"
exit /b 0


rem ============================================================
rem  :is_running N  ->  errorlevel 0 si ArteSana responde (espera hasta N/2 segundos)
rem ============================================================
:is_running
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = New-Object Net.WebClient; $c.Proxy = $null; for ($i = 0; $i -lt %~1; $i++) { try { $r = $c.DownloadString('http://127.0.0.1:%PUERTO%/salud'); if ($r -match 'ok') { exit 0 } } catch { }; Start-Sleep -Milliseconds 500 }; exit 1" >nul 2>nul
exit /b %errorlevel%
