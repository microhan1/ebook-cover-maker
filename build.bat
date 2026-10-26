@echo off
rem Build ebook-cover-maker.exe (single file, no console) with PyInstaller.
rem Usage: build.bat          -> dist\ebook-cover-maker.exe
setlocal
cd /d "%~dp0"

python -m PyInstaller --version >nul 2>&1 || python -m pip install pyinstaller
python -m pip install -r requirements.txt

rem The exe carries small subsets of the Noto CJK collections (fonts\ stays the source), see tools\subset_fonts.py.
rem They take a few minutes to make, so they are made once; delete fontbuild\ to make them again.
if not exist fontbuild\pack\serif-kr.otf.xz (
  python tools\subset_fonts.py
  if errorlevel 1 (
    echo Making the font subsets failed.
    exit /b 1
  )
)

python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name ebook-cover-maker ^
  --add-data "lang;lang" ^
  --add-data "fontbuild\pack;fonts" ^
  --collect-data tkinterdnd2 ^
  --exclude-module numpy --exclude-module lxml --exclude-module scipy --exclude-module PIL._avif ^
  main.py

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo.
echo Done: dist\ebook-cover-maker.exe
endlocal
