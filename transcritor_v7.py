"""
Transcritor de Reuniões v7.0
Arquitetura : Fcllet Assíncrono | Faster-Whisper (CTranslate2)
Modelo      : Medium | Quantização: INT8 | Device: CPU

Modo Arquivo  (v6.3 preservado) — transcreve MP4/áudio gravado.
Modo Ao Vivo  (novo v7.0)       — captura Microfone + WASAPI Loopback
                                  em tempo real, produz .txt incremental.

Dependência nova: pyaudiowpatch (WASAPI loopback no Windows).

Desenvolvido por: João Arias e IA Claude.

Histórico:
  v6.1 – Logs granulares, vad_filter, beam_size=2, cpu_threads auto.
  v6.2 – FFmpeg via thread (evita bloqueio asyncio com \\r).
  v6.3 – Feedback imediato pausa/stop, fatia→segmento.
  v7.0 – Modo Ao Vivo: pyaudiowpatch + WASAPI Loopback + tabs UI.
"""

import os
import sys
import atexit
import ctypes
import inspect
import asyncio
import subprocess
import threading
import textwrap
import re
import shutil
import json
import queue
import time
import wave
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pyaudiowpatch as pyaudio

# Oculta console do FFmpeg no Windows
CREATE_NO_WINDOW = 0x08000000

# ==========================================================
# CONFIGURAÇÃO DE PATHS
# ==========================================================

def is_compiled() -> bool:
    return "__compiled__" in globals() or getattr(sys, "frozen", False)


def get_base_path() -> Path:
    if is_compiled():
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir.parent if exe_dir.name.lower() == "bin" else exe_dir
    return Path(__file__).resolve().parent


BASE_DIR = get_base_path()
EXE_DIR  = (Path(sys.executable).resolve().parent
            if is_compiled() else Path(__file__).resolve().parent)


def get_data_dir() -> Path:
    """
    Quando compilado: Documentos\Transcritor  (gravável pelo usuário).
    Em desenvolvimento: pasta do script (comportamento original).
    """
    if is_compiled():
        docs = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
        return docs / "Transcritor"
    return BASE_DIR


DATA_DIR   = get_data_dir()
TEMP_DIR   = DATA_DIR / "temp"
OUTPUT_DIR = DATA_DIR / "output"
LOGS_DIR   = DATA_DIR / "logs"

for _pasta in [OUTPUT_DIR, LOGS_DIR, TEMP_DIR]:
    _pasta.mkdir(exist_ok=True, parents=True)


def get_model_path() -> Path:
    caminho_bin = BASE_DIR / "bin" / "modelos" / "medium"
    caminho_dev = BASE_DIR / "modelos" / "medium"
    return caminho_bin if caminho_bin.exists() else caminho_dev


def get_ffmpeg_exe() -> str:
    """Prefere ffmpeg.exe na pasta do app; fallback para o PATH do sistema."""
    local = BASE_DIR / "ffmpeg.exe"
    return str(local) if local.exists() else "ffmpeg"


# ==========================================================
# CONFIGURAÇÃO DE AMBIENTE (executável Nuitka)
# ==========================================================

def configurar_ambiente_executavel():
    if not is_compiled():
        return
    os.environ["PATH"] = str(EXE_DIR) + os.pathsep + os.environ.get("PATH", "")
    torch_lib = EXE_DIR / "torch" / "lib"
    if torch_lib.exists():
        os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
        try:
            ctypes.windll.kernel32.SetDllDirectoryW(str(torch_lib))
        except Exception:  # pylint: disable=broad-except
            pass
    os.environ["TORCH_JIT_DISABLE"] = "1"
    os.environ["PYTORCH_JIT"] = "0"
    inspect.getsource      = lambda obj: ""
    inspect.getsourcelines = lambda obj: ([], 0)
    inspect.findsource     = lambda obj: ([], 0)


# ==========================================================
# IMPORTS PESADOS (após ambiente)
# ==========================================================

from faster_whisper import WhisperModel  # noqa: E402
from pydub import AudioSegment           # noqa: E402
import flet as ft                        # noqa: E402
from flet import (                       # noqa: E402
    Page, Container, Column, Row, Text, IconButton, ProgressBar,
    FilePicker, FilePickerResultEvent, colors, icons, MainAxisAlignment,
)

# ==========================================================
# CONSTANTES DE ÁUDIO AO VIVO
# ==========================================================

RATE_TARGET    = 16_000       # Hz exigido pelo Whisper
PA_FORMAT      = pyaudio.paInt16
BYTES_PER_SAMP = 2            # int16 = 2 bytes por amostra
PA_CHUNK       = 512          # frames por callback PyAudio
LIVE_CHUNK_S   = 10           # segundos por bloco de transcrição

# ==========================================================
# UTILITÁRIOS DE MODELO E DICIONÁRIO (v6.3 inalterado)
# ==========================================================

def converter_vocabulario(model_path: Path):
    v_txt  = model_path / "vocabulary.txt"
    v_json = model_path / "vocabulary.json"
    if v_txt.exists() and not v_json.exists():
        try:
            tokens = v_txt.read_text(encoding="utf-8").splitlines()
            with open(v_json, "w", encoding="utf-8") as f:
                json.dump(tokens, f)
        except Exception:  # pylint: disable=broad-except
            pass


