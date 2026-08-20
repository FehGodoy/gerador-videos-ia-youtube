# Sobe o painel web local (webapp/server.py) usando o venv do projeto.
# Uso:
#   .\run.ps1              # porta 8000, com auto-reload
#   .\run.ps1 -Port 8005    # outra porta, se a 8000 estiver ocupada

param(
    [int]$Port = 8000
)

$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Venv não encontrado em $Python. Rode primeiro: python -m venv .venv"
    exit 1
}

& $Python -m uvicorn webapp.server:app --reload --host 127.0.0.1 --port $Port
