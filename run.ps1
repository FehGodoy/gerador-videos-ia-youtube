# Sobe o painel web local (webapp/server.py) usando o venv do projeto.
# Uso:
#   .\run.ps1              # porta 8010, com auto-reload
#   .\run.ps1 -Port 8005    # outra porta, se a 8010 estiver ocupada

param(
    [int]$Port = 8010
)

$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Error "Venv não encontrado em $Python. Rode primeiro: python -m venv .venv"
    exit 1
}

if (Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue) {
    Write-Error "Porta $Port já está em uso (ou reservada pelo Windows). Tente outra: .\run.ps1 -Port 8011"
    exit 1
}

& $Python -m uvicorn webapp.server:app --reload --host 127.0.0.1 --port $Port
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "O servidor caiu com erro (código $LASTEXITCODE) — veja a mensagem acima." -ForegroundColor Red
    Write-Host "Se for erro de porta/permissão, tente: .\run.ps1 -Port 8011" -ForegroundColor Yellow
}
