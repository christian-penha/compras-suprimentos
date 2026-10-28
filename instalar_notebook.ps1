# Prepara um computador novo (ex.: notebook da apresentação) para rodar o sistema.
# Pré-requisitos: Python 3.14 e PostgreSQL 18 instalados.
# Uso (na pasta webapp):  .\instalar_notebook.ps1 -Pacote C:\caminho\apresentacao
#   -Pacote é a pasta com dump.sql (+ nfe\ e tailwindcss.exe), gerada no computador de origem.
param([string]$Pacote = "", [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python não encontrado no PATH." }
if (-not (Test-Path "$PgBin\psql.exe")) { throw "PostgreSQL não encontrado em $PgBin (use -PgBin)." }

Write-Host "1/5 Ambiente virtual e dependências" -ForegroundColor Cyan
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "2/5 Arquivo .env" -ForegroundColor Cyan
if (-not (Test-Path .env)) {
    $chave = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(50))"
    @(
        "SECRET_KEY=$chave",
        "DEBUG=True",
        "DATABASE_URL=postgres://compras_app:admin@localhost:5432/compras_suprimentos"
    ) | Set-Content .env -Encoding ascii
} else { Write-Host "  .env já existe, mantido." }

Write-Host "3/5 Banco de dados (pede a senha do usuário postgres)" -ForegroundColor Cyan
& "$PgBin\psql.exe" -U postgres -h localhost -f setup_banco.sql

if ($Pacote -and (Test-Path "$Pacote\dump.sql")) {
    Write-Host "4/5 Restaurando os dados do pacote" -ForegroundColor Cyan
    $env:PGPASSWORD = "admin"
    & "$PgBin\psql.exe" -U compras_app -h localhost -d compras_suprimentos -v ON_ERROR_STOP=1 -q -f "$Pacote\dump.sql"
    if (Test-Path "$Pacote\nfe") { New-Item -ItemType Directory -Force privado | Out-Null; Copy-Item "$Pacote\nfe" privado -Recurse -Force }
    if (Test-Path "$Pacote\tailwindcss.exe") { New-Item -ItemType Directory -Force tools | Out-Null; Copy-Item "$Pacote\tailwindcss.exe" tools -Force }
} else {
    Write-Host "4/5 Sem pacote: criando banco vazio" -ForegroundColor Yellow
    .\.venv\Scripts\python.exe manage.py migrate
}

Write-Host "5/5 Verificação" -ForegroundColor Cyan
.\.venv\Scripts\python.exe manage.py migrate --check
.\.venv\Scripts\python.exe manage.py check

Write-Host "`nPronto. Para subir:  .\.venv\Scripts\python.exe manage.py runserver 8001" -ForegroundColor Green
