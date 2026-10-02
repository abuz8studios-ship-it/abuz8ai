@echo off
title ABUZ8 RIG
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "" http://127.0.0.1:8787
python rig.py up
pause
