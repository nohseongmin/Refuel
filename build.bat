@echo off
REM Refuel exe 빌드 (PyInstaller). 결과물: dist\Refuel.exe
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Refuel ^
  --collect-all pystray --collect-all PIL --collect-all winotify --collect-all qrcode ^
  run.py
echo.
echo 빌드 완료: dist\Refuel.exe
