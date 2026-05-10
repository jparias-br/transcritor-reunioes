# =============================================================
# build_inno.ps1 — Gera apenas o instalador (Inno Setup)
# Uso: .\build_inno.ps1
# Pré-requisito: Nuitka já executado (build\transcritor_v7.dist existe)
# =============================================================

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$INNO = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$ISS  = "$ROOT\installer\setup_transcritor.iss"

Write-Host ""
Write-Host "======================================================"
Write-Host "  Transcritor v7 — Gerando instalador (Inno Setup)"
Write-Host "======================================================"
Write-Host ""

if (-not (Test-Path $INNO)) {
    Write-Host "ERRO: Inno Setup nao encontrado em:" -ForegroundColor Red
    Write-Host "      $INNO" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $ISS)) {
    Write-Host "ERRO: Script .iss nao encontrado em $ISS" -ForegroundColor Red
    exit 1
}

& "$INNO" "$ISS"

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERRO: Inno Setup falhou (codigo $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "======================================================"
Write-Host "  INSTALADOR GERADO COM SUCESSO!"
Write-Host "  Arquivo: $ROOT\installer\Output\Setup_Transcritor_v7.exe"
Write-Host "======================================================"
Write-Host ""
