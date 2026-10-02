@echo off
title ABUZ8 VAULT
cd /d "%~dp0"
echo.
echo  ABUZ8 VAULT - permanent compounding memory
echo  1) Run with local brain (Qwen :8011 / Ollama) - sovereign, free
echo  2) Run with Haiku API (fast, needs ANTHROPIC_API_KEY)
echo  3) Run with no AI (heuristic, instant, rough)
echo  4) Search the vault
echo.
set /p c=Choose 1-4: 
if "%c%"=="1" python abuz8_vault.py --brain auto --since 2026-01-17
if "%c%"=="2" (if "%ANTHROPIC_API_KEY%"=="" set /p ANTHROPIC_API_KEY=Paste API key: )
if "%c%"=="2" python abuz8_vault.py --brain haiku --since 2026-01-17
if "%c%"=="3" python abuz8_vault.py --brain heuristic --since 2026-01-17
if "%c%"=="4" (set /p q=Search for: & python abuz8_vault.py search "%q%")
if exist "E:\ABU\ABUZ8_VAULT\index.html" start "" "E:\ABU\ABUZ8_VAULT\index.html"
pause
