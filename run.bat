@echo off
REM Duplo-clique nele sobe o painel web (run.ps1 faz o trabalho de verdade).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
