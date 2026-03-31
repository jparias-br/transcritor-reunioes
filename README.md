# 🎙️ Transcritor de Reuniões v6.0 (Faster-Whisper)

O **Transcritor de Reuniões** é uma ferramenta de alta performance para conversão de áudio em texto, focada em privacidade total (100% offline) e precisão gramatical para o português do Brasil.

Esta versão 6.0 utiliza o motor **Faster-Whisper (CTranslate2)**, sendo até 4x mais rápida que as versões anteriores e otimizada para rodar em CPUs comuns com baixo consumo de memória RAM.

---

## ✨ Destaques da Versão 6.0
- **Motor:** Faster-Whisper (implementação CTranslate2 otimizada).
- **Modelo:** `Medium` (Equilíbrio ideal entre velocidade e precisão para Português).
- **Equalização e Normalização:** Pré-processamento via FFmpeg para equalizar volumes de diferentes participantes e melhorar a clareza da voz antes da transcrição.
- **Segmentação:** Processamento do áudio em blocos de 60 segundos, garantindo estabilidade de memória e fluidez no log.
- **Privacidade:** 100% Offline. Nenhum dado ou áudio sai da sua máquina.
- **Interface:** Controle total com botões de Play, Pause e Stop.

---

## 🛠️ Requisito Externo: FFMPEG

O transcritor depende da ferramenta **FFmpeg** para equalizar e segmentar as gravações. Por transparência e segurança, recomendamos que você baixe a versão oficial:

1. **Download:** [Gyan.dev - FFmpeg Shared Build](https://www.gyan.dev/ffmpeg/builds/ffmpeg-master-latest-win64-gpl-shared.zip)
2. **Instalação:** Extraia os arquivos em uma pasta de sua preferência (ex: `C:\ffmpeg`).
3. **Variável de Ambiente:** Adicione o caminho da pasta `bin` (ex: `C:\ffmpeg\bin`) à variável de ambiente `PATH` do seu Windows.

---

## 🏗️ Ambiente de Desenvolvimento

Para garantir a compatibilidade, utilize o **Python 3.11.9**. Siga os passos abaixo no PowerShell:

1. **Crie e Ative o Ambiente Virtual:**
```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
```

2. **Configure as Permissões (se necessário):**
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

3. **Instale as Dependências:**
```powershell
python -m pip install --upgrade pip
python -m pip install torch==2.1.2
python -m pip install faster-whisper
python -m pip install flet==0.22.0
python -m pip install pydub==0.25.1 numpy==1.24.3 tqdm==4.67.3 requests==2.32.5 regex==2024.1.15 pylint nuitka
```

---

## 🤖 Modelo de IA (Weights)

O motor Faster-Whisper requer os pesos do modelo convertidos para o formato CTranslate2.
1. Crie a pasta `.\modelos\medium` na raiz do projeto.
2. Baixe os arquivos do repositório [Faster-Whisper Medium (Hugging Face)](https://huggingface.co/guillaumekln/faster-whisper-medium/tree/main):
   - `config.json`
   - `model.bin`
   - `tokenizer.json`
   - `vocabulary.txt`

---

## 📦 Geração do Executável e Distribuição

### Compilação via Nuitka
Execute o comando abaixo no terminal com o ambiente virtual ativado:
```powershell
nuitka --standalone --company-name="João Arias" --product-name="Transcritor de Reuniões" --file-version="6.0.0" --product-version="6.0.0" --windows-console-mode=disable --windows-icon-from-ico=transcritor.ico --include-package-data=flet --output-dir=build transcritor_v6.py
```

### Estrutura do Ambiente de Execução
Para distribuir o app (ex: `C:\apps\transcritor`), organize assim:
```text
# transcritor/
# ├── bin/ (Conteúdo da pasta build/dist - aprox. 2.3GB)
# ├── bin/transcritor_v6.exe
# ├── logs/ (Criado em tempo de execução)
# ├── output/ (Criado em tempo de execução)
# └── replace.txt (Seu dicionário personalizado)
```

---

## 📝 O Dicionário (replace.txt)

A IA pode confundir termos técnicos devido a sotaques ou regionalismos. O arquivo `replace.txt` corrige automaticamente o texto final. 
**Exemplos de termos mapeados:**
- **Fortinet:** "Fortune Net", Forchimetchi, footnet, Fortunet, FATnet, forxinete, portinete, Forcingetor.
- **Hostname:** Hostingame, Hostineimi, "Hosting Nemi", Hostinemi, "Hostie Neymi", "Justin Emy".
- **Site:** Saiti, Saici, Sáici.
- **Cloud:** Claudio, Claudi.

---

## 🧠 Pós-Processamento: Resumo e Ata com IA

Utilize os prompts abaixo em uma IA (ChatGPT, Claude, Gemini) para transformar a transcrição bruta em uma ata executiva:

### PASSO 1: Resumo Técnico
> O anexo contém a transcrição de uma reunião sobre [ASSUNTO]. Atue como um consultor sênior em Telecom e migração de sistemas. Gere um resumo sucinto dos assuntos discutidos, descreva a conclusão e os próximos passos no formato de redação, eliminando redundâncias e traduzindo trechos estrangeiros para Português-BR.

### PASSO 2: Ata Estruturada
> Baseado no resumo anterior, gere uma ata em tópicos seguindo rigorosamente:
> - **Pontos Principais Discutidos:** Use tópicos e subtópicos para detalhar orientações e riscos.
> - **Entendimento Compartilhado:** Destaque o que todos concordam (não use o termo 'Decisões').
> - **Conclusão:** Síntese objetiva e pontos de atenção.
> - **Próximos Passos:** Tabela contendo Ação, Responsável e Previsão.

---

## ⚖️ Licença (MIT)

Copyright (c) 2024 João Arias

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

---
*"Reto é o passo do justo; reto é o seu caminho."*