def carregar_regras(caminho: Path) -> Dict[str, List[str]]:
    regras: Dict[str, List[str]] = {}
    if not caminho.exists():
        return regras
    with caminho.open("r", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or ":" not in linha:
                continue
            palavra_correta, erros = linha.split(":", maxsplit=1)
            regras[palavra_correta.strip()] = [
                e.strip().strip('"') for e in erros.split(",")
            ]
    return regras


def construir_mapa(regras: Dict[str, List[str]]) -> Dict[str, str]:
    return {erro.lower(): correta
            for correta, erros in regras.items()
            for erro in erros}


def aplicar_correcoes(texto: str, mapa: Dict[str, str]) -> str:
    termos   = sorted(mapa.keys(), key=len, reverse=True)
    padroes  = []
    for t in termos:
        e = re.escape(t)
        padroes.append(rf"\s{re.escape(t[1:])}\b" if t.startswith(" ")
                       else rf"\b{e}\b")
    if not padroes:
        return texto
    regex = re.compile("|".join(padroes), flags=re.IGNORECASE)

    def sub_func(match: re.Match) -> str:
        v  = match.group(0)
        vl = v.lower()
        if vl.startswith(" "):
            p = vl[1:]
            return " " + mapa.get(" " + p, p)
        return mapa.get(vl, v)

    return regex.sub(sub_func, texto)


# ==========================================================
# UTILITÁRIOS DE ÁUDIO AO VIVO (novo v7.0)
# ==========================================================

def bytes_to_mono_float32(raw: bytes, channels: int) -> np.ndarray:
    """int16 bytes → float32 mono numpy array."""
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    return arr


def resample_linear(data: np.ndarray, orig_sr: int, tgt_sr: int) -> np.ndarray:
    """Reamostragem linear simples (suficiente para fala)."""
    if orig_sr == tgt_sr or len(data) == 0:
        return data
    new_len = max(1, int(len(data) * tgt_sr / orig_sr))
    return np.interp(np.linspace(0, len(data) - 1, new_len),
                     np.arange(len(data)), data)


def mix_e_salvar_wav(
    mic_raw:  bytes, mic_sr:  int, mic_ch:  int,
    loop_raw: bytes, loop_sr: int, loop_ch: int,
    dest: Path,
) -> bool:
    """
    Mistura microfone + loopback em WAV 16 kHz mono.
    Retorna False se o resultado for silêncio puro.
    """
    partes: List[np.ndarray] = []
    if mic_raw:
        arr = bytes_to_mono_float32(mic_raw, mic_ch)
        partes.append(resample_linear(arr, mic_sr, RATE_TARGET))
    if loop_raw:
        arr = bytes_to_mono_float32(loop_raw, loop_ch)
        partes.append(resample_linear(arr, loop_sr, RATE_TARGET))
    if not partes:
        return False

    # Alinha pelo menor (sincroniza os dois streams)
    n = min(len(a) for a in partes)
    mixed = np.stack([a[:n] for a in partes]).mean(axis=0)

    # Normaliza suavemente
    mx = np.abs(mixed).max()
    if mx < 0.001:
        return False           # silêncio puro
    mixed = mixed * (0.9 / mx)

    pcm = (mixed * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(BYTES_PER_SAMP)
        wf.setframerate(RATE_TARGET)
        wf.writeframes(pcm.tobytes())
    return True


def listar_dispositivos() -> dict:
    """
    Enumera todos os microfones e dispositivos WASAPI Loopback disponíveis.
    Retorna dict com listas e índices padrão sugeridos.
    """
    pa = pyaudio.PyAudio()
    try:
        mics: List[dict]      = []
        loopbacks: List[dict] = []

        # Microfones: apenas dispositivos WASAPI de entrada (evita triplicação
        # causada pelas demais APIs — MME, DirectSound — que listam o mesmo
        # hardware físico várias vezes)
        try:
            wasapi_api_idx = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        except Exception:  # pylint: disable=broad-except
            wasapi_api_idx = None

        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if int(info["maxInputChannels"]) < 1:
                continue
            if "[Loopback]" in info["name"]:
                continue
            if wasapi_api_idx is not None and info["hostApi"] != wasapi_api_idx:
                continue
            mics.append({
                "index": i,
                "name":  info["name"],
                "rate":  int(info["defaultSampleRate"]),
                "ch":    1,
            })

        # Loopbacks WASAPI
        try:
            for lb in pa.get_loopback_device_info_generator():
                loopbacks.append({
                    "index": int(lb["index"]),
                    "name":  lb["name"],
                    "rate":  int(lb["defaultSampleRate"]),
                    "ch":    int(lb.get("maxInputChannels", 2)),
                })
        except Exception:  # pylint: disable=broad-except
            pass

        # Índices padrão sugeridos
        default_mic_idx = None
        try:
            default_mic_idx = int(pa.get_default_input_device_info()["index"])
        except Exception:  # pylint: disable=broad-except
            pass

        default_loop_idx = None
        try:
            wasapi   = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            spk_name = pa.get_device_info_by_index(
                wasapi["defaultOutputDevice"])["name"]
            for lb in loopbacks:
                if spk_name in lb["name"]:
                    default_loop_idx = lb["index"]
                    break
            if default_loop_idx is None and loopbacks:
                default_loop_idx = loopbacks[0]["index"]
        except Exception:  # pylint: disable=broad-except
            pass

        return {
            "mics":             mics,
            "loopbacks":        loopbacks,
            "default_mic_idx":  default_mic_idx,
            "default_loop_idx": default_loop_idx,
        }
    finally:
        pa.terminate()


# ==========================================================
# INTERFACE E LÓGICA PRINCIPAL v7.0
# ==========================================================

async def main(page: Page):
    page.title            = "Transcritor de Reuniões v7.0"
    page.theme_mode       = ft.ThemeMode.DARK
    page.window_width     = 750
    page.window_height    = 700
    page.window_resizable = False

    # ----------------------------------------------------------
    # Cache de modelo compartilhado entre os dois modos
    # ----------------------------------------------------------
    _model_cache: List[Optional[WhisperModel]] = [None]

    # ----------------------------------------------------------
    # UI COMPARTILHADA
    # ----------------------------------------------------------
    progress_bar    = ProgressBar(width=500, value=0, visible=False)
    status_esq      = Text("0/0",    size=12, weight="bold", color=colors.CYAN)
    status_dir      = Text("0%",     size=12, weight="bold", color=colors.CYAN)
    status_geral    = Text("Pronto", size=11, color=colors.GREY_400)
    console_arquivo = ft.ListView(expand=True, spacing=3, auto_scroll=True)
    console_ao_vivo = ft.ListView(expand=True, spacing=3, auto_scroll=True)

    # Estados (precisam existir antes dos handlers)
    st: dict = {
        "arquivo": None, "processando": False, "pausado": False,
        "solicitou_pausa": False, "solicitou_parar": False,
        "log_path": None, "total": 0, "t_inicio": None,
        "t_pausa_acumulada": 0, "t_momento_pausa": None,
    }
    st_live: dict = {
        "gravando": False, "solicitou_parar": False,
        "log_path": None, "txt_path": None,
        "frames_mic": [], "frames_loop": [],
        "frames_lock": threading.Lock(),
        "chunk_q": queue.Queue(),
        "devs": None,
        "t_inicio": None, "n_chunks": 0,
        "_mics": [], "_loops": [],
    }

    def adicionar_log(msg: str, cor: str = colors.GREEN_400):
        ts    = datetime.now().strftime("%H:%M:%S")
        linha = f"[{ts}] {msg}"
        console_arquivo.controls.append(
            Text(linha, color=cor, size=11, font_family="Consolas"))
        lp = st.get("log_path") or st_live.get("log_path")
        if lp:
            try:
                with open(lp, "a", encoding="utf-8") as fh:
                    fh.write(linha + "\n")
            except Exception:  # pylint: disable=broad-except
                pass
        page.update()

    def adicionar_texto_ao_vivo(texto: str):
        console_ao_vivo.controls.append(
            Text(texto.strip(), color=colors.WHITE, size=12))
        page.update()

    async def get_model() -> WhisperModel:
        if _model_cache[0] is None:
            adicionar_log("🧠 Carregando modelo de IA (Medium/INT8)...")
            adicionar_log("   ⚠️ Este passo pode levar 1-2 minutos.", colors.AMBER)
            m_path = get_model_path()
            if not m_path.exists():
                raise FileNotFoundError(f"Modelo não encontrado: {m_path}")
            await asyncio.to_thread(converter_vocabulario, m_path)
            _model_cache[0] = await asyncio.to_thread(
                WhisperModel, str(m_path),
                device="cpu", compute_type="int8",
                cpu_threads=os.cpu_count() or 4,
                local_files_only=True,
            )
            adicionar_log(
                f"✅ Modelo carregado. Usando {os.cpu_count() or 4} núcleos de CPU.")
        return _model_cache[0]

    # Limpeza e encerramento forçado ao sair (cobre window_destroy e sys.exit)
    def _saida_forcada():
        if st_live["gravando"]:
            st_live["solicitou_parar"] = True
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        os._exit(0)

    atexit.register(_saida_forcada)

    # ==========================================================
    # TAB 0 — MODO ARQUIVO  (v6.3 preservado)
    # ==========================================================

    arquivo_text = Text("Nenhum arquivo selecionado",
                        color=colors.RED, size=12, italic=True)
    btn_play  = IconButton(icons.PLAY_CIRCLE_FILLED,  icon_size=40)
    btn_pause = IconButton(icons.PAUSE_CIRCLE_FILLED, icon_size=40)
    btn_stop  = IconButton(icons.STOP_CIRCLE,         icon_size=40)

    async def atualizar_botoes():
        if not st["arquivo"]:
            for b in [btn_play, btn_pause, btn_stop]:
                b.disabled = True; b.icon_color = colors.GREY_400
        elif st["solicitou_pausa"] or st["solicitou_parar"]:
            for b in [btn_play, btn_pause, btn_stop]:
                b.disabled = True; b.icon_color = colors.GREY_400
        elif not st["processando"]:
            btn_play.disabled  = False; btn_play.icon_color  = colors.GREEN
            btn_pause.disabled = True;  btn_pause.icon_color = colors.GREY_400
            btn_stop.disabled  = True;  btn_stop.icon_color  = colors.GREY_400
        elif st["pausado"]:
            btn_play.disabled  = True;  btn_play.icon_color  = colors.GREY_400
            btn_pause.disabled = False; btn_pause.icon_color = colors.BLUE
            btn_stop.disabled  = False; btn_stop.icon_color  = colors.RED
        else:
            btn_play.disabled  = True;  btn_play.icon_color  = colors.GREY_400
            btn_pause.disabled = False; btn_pause.icon_color = colors.AMBER
            btn_stop.disabled  = False; btn_stop.icon_color  = colors.RED
        page.update()

    async def transcrever():
        try:
            st["t_inicio"] = datetime.now()
            st["t_pausa_acumulada"] = 0
            nome_base      = st["arquivo"].stem
            st["log_path"] = (LOGS_DIR /
                f"log_{nome_base}_{st['t_inicio'].strftime('%Y%m%d_%H%M%S')}.txt")
            adicionar_log(f"▶️ Iniciado (Motor v7.0): {nome_base}")

            # 1. FFmpeg
            status_geral.value = "Extraindo e normalizando áudio..."
            wav_p = TEMP_DIR / f"{nome_base}.wav"
            adicionar_log("🎵 Iniciando conversão com FFmpeg...")
            adicionar_log("⚙️ Normalizando volume (loudnorm)... aguarde.", colors.GREY_400)

            cmd = [
                get_ffmpeg_exe(), "-y", "-i", str(st["arquivo"]),
                "-vn", "-ac", "1", "-ar", "16000",
                "-af", "loudnorm=I=-16", "-acodec", "pcm_s16le", str(wav_p),
            ]
            resultado_ffmpeg = [None]

            def rodar_ffmpeg():
                resultado_ffmpeg[0] = subprocess.run(
                    cmd, stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW)

            t_ffmpeg = threading.Thread(target=rodar_ffmpeg, daemon=True)
            t_ffmpeg.start()
            contador = 0
            while t_ffmpeg.is_alive():
                await asyncio.sleep(30)
                if t_ffmpeg.is_alive():
                    contador += 1
                    adicionar_log(
                        f"   ⏱️ FFmpeg em andamento... {contador * 30}s", colors.GREY_400)
            t_ffmpeg.join()

            if resultado_ffmpeg[0] is None or resultado_ffmpeg[0].returncode != 0:
                stderr_txt = (resultado_ffmpeg[0].stderr.decode("utf-8", errors="ignore")
                              if resultado_ffmpeg[0] else "")
                raise RuntimeError(f"FFmpeg falhou.\n{stderr_txt[-300:]}")
            adicionar_log("✅ Áudio convertido e normalizado.")

            # 2. Modelo
            model = await get_model()

            # 3. Fatiamento
            adicionar_log("📊 Analisando duração do áudio...")
            audio      = await asyncio.to_thread(AudioSegment.from_wav, wav_p)
            duracao_ms = len(audio)
            adicionar_log(f"⏱️ Duração: {duracao_ms / 60000:.1f} minutos.")
            adicionar_log("✂️ Segmentando em blocos de 60s...")
            intervalo_ms = 60_000
            total_segs   = (duracao_ms + intervalo_ms - 1) // intervalo_ms
            segs_tmp     = []

            for idx, ini in enumerate(range(0, duracao_ms, intervalo_ms), 1):
                if st["solicitou_parar"]:
                    return
                seg_p = TEMP_DIR / f"seg_{idx}.wav"
                await asyncio.to_thread(
                    audio[ini:ini + intervalo_ms].export, seg_p, format="wav")
                adicionar_log(
                    f"   ✔️ Segmento {idx}/{total_segs} criado.", colors.GREY_400)
                segs_tmp.append(seg_p)

            st["total"] = len(segs_tmp)
            adicionar_log(f"💾 {st['total']} segmentos prontos.", colors.CYAN)
            progress_bar.visible = True

            # 4. Transcrição
            txt_out = OUTPUT_DIR / f"{nome_base}_transcricao.txt"
            with open(txt_out, "w", encoding="utf-8") as f_out:
                for i, tmp in enumerate(segs_tmp, 1):
                    # Pausa
                    if st["solicitou_pausa"]:
                        st["pausado"]        = True
                        st["solicitou_pausa"] = False
                        st["t_momento_pausa"] = datetime.now()
                        status_geral.value   = "Pausado"
                        adicionar_log(
                            f"⏸️ Pausado antes do segmento {i}/{st['total']}.",
                            colors.BLUE)
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
                        st["processando"]   = False
                        status_geral.value  = "Interrompido"
                        adicionar_log(
                            f"🛑 Interrompido antes do segmento {i}.", colors.RED)
                        await atualizar_botoes()
                        return

                    progress_bar.value = i / st["total"]
                    status_esq.value   = f"{i}/{st['total']}"
                    status_dir.value   = f"{int(i / st['total'] * 100)}%"
                    status_geral.value = f"Processando... {i}/{st['total']}"
                    adicionar_log(f"📝 Transcrevendo segmento {i}/{st['total']}...")

                    segs, _ = await asyncio.to_thread(
                        model.transcribe, str(tmp), language="pt",
                        beam_size=2, vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                    )
                    texto = "".join(s.text for s in segs).strip()
                    if texto:
                        f_out.write(textwrap.fill(texto, 70) + "\n\n")
                        f_out.flush()
                    tmp.unlink(missing_ok=True)

            # 5. replace.txt
            rp = BASE_DIR / "replace.txt"
            if rp.exists() and not st["solicitou_parar"]:
                adicionar_log("🔄 Aplicando replace.txt...")
                regras = await asyncio.to_thread(carregar_regras, rp)
                mapa   = await asyncio.to_thread(construir_mapa, regras)
                txt_out.write_text(
                    aplicar_correcoes(txt_out.read_text(encoding="utf-8"), mapa),
                    encoding="utf-8")
                adicionar_log("✅ Correções aplicadas.")
            elif not rp.exists():
                adicionar_log("ℹ️ replace.txt não encontrado.", colors.AMBER)

            # 6. Modal
            t_fim   = datetime.now()
            t_total = (t_fim - st["t_inicio"]).total_seconds()
            t_proc  = t_total - st["t_pausa_acumulada"]
            adicionar_log("🎉 Concluído com sucesso!", colors.CYAN)

            def fechar_modal(e):  # pylint: disable=unused-argument
                page.dialog.open = False
                page.update()

            page.dialog = ft.AlertDialog(
                title=Text("✅ Transcrição Concluída!"),
                content=Text(
                    f"Arquivo: {txt_out.name}\nLocal: {OUTPUT_DIR}\n\n"
                    f"Tempo de Operação: {t_total/60:.1f} min\n"
                    f"Tempo Efetivo de IA: {t_proc/60:.1f} min\n"
                    f"Pausas: {st['t_pausa_acumulada']/60:.1f} min"),
                actions=[ft.TextButton("OK", on_click=fechar_modal)],
            )
            page.dialog.open = True
            page.update()

        except Exception as ex:  # pylint: disable=broad-except
            adicionar_log(f"❌ ERRO CRÍTICO: {ex}", colors.RED)
        finally:
            st["processando"]  = False
            status_geral.value = "Finalizado"
            await atualizar_botoes()

    # Handlers modo arquivo
    async def h_arquivo(e: FilePickerResultEvent):
        if e.files:
            st["arquivo"]       = Path(e.files[0].path)
            arquivo_text.value  = st["arquivo"].name
            arquivo_text.color  = colors.GREEN
            console_arquivo.controls.clear()
            progress_bar.visible = False
            progress_bar.value   = 0
            status_esq.value     = "0/0"
            status_dir.value     = "0%"
            status_geral.value   = "Pronto"
            adicionar_log(f"📁 Carregado: {st['arquivo'].name}")
            await atualizar_botoes()

    async def h_play(e):  # pylint: disable=unused-argument
        st["processando"] = True
        st["solicitou_parar"] = st["pausado"] = False
        console_arquivo.controls.clear()
        status_geral.value = "Iniciando motor v7.0..."
        await atualizar_botoes()
        page.run_task(transcrever)

    async def h_pause(e):  # pylint: disable=unused-argument
        if not st["pausado"]:
            st["solicitou_pausa"] = True
            btn_pause.disabled = btn_stop.disabled = True
            btn_pause.icon_color = btn_stop.icon_color = colors.GREY_400
            status_geral.value = "Aguardando segmento concluir para pausar..."
            page.update()
            adicionar_log(
                "⏳ Pausa solicitada — aguardando segmento atual...", colors.AMBER)
        else:
            st["pausado"] = False
            adicionar_log("▶️ Retomando.")
        await atualizar_botoes()

    async def h_stop(e):  # pylint: disable=unused-argument
        st["solicitou_parar"] = True
        btn_pause.disabled = btn_stop.disabled = True
        btn_pause.icon_color = btn_stop.icon_color = colors.GREY_400
        status_geral.value = "Aguardando segmento concluir para parar..."
        page.update()
        adicionar_log(
            "⏳ Parada solicitada — concluindo segmento atual...", colors.AMBER)
        await atualizar_botoes()

    picker = FilePicker(on_result=h_arquivo)
    page.overlay.append(picker)
    btn_play.on_click  = h_play
    btn_pause.on_click = h_pause
    btn_stop.on_click  = h_stop

    # ==========================================================
    # TAB 1 — MODO AO VIVO  (novo v7.0)
    # ==========================================================

    dd_mic = ft.Dropdown(
        label="🎤  Microfone",
        hint_text="Detectando...",
        width=560, disabled=True,
        options=[],
    )
    dd_loop = ft.Dropdown(
        label="🔊  Saída de Som (áudio que você ouve)",
        hint_text="Detectando...",
        width=560, disabled=True,
        options=[],
    )
    lbl_live_status = Text("Aguardando início...", size=11, color=colors.GREY_400)
    lbl_chunks      = Text("Blocos transcritos: 0", size=11, color=colors.CYAN)

    btn_live_start = ft.ElevatedButton(
        "🔴  Iniciar Gravação",
        bgcolor=colors.RED_900, color=colors.WHITE, height=44)
    btn_live_stop = ft.ElevatedButton(
        "⏹  Encerrar Reunião",
        bgcolor=colors.GREY_800, color=colors.WHITE, height=44, disabled=True)
    btn_refresh = ft.IconButton(
        icons.REFRESH, tooltip="Redetectar dispositivos",
        icon_color=colors.BLUE_400)

    def _popular_dropdowns(resultado: dict):
        """Popula os dropdowns com os dispositivos encontrados (chamado da thread)."""
        mics      = resultado["mics"]
        loopbacks = resultado["loopbacks"]
        st_live["_mics"]  = mics
        st_live["_loops"] = loopbacks

        dd_mic.options = [
            ft.dropdown.Option(key=str(m["index"]), text=m["name"][:60])
            for m in mics
        ]
        dd_loop.options = [
            ft.dropdown.Option(key=str(lb["index"]), text=lb["name"][:60])
            for lb in loopbacks
        ] + [ft.dropdown.Option(key="none", text="(nenhum — só microfone)")]

        # Pré-seleciona os padrões
        def_mic  = resultado["default_mic_idx"]
        def_loop = resultado["default_loop_idx"]
        dd_mic.value  = str(def_mic)  if def_mic  is not None and mics      else None
        dd_loop.value = str(def_loop) if def_loop is not None and loopbacks  else "none"

        dd_mic.disabled  = False
        dd_loop.disabled = False
        page.update()

    def _detectar_dispositivos_bg():
        try:
            resultado = listar_dispositivos()
            _popular_dropdowns(resultado)
        except Exception as ex:  # pylint: disable=broad-except
            dd_mic.hint_text  = f"Erro: {ex}"
            dd_loop.hint_text = ""
            page.update()

    threading.Thread(target=_detectar_dispositivos_bg, daemon=True).start()

    async def h_refresh(e):  # pylint: disable=unused-argument
        dd_mic.disabled  = True
        dd_loop.disabled = True
        dd_mic.hint_text  = "Detectando..."
        dd_loop.hint_text = "Detectando..."
        dd_mic.options   = []
        dd_loop.options  = []
        page.update()
        threading.Thread(target=_detectar_dispositivos_bg, daemon=True).start()

    btn_refresh.on_click = h_refresh

    async def atualizar_botoes_live():
        g = st_live["gravando"]
        btn_live_start.disabled = g
        btn_live_stop.disabled  = not g
        btn_refresh.disabled    = g
        dd_mic.disabled         = g
        dd_loop.disabled        = g
        btn_live_start.bgcolor  = colors.GREY_700 if g else colors.RED_900
        btn_live_stop.bgcolor   = colors.RED_700  if g else colors.GREY_800
        # Bloqueia navegação entre abas e controles do Arquivo durante gravação
        btn_pasta.disabled  = g
        hdr_arq.disabled    = g
        hdr_vivo.disabled   = g
        if g:
            btn_play.disabled   = True
            btn_play.icon_color = colors.GREY_400
            page.update()
        else:
            # Restaura estado correto da aba Arquivo (respeita se há arquivo selecionado)
            await atualizar_botoes()

    # Função auxiliar: transcreve um chunk de áudio já capturado
    async def _transcrever_chunk(
        model: WhisperModel,
        mic_raw:  bytes, loop_raw: bytes,
        devs: dict, n: int, f_out,
    ):
        wav_tmp = TEMP_DIR / f"live_{n}.wav"
        ok = await asyncio.to_thread(
            mix_e_salvar_wav,
            mic_raw,  devs["mic_rate"],  devs["mic_ch"],
            loop_raw if devs["loop_index"] is not None else b"",
            devs["loop_rate"], devs["loop_ch"],
            wav_tmp,
        )
        if not ok:
            adicionar_log(f"   ⏭️ Bloco {n} silencioso — ignorado.", colors.GREY_400)
            return
        segs, _ = await asyncio.to_thread(
            model.transcribe, str(wav_tmp), language="pt",
            beam_size=2, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        texto = "".join(s.text for s in segs).strip()
        if texto:
            f_out.write(textwrap.fill(texto, 70) + "\n\n")
            f_out.flush()
            adicionar_texto_ao_vivo(texto)
        wav_tmp.unlink(missing_ok=True)

    async def gravacao_ao_vivo():
        # Monta devs a partir da seleção dos dropdowns
        mic_key  = dd_mic.value
        loop_key = dd_loop.value

        if not mic_key:
            adicionar_log("❌ Selecione um microfone antes de iniciar.", colors.RED)
            st_live["gravando"] = False
            await atualizar_botoes_live()
            return

        mic_info  = next((m for m in st_live["_mics"]
                          if str(m["index"]) == mic_key), None)
        loop_info = next((lb for lb in st_live["_loops"]
                          if str(lb["index"]) == loop_key), None)

        if not mic_info:
            adicionar_log("❌ Microfone selecionado não encontrado.", colors.RED)
            st_live["gravando"] = False
            await atualizar_botoes_live()
            return

        devs = {
            "mic_index":  mic_info["index"],
            "mic_name":   mic_info["name"],
            "mic_rate":   mic_info["rate"],
            "mic_ch":     mic_info["ch"],
            "loop_index": loop_info["index"] if loop_info else None,
            "loop_name":  loop_info["name"]  if loop_info else "Nenhum",
            "loop_rate":  loop_info["rate"]  if loop_info else RATE_TARGET,
            "loop_ch":    loop_info["ch"]    if loop_info else 2,
        }
        st_live["devs"] = devs

        ts = datetime.now()
        st_live["t_inicio"]  = ts
        st_live["n_chunks"]  = 0
        nome_base            = f"reuniao_{ts.strftime('%Y%m%d_%H%M%S')}"
        st_live["txt_path"]  = OUTPUT_DIR / f"{nome_base}.txt"
        st_live["log_path"]  = LOGS_DIR   / f"log_{nome_base}.txt"
        st_live["frames_mic"].clear()
        st_live["frames_loop"].clear()
        while not st_live["chunk_q"].empty():
            st_live["chunk_q"].get_nowait()

        adicionar_log(f"🎙️ Gravação iniciada → {st_live['txt_path'].name}")
        adicionar_log(f"   Microfone : {devs['mic_name'][:55]}", colors.GREY_400)
        adicionar_log(f"   Saída de Som: {devs['loop_name'][:55]}", colors.GREY_400)
        adicionar_log(f"   Bloco     : {LIVE_CHUNK_S}s por transcrição",
                      colors.GREY_400)

        try:
            model = await get_model()
        except Exception as ex:  # pylint: disable=broad-except
            adicionar_log(f"❌ Falha ao carregar modelo: {ex}", colors.RED)
            st_live["gravando"] = False
            await atualizar_botoes_live()
            return

        pa = pyaudio.PyAudio()

        def cb_mic(in_data, _fc, _ti, _st):
            with st_live["frames_lock"]:
                st_live["frames_mic"].append(in_data)
            return (None, pyaudio.paContinue)

        def cb_loop(in_data, _fc, _ti, _st):
            with st_live["frames_lock"]:
                st_live["frames_loop"].append(in_data)
            return (None, pyaudio.paContinue)

        try:
            stream_mic = pa.open(
                format=PA_FORMAT, channels=devs["mic_ch"],
                rate=devs["mic_rate"], input=True,
                input_device_index=devs["mic_index"],
                frames_per_buffer=PA_CHUNK,
                stream_callback=cb_mic,
            )
        except Exception as ex:  # pylint: disable=broad-except
            adicionar_log(f"❌ Erro ao abrir microfone: {ex}", colors.RED)
            pa.terminate()
            st_live["gravando"] = False
            await atualizar_botoes_live()
            return

        stream_loop = None
        if devs["loop_index"] is not None:
            try:
                stream_loop = pa.open(
                    format=PA_FORMAT, channels=devs["loop_ch"],
                    rate=devs["loop_rate"], input=True,
                    input_device_index=devs["loop_index"],
                    frames_per_buffer=PA_CHUNK,
                    stream_callback=cb_loop,
                )
            except Exception as ex:  # pylint: disable=broad-except
                adicionar_log(
                    f"⚠️ Saída de Som indisponível ({ex}). Usando só microfone.",
                    colors.AMBER)
                stream_loop = None

        stream_mic.start_stream()
        if stream_loop:
            stream_loop.start_stream()

        lbl_live_status.value = "🔴 Gravando..."
        lbl_live_status.color = colors.RED
        page.update()

        # Thread coletora: snapshots a cada LIVE_CHUNK_S segundos
        def coletor():
            while not st_live["solicitou_parar"]:
                time.sleep(LIVE_CHUNK_S)
                with st_live["frames_lock"]:
                    mic_raw  = b"".join(st_live["frames_mic"])
                    loop_raw = b"".join(st_live["frames_loop"])
                    st_live["frames_mic"].clear()
                    st_live["frames_loop"].clear()
                if not st_live["solicitou_parar"]:
                    st_live["chunk_q"].put(("chunk", mic_raw, loop_raw))
            # Flush final: captura o que ficou no buffer
            with st_live["frames_lock"]:
                mic_raw  = b"".join(st_live["frames_mic"])
                loop_raw = b"".join(st_live["frames_loop"])
                st_live["frames_mic"].clear()
                st_live["frames_loop"].clear()
            st_live["chunk_q"].put(("fim", mic_raw, loop_raw))

        threading.Thread(target=coletor, daemon=True).start()

        # Loop de transcrição (consome a fila)
        n = 0
        with open(st_live["txt_path"], "w", encoding="utf-8") as f_out:
            while True:
                tipo, mic_raw, loop_raw = await asyncio.to_thread(
                    st_live["chunk_q"].get)

                if tipo == "fim":
                    # Transcreve o último fragmento antes de encerrar
                    if mic_raw or loop_raw:
                        n += 1
                        adicionar_log(f"📝 Transcrevendo bloco final ({n})...")
                        await _transcrever_chunk(
                            model, mic_raw, loop_raw, devs, n, f_out)
                    break

                n += 1
                adicionar_log(f"📝 Transcrevendo bloco {n}...")
                await _transcrever_chunk(model, mic_raw, loop_raw, devs, n, f_out)
                st_live["n_chunks"]  = n
                lbl_chunks.value     = f"Blocos transcritos: {n}"
                adicionar_log(f"✅ Bloco {n} concluído.")
                page.update()

        # Encerra streams
        stream_mic.stop_stream()
        stream_mic.close()
        if stream_loop:
            stream_loop.stop_stream()
            stream_loop.close()
        pa.terminate()

        # replace.txt
        rp = BASE_DIR / "replace.txt"
        if rp.exists():
            adicionar_log("🔄 Aplicando replace.txt...")
            regras = await asyncio.to_thread(carregar_regras, rp)
            mapa   = await asyncio.to_thread(construir_mapa, regras)
            st_live["txt_path"].write_text(
                aplicar_correcoes(
                    st_live["txt_path"].read_text(encoding="utf-8"), mapa),
                encoding="utf-8")
            adicionar_log("✅ Correções aplicadas.")

        t_total = (datetime.now() - ts).total_seconds()
        adicionar_log(
            f"🎉 Reunião encerrada! Duração: {t_total/60:.1f} min | "
            f"Blocos: {st_live['n_chunks']}", colors.CYAN)

        def fechar_modal_live(e):  # pylint: disable=unused-argument
            page.dialog.open = False
            page.update()

        page.dialog = ft.AlertDialog(
            title=Text("✅ Reunião Encerrada!"),
            content=Text(
                f"Arquivo: {st_live['txt_path'].name}\n"
                f"Local: {OUTPUT_DIR}\n\n"
                f"Duração total: {t_total/60:.1f} min\n"
                f"Blocos transcritos: {st_live['n_chunks']}"),
            actions=[ft.TextButton("OK", on_click=fechar_modal_live)],
        )
        page.dialog.open = True

        st_live["gravando"]     = False
        lbl_live_status.value   = "Gravação encerrada."
        lbl_live_status.color   = colors.GREY_400
        await atualizar_botoes_live()
        page.update()

    async def h_live_start(e):  # pylint: disable=unused-argument
        if st_live["gravando"]:
            return
        st_live["gravando"]      = True
        st_live["solicitou_parar"] = False
        console_ao_vivo.controls.clear()
        console_ao_vivo.controls.append(
            Text("🔴 Gravação iniciada. O texto transcrito aparecerá aqui.",
                 color=colors.GREY_400, size=11, italic=True))
        lbl_chunks.value = "Blocos transcritos: 0"
        await atualizar_botoes_live()
        page.run_task(gravacao_ao_vivo)

    async def h_live_stop(e):  # pylint: disable=unused-argument
        if not st_live["gravando"]:
            return
        st_live["solicitou_parar"] = True
        btn_live_start.disabled = btn_live_stop.disabled = True
        lbl_live_status.value   = "⏳ Encerrando — transcrevendo os últimos blocos..."
        lbl_live_status.color   = colors.AMBER
        page.update()
        adicionar_log("⏹ Encerramento solicitado — transcrevendo os últimos blocos...",
                      colors.AMBER)

    btn_live_start.on_click = h_live_start
    btn_live_stop.on_click  = h_live_stop

    # ==========================================================
    # LAYOUT — ABAS MANUAIS (show/hide; sem ft.Tabs)
    # Cada painel tem exatamente a altura dos seus controles,
    # sem interferência entre as abas.
    # ==========================================================

    btn_pasta = IconButton(
        icons.FOLDER_OPEN, icon_color=colors.BLUE,
        on_click=lambda _: picker.pick_files(
            allow_multiple=False,
            allowed_extensions=["mp4", "m4a", "mp3", "wav", "mov", "avi"]),
    )

    # Cabeçalhos das abas
    ind_arq  = Container(height=2, bgcolor=colors.BLUE_400)
    ind_vivo = Container(height=2, bgcolor=colors.TRANSPARENT)
    txt_arq  = Text("📁  Arquivo", size=13, color=colors.WHITE, weight="bold")
    txt_vivo = Text("🎙️  Ao Vivo", size=13, color=colors.GREY_500)

    hdr_arq = Container(
        content=Column([txt_arq, ind_arq], spacing=3,
                       horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.padding.only(left=10, right=10, top=6, bottom=0),
        ink=True,
    )
    hdr_vivo = Container(
        content=Column([txt_vivo, ind_vivo], spacing=3,
                       horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.padding.only(left=10, right=10, top=6, bottom=0),
        ink=True,
    )

    # Painéis de conteúdo — só um fica visível por vez
    painel_arquivo = Container(
        content=Column([
            Row([btn_pasta, Text("Arquivo:", weight="bold", size=12), arquivo_text]),
            Row([btn_play, btn_pause, btn_stop], alignment=MainAxisAlignment.CENTER),
            progress_bar,
            Row([status_esq, status_dir],
                alignment=MainAxisAlignment.SPACE_BETWEEN),
            status_geral,
            Container(
                content=console_arquivo,
                bgcolor=colors.BLACK,
                height=340,
                border_radius=8,
                padding=10,
            ),
        ], spacing=6),
        padding=ft.padding.only(top=10, bottom=4),
        visible=True,
    )
    painel_ao_vivo = Container(
        content=Column([
            dd_mic, dd_loop,
            Row([btn_live_start, btn_live_stop, btn_refresh], spacing=12),
            Container(
                content=Row([lbl_live_status, lbl_chunks], spacing=20),
                padding=ft.padding.only(top=6),
            ),
            Container(
                content=console_ao_vivo,
                bgcolor=colors.BLACK,
                height=300,
                border_radius=8,
                padding=10,
            ),
        ], spacing=8),
        padding=ft.padding.only(top=10, bottom=4),
        visible=False,
    )

    async def h_tab_arq(e):  # pylint: disable=unused-argument
        painel_arquivo.visible  = True
        painel_ao_vivo.visible  = False
        txt_arq.color  = colors.WHITE;    txt_arq.weight  = "bold"
        txt_vivo.color = colors.GREY_500; txt_vivo.weight = "normal"
        ind_arq.bgcolor  = colors.BLUE_400
        ind_vivo.bgcolor = colors.TRANSPARENT
        page.update()

    async def h_tab_vivo(e):  # pylint: disable=unused-argument
        painel_arquivo.visible  = False
        painel_ao_vivo.visible  = True
        txt_arq.color  = colors.GREY_500; txt_arq.weight  = "normal"
        txt_vivo.color = colors.WHITE;    txt_vivo.weight = "bold"
        ind_arq.bgcolor  = colors.TRANSPARENT
        ind_vivo.bgcolor = colors.BLUE_400
        page.update()

    hdr_arq.on_click  = h_tab_arq
    hdr_vivo.on_click = h_tab_vivo

    page.add(Container(
        content=Column([
            Text("Transcritor de Reuniões", size=24, font_family="Segoe UI Light"),
            Container(
                Text("Versão 7.0", size=10, color=colors.GREY_400),
                tooltip="Desenvolvido por\nJoão Arias & Claude\nSão Paulo / SP / Brasil"),
            Row([hdr_arq, hdr_vivo]),
            ft.Divider(height=1, color=colors.GREY_800),
            painel_arquivo,
            painel_ao_vivo,
        ], spacing=5),
        padding=10,
    ))

    await atualizar_botoes()


if __name__ == "__main__":
    configurar_ambiente_executavel()
    ft.app(target=main)
