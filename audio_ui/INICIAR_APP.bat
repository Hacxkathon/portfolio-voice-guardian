@echo off
cd /d "%~dp0"
title Portfolio Voice Guardian
echo ============================================
echo    Portfolio Voice Guardian - arrancando...
echo ============================================
echo.
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creando el entorno virtual ^(solo la primera vez^)...
  python -m venv .venv
  if errorlevel 1 goto :error
)
call ".venv\Scripts\activate.bat"
echo [2/3] Comprobando librerias ^(la primera vez tarda 1-3 minutos^)...
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
if errorlevel 1 goto :error
echo [3/3] Abriendo la app en el navegador...
echo.
echo    Para cerrar la app: cierra esta ventana o pulsa Ctrl+C
echo.
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
streamlit run app.py --server.port 8501
pause
exit /b

:error
echo.
echo  *** Algo ha fallado. Haz una captura de esta ventana y mandasela a Claude. ***
pause
