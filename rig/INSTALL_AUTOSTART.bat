@echo off
cd /d "%~dp0"
schtasks /create /tn "ABUZ8 RIG" /tr "cmd /c cd /d \"%~dp0\" && set PYTHONIOENCODING=utf-8 && pythonw rig.py up" /sc onlogon /rl limited /f
echo.
echo ABUZ8 RIG will start every time you log in. Dashboard: http://127.0.0.1:8787
echo Remove it later with:  schtasks /delete /tn "ABUZ8 RIG" /f
pause
