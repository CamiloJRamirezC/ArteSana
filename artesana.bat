@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ArteSana
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%VENV_PY%" (
  call :instalar
  if errorlevel 1 goto :error_instalacion
)

rem Sin argumentos: abre el icono de la bandeja (junto al reloj) y cierra esta ventana
if "%~1"=="" goto :bandeja

rem "artesana.bat menu" muestra el menu clasico en la consola
if /i "%~1"=="menu" goto :menu

rem Con otros argumentos se ejecuta directo. Ej: artesana.bat iniciar
"%VENV_PY%" servicio.py %*
exit /b %errorlevel%

:bandeja
"%VENV_PY%" -c "import pystray, PIL" >nul 2>nul
if errorlevel 1 (
  echo   Instalando el componente del icono de la bandeja...
  call :dependencias
)
start "" "%VENV_PYW%" "%~dp0bandeja.py" --abrir
exit /b 0

:menu
cls
echo.
echo   ==================================================
echo                ArteSana - Costos
echo   ==================================================
echo.
"%VENV_PY%" servicio.py estado --breve
echo.
echo   [1] Iniciar y abrir en el navegador
echo   [2] Abrir en el navegador
echo   [3] Reiniciar
echo   [4] Buscar y aplicar actualizaciones de GitHub
echo   [5] Detener
echo   [6] Ver estado completo
echo   [7] Ver registro (log)
echo   [8] Iniciar con Windows (activar / desactivar)
echo   [9] Reinstalar dependencias
echo   [R] Usar desde otros equipos de la red (celular, otra PC)
echo   [0] Salir (la app sigue corriendo en segundo plano)
echo.
choice /c 1234567890R /n /m "  Elegi una opcion: "
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
echo.
pause
goto :menu


:instalar
echo.
echo   Instalando ArteSana (solo la primera vez)...
echo.
set "PYBASE="
where py >nul 2>nul && set "PYBASE=py -3"
if not defined PYBASE (
  where python >nul 2>nul && set "PYBASE=python"
)
if not defined PYBASE goto :sin_python
%PYBASE% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :sin_python
echo   - Creando entorno de Python (.venv)
%PYBASE% -m venv "%~dp0.venv"
if errorlevel 1 exit /b 1
call :dependencias
if errorlevel 1 exit /b 1
"%VENV_PY%" servicio.py configurar
echo.
echo   Instalacion terminada.
echo.
exit /b 0

:dependencias
echo   - Instalando dependencias (puede tardar un minuto)
"%VENV_PY%" -m pip install --disable-pip-version-check -q --upgrade pip
"%VENV_PY%" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo   ERROR: no se pudieron instalar las dependencias. Revisa la conexion a internet.
  exit /b 1
)
echo   - Dependencias listas
exit /b 0

:sin_python
echo   No se encontro Python 3.10 o mas nuevo.
echo   Instalalo con:   winget install --id Python.Python.3.12 -e
echo   o desde https://www.python.org/downloads/ marcando "Add python.exe to PATH".
echo   Despues volve a ejecutar artesana.bat
exit /b 1

:error_instalacion
echo.
echo   La instalacion no se completo.
if exist "%~dp0.venv" rmdir /s /q "%~dp0.venv"
pause
exit /b 1
