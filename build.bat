@echo off
rem Build ebook-cover-maker.exe (single file, no console) with PyInstaller.
rem Usage: build.bat          -> dist\ebook-cover-maker.exe
setlocal
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1 || python -m pip install pyinstaller
python -m pip install -r requirements.txt

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name ebook-cover-maker ^
  --add-data "lang;lang" ^
  --add-data "fonts;fonts" ^
  --collect-data tkinterdnd2 ^
  main.py

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo.
echo Done: dist\ebook-cover-maker.exe
endlocal
