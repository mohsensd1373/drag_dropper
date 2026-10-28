@echo off
REM ===================================================================
REM  Build DragDropSorter.exe with PyInstaller (run on Windows)
REM  Place this .bat next to DragDropSorter.pyw and DragDropSorter.ico
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo [1/2] Installing PyInstaller (only needed the first time)...
python -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo Failed to install PyInstaller. Check your Python / pip / internet.
    pause
    exit /b 1
)

echo.
echo [2/2] Building the EXE...
pyinstaller --noconfirm --onefile --windowed ^
    --name DragDropSorter ^
    --icon "DragDropSorter.ico" ^
    --add-data "DragDropSorter.ico;." ^
    "DragDropSorter.pyw"

if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo ===================================================================
echo  Done! Your program is here:  dist\DragDropSorter.exe
echo  (You can move that single .exe anywhere and run it.)
echo ===================================================================
pause
