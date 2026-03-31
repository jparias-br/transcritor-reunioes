"""
Transcritor de Reuniões v6.0 - Versão Faster-Whisper
Arquitetura: Flet Assíncrono | Faster-Whisper (CTranslate2)
Modelo: Medium | Quantização: INT8 | Device: CPU
Desenvolvido por: João Arias e IA Gemini.
Ajustes: Novo Motor de Transcrição, Slicing de 60s e Performance Otimizada.
"""

import os
import sys
import ctypes
import inspect
import asyncio
import subprocess
import textwrap
import re
import shutil
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List

# Constante para ocultar consoles do FFmpeg no Windows
CREATE_NO_WINDOW = 0x08000000

# ==========================================================
# CONFIGURAÇÃO DE PATHS
# ==========================================================


def is_compiled() -> bool:
    """Verifica se o script está rodando como executável Nuitka."""
    return "__compiled__" in globals() or getattr(sys, "frozen", False)


def get_base_path() -> Path:
    """Retorna a raiz do projeto."""
    if is_compiled():
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.name.lower() == 'bin':
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parent


BASE_DIR = get_base_path()
EXE_DIR = Path(sys.executable).resolve(
).parent if is_compiled() else Path(__file__).resolve().parent

TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"

# Garante a existência das pastas de trabalho
for pasta in [OUTPUT_DIR, LOGS_DIR, TEMP_DIR]:
    pasta.mkdir(exist_ok=True, parents=True)


def get_model_path() -> Path:
    """Localiza a pasta do modelo Medium conforme o ambiente."""
    # Caminho no VSCode (Desenvolvimento)
    caminho_dev = BASE_DIR / "modelos" / "medium"
    # Caminho no Executável (Nuitka)
    caminho_bin = BASE_DIR / "bin" / "modelos" / "medium"

    if caminho_bin.exists():
        return caminho_bin
    return caminho_dev


# ==========================================================
# CONFIGURAÇÃO DE AMBIENTE
# ==========================================================

def configurar_ambiente_executavel():
    """Prepara DLLs e Assets para execução independente."""
    if not is_compiled():
        return

    os.environ['PATH'] = str(EXE_DIR) + os.pathsep + os.environ.get('PATH', '')

    torch_lib = EXE_DIR / "torch" / "lib"
    if torch_lib.exists():
        os.environ['PATH'] = str(torch_lib) + \
            os.pathsep + os.environ.get('PATH', '')
        try:
            ctypes.windll.kernel32.SetDllDirectoryW(str(torch_lib))
        except Exception:  # pylint: disable=broad-except
            pass

    os.environ['TORCH_JIT_DISABLE'] = '1'
    os.environ['PYTORCH_JIT'] = '0'
    inspect.getsource = lambda obj: ""
    inspect.getsourcelines = lambda obj: ([], 0)
    inspect.findsource = lambda obj: ([], 0)


# ==========================================================
# IMPORTS PESADOS (APÓS AMBIENTE)
# ==========================================================

from faster_whisper import WhisperModel
from pydub import AudioSegment

import flet as ft
from flet import (
    Page, Container, Column, Row, Text, IconButton, ProgressBar,
    FilePicker, FilePickerResultEvent, AlertDialog, TextButton,
    colors, icons, MainAxisAlignment
)


# ==========================================================
# UTILITÁRIOS DE MODELO E DICIONÁRIO
# ==========================================================

def converter_vocabulario(model_path: Path):
    """Garante a existência do vocabulary.json para o Faster-Whisper."""
    v_txt = model_path / "vocabulary.txt"
    v_json = model_path / "vocabulary.json"

    if v_txt.exists() and not v_json.exists():
        try:
            tokens = v_txt.read_text(encoding="utf-8").splitlines()
            with open(v_json, "w", encoding="utf-8") as f:
                json.dump(tokens, f)
        except Exception:  # pylint: disable=broad-except
            pass


def carregar_regras(caminho: Path) -> Dict[str, List[str]]:
    """Lê o arquivo de dicionário replace.txt."""
    regras = {}
    if not caminho.exists():
        return regras
    with caminho.open("r", encoding="utf-8") as file:
        for linha in file:
            linha = linha.strip()
            if not linha or ":" not in linha:
                continue
            palavra_correta, erros = linha.split(":", maxsplit=1)
            correta = palavra_correta.strip()
            lista_erros = [err.strip().strip('"') for err in erros.split(",")]
            regras[correta] = lista_erros
    return regras


def construir_mapa(regras: Dict[str, List[str]]) -> Dict[str, str]:
    """Constrói mapa de busca rápida para substituições."""
    mapa = {}
    for correta, erros in regras.items():
        for erro in erros:
            mapa[erro.lower()] = correta
    return mapa


