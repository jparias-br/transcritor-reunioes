# =============================================================
# build_v7.ps1 — Compila + empacota o Transcritor de Reuniões v7
# Uso: clique direito → "Executar com PowerShell"
#      ou: .\build_v7.ps1
# =============================================================

$ErrorActionPreference = "Stop"
$ROOT    = Split-Path -Parent $MyInvocation.MyCommand.Path
$VENV    = "$ROOT\venv\Scripts"
$SCRIPT  = "$ROOT\transcritor_v7.py"
$OUTDIR  = "$ROOT\build"
$DISTDIR = "$OUTDIR\transcritor_v7.dist"
$FFMPEG  = "C:\ffmpeg\bin"
$INNO    = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
$ISS     = "$ROOT\installer\setup_transcritor.iss"

Write-Host ""
Write-Host "======================================================"
Write-Host "  Transcritor de Reunioes v7 — Build Script"
Write-Host "======================================================"
Write-Host ""

# --------------------------------------------------------------
# 1. Nuitka
# --------------------------------------------------------------
Write-Host "[1/2] Compilando com Nuitka..."
Write-Host ""

& "$VENV\python.exe" -m nuitka `
    --standalone `
    --company-name="Joao Arias" `
    --product-name="Transcritor de Reunioes" `
    --file-version="7.0.0" `
    --product-version="7.0.0" `
    --windows-console-mode=disable `
    --windows-icon-from-ico="$ROOT\transcritor.ico" `
    --include-package-data=flet `
    --include-package-data=faster_whisper `
    --module-parameter=torch-disable-jit=yes `
    --output-dir="$OUTDIR" `
    "$SCRIPT"

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERRO: Nuitka falhou (codigo $LASTEXITCODE)." -ForegroundColor Red
    exit 1
}

# Copia ffmpeg.exe + DLLs necessarias (exclui ffplay e ffprobe)
if (Test-Path $FFMPEG) {
    Write-Host "   Copiando FFmpeg ($FFMPEG) para o dist..."
    $ffmpegFiles = @("ffmpeg.exe","avcodec-62.dll","avdevice-62.dll",
                     "avfilter-11.dll","avformat-62.dll","avutil-60.dll",
                     "swresample-6.dll","swscale-9.dll")
    foreach ($f in $ffmpegFiles) {
        $src = "$FFMPEG\$f"
        if (Test-Path $src) {
            Copy-Item $src "$DISTDIR\$f" -Force
        } else {
            Write-Host "   AVISO: $f nao encontrado em $FFMPEG" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "   ERRO: pasta FFmpeg nao encontrada: $FFMPEG" -ForegroundColor Red
    Write-Host "         Ajuste a variavel FFMPEG no topo do script." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "   Compilacao concluida: $DISTDIR"
Write-Host ""

# --------------------------------------------------------------
# 2. Inno Setup
# --------------------------------------------------------------
Write-Host "[2/2] Gerando instalador com Inno Setup..."
Write-Host ""

if (-not (Test-Path $INNO)) {
    Write-Host "ERRO: Inno Setup nao encontrado em:" -ForegroundColor Red
    Write-Host "      $INNO" -ForegroundColor Red
    Write-Host "      Baixe em https://jrsoftware.org/isinfo.php e instale." -ForegroundColor Yellow
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
Write-Host "  BUILD CONCLUIDO COM SUCESSO!"
Write-Host "  Instalador gerado em: $ROOT\installer\Output\"
Write-Host "======================================================"
Write-Host ""
