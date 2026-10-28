DragDropSorter - How to make an EXE and run at Windows startup
=============================================================

FILES
-----
  DragDropSorter.pyw   -> the program (pure Python, no extra packages)
  DragDropSorter.ico   -> the program icon (keep it NEXT TO the .pyw)
  build_exe.bat        -> one-click EXE builder (uses PyInstaller)


A) BUILD A STANDALONE .EXE
--------------------------
  1. Put these 3 files in one folder:
       DragDropSorter.pyw
       DragDropSorter.ico
       build_exe.bat
  2. Double-click  build_exe.bat
       - first run installs PyInstaller (internet needed once)
       - then it builds the exe
  3. Your program appears at:   dist\DragDropSorter.exe
     That single .exe runs on any Windows machine WITHOUT Python installed.

  (Manual command, if you prefer the console:)
     pip install pyinstaller
     pyinstaller --onefile --windowed --name DragDropSorter ^
         --icon DragDropSorter.ico --add-data "DragDropSorter.ico;." DragDropSorter.pyw


B) RUN AUTOMATICALLY AT WINDOWS STARTUP
---------------------------------------
  EASIEST (built in): Open the program -> right-click the tray icon ->
  Settings -> check "Run at Windows startup" -> Save.
  (This writes an entry under HKCU\...\CurrentVersion\Run for the current user.)

  MANUAL alternative (shortcut method):
  1. Press  Win + R , type:   shell:startup   and press Enter.
  2. The Startup folder opens.
  3. Put a SHORTCUT to DragDropSorter.exe (or to the .pyw) in that folder.
  Anything in that folder launches automatically when Windows starts.


NOTES
-----
  * The global shortcut key is now layout-independent: it works whether your
    keyboard language is English or Persian (it listens to the physical key).
  * Default shortcut: Ctrl + Shift + D. Change it in Settings.
  * Config is stored at:  %APPDATA%\DragDropSorter\config.json
