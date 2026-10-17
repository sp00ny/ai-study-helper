@echo off
chcp 65001 >nul
echo Installing dependencies...
pip install -r requirements.txt pyinstaller
echo Building exe...
pyinstaller --onefile --windowed --name AIStudyHelper --add-data "inject;inject" main.py
echo Done. exe is in dist folder.
pause