def aplicar_correcoes(texto: str, mapa: Dict[str, str]) -> str:
    """Aplica substituições ortográficas via Regex."""
    termos = sorted(mapa.keys(), key=len, reverse=True)
    padroes = []
    for t in termos:
        e = re.escape(t)
        if t.startswith(" "):
            padroes.append(rf"\s{re.escape(t[1:])}\b")
        else:
            padroes.append(rf"\b{e}\b")

    if not padroes:
        return texto

    regex = re.compile("|".join(padroes), flags=re.IGNORECASE)

    def sub_func(match: re.Match) -> str:
        valor = match.group(0)
        vl = valor.lower()
        if vl.startswith(" "):
            p = vl[1:]
            return " " + mapa.get(" " + p, p)
        return mapa.get(vl, valor)

    return regex.sub(sub_func, texto)


# ==========================================================
# INTERFACE E LÓGICA PRINCIPAL (V6.0)
# ==========================================================

async def main(page: Page):
    """Loop principal da UI e Processamento."""
    page.title = "Transcritor de Reuniões v6.0"
    page.theme_mode = ft.ThemeMode.DARK
    page.window_width = 750
    page.window_height = 600
    page.window_resizable = False
    page.window_prevent_close = True

    # Estado Interno da Aplicação
    st = {
        "arquivo": None,
        "processando": False,
        "pausado": False,
        "solicitou_pausa": False,
        "solicitou_parar": False,
        "log_path": None,
        "total": 0,
        "t_inicio": None,
        "t_pausa_acumulada": 0,
        "t_momento_pausa": None
    }

    arquivo_text = ft.Text(
        "Nenhum arquivo selecionado",
        color=colors.RED,
        size=12,
        italic=True
    )
    btn_play = ft.IconButton(icons.PLAY_CIRCLE_FILLED, icon_size=40)
    btn_pause = ft.IconButton(icons.PAUSE_CIRCLE_FILLED, icon_size=40)
    btn_stop = ft.IconButton(icons.STOP_CIRCLE, icon_size=40)

    progress_bar = ft.ProgressBar(width=500, value=0, visible=False)
    status_esq = ft.Text("0/0", size=12, weight="bold", color=colors.CYAN)
    status_dir = ft.Text("0%", size=12, weight="bold", color=colors.CYAN)
    status_geral = ft.Text("Pronto", size=11, color=colors.GREY_400)
    console = ft.ListView(expand=True, spacing=3, auto_scroll=True)

    def adicionar_log(msg: str, cor: str = colors.GREEN_400):
        """Log visual no console e gravação imediata em disco."""
        ts = datetime.now().strftime("%H:%M:%S")
        linha = f"[{ts}] {msg}"
        console.controls.append(
            ft.Text(linha, color=cor, size=11, font_family="Consolas"))
        if st["log_path"]:
            try:
                with open(st["log_path"], "a", encoding="utf-8") as f:
                    f.write(linha + "\n")
            except Exception:  # pylint: disable=broad-except
                pass
        page.update()

    async def atualizar_botoes():
        """Gerencia estados e cores conforme regra de negócio."""
        if not st["arquivo"]:
            btn_play.disabled = True
            btn_play.icon_color = colors.GREY_400
            btn_pause.disabled = True
            btn_pause.icon_color = colors.GREY_400
            btn_stop.disabled = True
            btn_stop.icon_color = colors.GREY_400
        elif st["solicitou_pausa"] or st["solicitou_parar"]:
            btn_play.disabled = True
            btn_play.icon_color = colors.GREY_400
            btn_pause.disabled = True
            btn_pause.icon_color = colors.GREY_400
            btn_stop.disabled = True
            btn_stop.icon_color = colors.GREY_400
        elif not st["processando"]:
            btn_play.disabled = False
            btn_play.icon_color = colors.GREEN
            btn_pause.disabled = True
            btn_pause.icon_color = colors.GREY_400
            btn_stop.disabled = True
            btn_stop.icon_color = colors.GREY_400
        elif st["pausado"]:
            btn_play.disabled = True
            btn_play.icon_color = colors.GREY_400
            btn_pause.disabled = False
            btn_pause.icon_color = colors.BLUE
            btn_stop.disabled = False
            btn_stop.icon_color = colors.RED
        else:
            btn_play.disabled = True
            btn_play.icon_color = colors.GREY_400
            btn_pause.disabled = False
            btn_pause.icon_color = colors.AMBER
            btn_stop.disabled = False
            btn_stop.icon_color = colors.RED
        page.update()

    async def fechar_janela(e):
        """Limpeza ao fechar o programa."""
        if e.data == "close":
            shutil.rmtree(TEMP_DIR, ignore_errors=True)
            page.window_destroy()

    page.on_window_event = fechar_janela

    async def transcrever():
        """Fluxo de IA utilizando Faster-Whisper."""
        try:
            st["t_inicio"] = datetime.now()
            st["t_pausa_acumulada"] = 0
            nome_base = st["arquivo"].stem
            st["log_path"] = LOGS_DIR / \
                f"log_{nome_base}_{st['t_inicio'].strftime('%Y%m%d_%H%M%S')}.txt"
            adicionar_log(f"▶️ Iniciado (Motor v6.0): {nome_base}")

            # 1. Preparação de Áudio via FFmpeg
            status_geral.value = "Extraindo e normalizando áudio..."
            wav_p = TEMP_DIR / f"{nome_base}.wav"
            cmd = [
                "ffmpeg", "-y", "-i", str(st["arquivo"]),
                "-vn", "-ac", "1", "-ar", "16000",
                "-af", "loudnorm=I=-16",
                "-acodec", "pcm_s16le", str(wav_p)
            ]
            await asyncio.to_thread(subprocess.run, cmd, check=True, creationflags=CREATE_NO_WINDOW)

            # 2. Carregamento do Modelo Faster-Whisper
            adicionar_log("🔄 Carregando Faster-Whisper (Medium/INT8)...")
            m_path = get_model_path()
            if not m_path.exists():
                raise FileNotFoundError(
                    f"Pasta do modelo não encontrada em: {m_path}")

            await asyncio.to_thread(converter_vocabulario, m_path)

            model = await asyncio.to_thread(
                WhisperModel,
                str(m_path),
                device="cpu",
                compute_type="int8",
                local_files_only=True
            )

            # 3. Fatiamento de Áudio (60 Segundos)
            adicionar_log("✂️ Fatiando áudio em blocos de 60s...")
            audio = await asyncio.to_thread(AudioSegment.from_wav, wav_p)
            duracao_ms = len(audio)
            intervalo_ms = 60000  # 60 segundos

            arquivos_tmp = []
            cont = 1
            for i in range(0, duracao_ms, intervalo_ms):
                if st["solicitou_parar"]:
                    return
                fim = min(i + intervalo_ms, duracao_ms)
                fatia = audio[i:fim]
                caminho_seg = TEMP_DIR / f"seg_{cont}.wav"
                await asyncio.to_thread(fatia.export, caminho_seg, format="wav")
                arquivos_tmp.append(caminho_seg)
                cont += 1

            st["total"] = len(arquivos_tmp)
            adicionar_log(f"💾 {st['total']} fragmentos gerados.", colors.CYAN)
            progress_bar.visible = True

            # 4. Loop de Transcrição
            txt_out = OUTPUT_DIR / f"{nome_base}_transcricao.txt"
            with open(txt_out, "w", encoding="utf-8") as f_out:
                for i, tmp in enumerate(arquivos_tmp, 1):
                    # Gestão de Pausa
                    if st["solicitou_pausa"]:
                        st["pausado"] = True
                        st["solicitou_pausa"] = False
                        st["t_momento_pausa"] = datetime.now()
                        status_geral.value = "Pausado"
                        adicionar_log(
                            f"⏸️ Pausado no bloco {i}/{st['total']}.", colors.BLUE)
                        await atualizar_botoes()
                        while st["pausado"] and not st["solicitou_parar"]:
                            await asyncio.sleep(0.2)

                        if not st["solicitou_parar"]:
                            dur_p = (datetime.now() -
                                     st["t_momento_pausa"]).total_seconds()
                            st["t_pausa_acumulada"] += dur_p
                            adicionar_log("▶️ Retomando transcrição...")
                            await atualizar_botoes()

                    if st["solicitou_parar"]:
                        st["processando"] = False
                        status_geral.value = "Interrompido"
                        await atualizar_botoes()
                        return

                    # Progresso da UI
                    porc = int((i / st["total"]) * 100)
                    progress_bar.value = i / st["total"]
                    status_esq.value = f"{i}/{st['total']}"
                    status_dir.value = f"{porc}%"
                    status_geral.value = f"Processando... {i}/{st['total']}"
                    adicionar_log(
                        f"📝 Transcrevendo bloco {i}/{st['total']}...")

                    # Transcrição via Faster-Whisper
                    # beam_size=5 é o padrão para o modelo medium manter precisão
                    segmentos, _ = await asyncio.to_thread(
                        model.transcribe, str(tmp), language="pt", beam_size=5
                    )

                    # Unifica o texto dos sub-segmentos do bloco de 60s
                    texto_bloco = ""
                    for s in segmentos:
                        texto_bloco += s.text + " "

                    texto_limpo = texto_bloco.strip()
                    if texto_limpo:
                        f_out.write(textwrap.fill(texto_limpo, 70) + "\n\n")
                        f_out.flush()
                        os.fsync(f_out.fileno())

                    tmp.unlink(missing_ok=True)

            # 5. Processamento do Dicionário (replace.txt)
            rp = BASE_DIR / "replace.txt"
            if rp.exists():
                if not st["solicitou_parar"]:
                    adicionar_log("🔄 Aplicando regras do replace.txt...")
                    regras = await asyncio.to_thread(carregar_regras, rp)
                    mapa = await asyncio.to_thread(construir_mapa, regras)
                    txt_final = aplicar_correcoes(
                        txt_out.read_text(encoding="utf-8"), mapa)
                    txt_out.write_text(txt_final, encoding="utf-8")
                    adicionar_log("✅ Correções ortográficas finalizadas.")
            else:
                adicionar_log(
                    "ℹ️ replace.txt não encontrado - mantendo texto original", colors.AMBER)

            # 6. Modal de Encerramento (Conforme v5.7)
            t_fim = datetime.now()
            t_total = (t_fim - st["t_inicio"]).total_seconds()
            t_proc = t_total - st["t_pausa_acumulada"]

            adicionar_log("🎉 Processo concluído com sucesso!", colors.CYAN)

            def fechar_modal(e):  # pylint: disable=unused-argument
                page.dialog.open = False
                page.update()

            page.dialog = ft.AlertDialog(
                title=ft.Text("✅ Transcrição Concluída!"),
                content=ft.Text(
                    f"Arquivo: {txt_out.name}\n"
                    f"Local: {OUTPUT_DIR}\n\n"
                    f"Tempo de Operação: {t_total/60:.1f} min\n"
                    f"Tempo Efetivo de IA: {t_proc/60:.1f} min\n"
                    f"Pausas: {st['t_pausa_acumulada']/60:.1f} min"
                ),
                actions=[ft.TextButton("OK", on_click=fechar_modal)]
            )
            page.dialog.open = True
            page.update()

        except Exception as ex:
            adicionar_log(f"❌ ERRO CRÍTICO: {str(ex)}", colors.RED)
        finally:
            st["processando"] = False
            status_geral.value = "Finalizado"
            await atualizar_botoes()

    # Handlers da Interface
    async def h_arquivo(e: FilePickerResultEvent):
        if e.files:
            st["arquivo"] = Path(e.files[0].path)
            arquivo_text.value = st["arquivo"].name
            arquivo_text.color = colors.GREEN
            adicionar_log(f"📁 Arquivo carregado: {st['arquivo'].name}")
            await atualizar_botoes()

    async def h_play(e):  # pylint: disable=unused-argument
        st["processando"] = True
        st["solicitou_parar"] = False
        st["pausado"] = False
        console.controls.clear()
        status_geral.value = "Iniciando motor..."
        await atualizar_botoes()
        asyncio.create_task(transcrever())

    async def h_pause(e):  # pylint: disable=unused-argument
        if not st["pausado"]:
            st["solicitou_pausa"] = True
            status_geral.value = "Pausando após o bloco..."
            adicionar_log(
                "⏳ Aguardando conclusão do bloco atual para pausar...")
        else:
            st["pausado"] = False
        await atualizar_botoes()

    async def h_stop(e):  # pylint: disable=unused-argument
        st["solicitou_parar"] = True
        status_geral.value = "Parando..."
        adicionar_log("⏳ Solicitada interrupção do processo...")
        await atualizar_botoes()

    picker = ft.FilePicker(on_result=h_arquivo)
    page.overlay.append(picker)
    btn_play.on_click = h_play
    btn_pause.on_click = h_pause
    btn_stop.on_click = h_stop

    # Layout Principal (Identidade Visual Mantida)
    page.add(Container(content=Column([
        Text("Transcritor de Reuniões", size=24, font_family="Segoe UI Light"),
        Container(Text("Versão 6.0", size=10, color=colors.GREY_400),
                  tooltip="Desenvolvido por\nJoão Arias & Gemini\nSão Paulo / SP / Brasil"),
        Row([ft.IconButton(icons.FOLDER_OPEN,
                           on_click=lambda _: picker.pick_files(allow_multiple=False,
                                                                allowed_extensions=[
                                                                    "mp4", "m4a", "mp3", "wav", "mov", "avi"]
                                                                ), icon_color=colors.BLUE),
             ft.Text("Arquivo:", weight="bold", size=12), arquivo_text]),
        Row([btn_play, btn_pause, btn_stop],
            alignment=MainAxisAlignment.CENTER),
        Column([progress_bar, Row([status_esq, status_dir],
               alignment=MainAxisAlignment.SPACE_BETWEEN), status_geral]),
        Container(content=console, bgcolor=colors.BLACK,
                  height=250, border_radius=8, padding=10)
    ], spacing=5), padding=10))
    await atualizar_botoes()

if __name__ == "__main__":
    configurar_ambiente_executavel()
    ft.app(target=main)
