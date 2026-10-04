@echo off
REM Builds Refuel.exe with PyInstaller. Output: dist\Refuel.exe
python -m pip install -r requirements-build.txt
if errorlevel 1 exit /b 1
python test_core.py
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --onefile --windowed --name Refuel ^
  --collect-all pystray --collect-all PIL --collect-all winotify --collect-all qrcode ^
  run.py
if errorlevel 1 exit /b 1
echo.
echo Build complete: dist\Refuel.exe
