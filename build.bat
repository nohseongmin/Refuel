@echo off
REM Builds Refuel.exe with PyInstaller. Output: dist\Refuel.exe
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Refuel ^
  --collect-all pystray --collect-all PIL --collect-all winotify --collect-all qrcode ^
  run.py
echo.
echo Build complete: dist\Refuel.exe
