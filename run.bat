@echo off
REM Duplo-clique nele sobe o painel web (run.ps1 faz o trabalho de verdade).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if errorlevel 1 (
    echo.
    echo Deu erro ao subir o servidor - veja a mensagem acima.
    pause
)
