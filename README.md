# LiveFrame ECS · V1 (piloto em casa)

Sistema por trás da Booth Session: **câmera → tratamento automático (LUT + assinatura do evento) → galeria ao vivo (Google Drive) → aviso de hora em hora no WhatsApp → reel de recap**.

> Nada vai a cliente real antes do Levi aprovar o piloto. Sem senhas ou tokens no repo: o acesso ao Drive fica no Google Drive for desktop ou no `rclone config` do Mac.

## Fluxo

```
câmera ──FTP (Wi-Fi, travel router 5 GHz)──▶ <evento>/incoming/
   JPEG  → curadoria → LUT + tamanho + assinatura → ready/ → publica no Drive → sent/
   vídeo → video/ (bruto, para o recap)
   RAW   → skipped/ (o RAW fica no cartão como backup)
   original JPEG → originals/   ·   arquivo corrompido → failed/
```

- **Idempotente / sobrevive a restart:** cada arquivo é identificado pelo SHA-1 do conteúdo num ledger SQLite (`state.sqlite`). Reenvio da câmera ou restart no meio não duplica nada na galeria.
- **Fila offline:** se a internet cai, o arquivo fica em `ready/` e é reenviado com backoff (5 s → 5 min).
- **Arquivo chegando:** só é processado depois de ficar parado `--settle` segundos (padrão 2 s), para não pegar JPEG pela metade do FTP.
- **Nomes:** `<evento>_<nome da câmera>.jpg`; se dois cartões repetirem o número, ganha um sufixo.

## Instalar (Mac)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install ffmpeg          # recap reel
brew install rclone          # só se usar o backend rclone
```

Servidor FTP no Mac (fora deste repo): qualquer servidor FTP local apontando para `<evento>/incoming/` (ex.: o pacote `pyftpdlib`, ou um app de FTP). Usuário/senha do FTP ficam na câmera e no servidor, nunca aqui.

## Usar num evento

```bash
cp events/example.toml events/minha-feira.toml      # editar nome, LUT, PNG, pasta do Drive, link, WhatsApp
.venv/bin/python -m liveframe run    events/minha-feira.toml   # play (Ctrl+C = pausa; rodar de novo = continua)
.venv/bin/python -m liveframe status events/minha-feira.toml
```

### Galeria (V1 = pasta compartilhada do Google Drive)
- `backend = "folder"`: copia para a pasta sincronizada pelo **Google Drive for desktop** (ex.: `~/Library/CloudStorage/GoogleDrive-…/My Drive/LiveFrame/<evento>`). Mais simples; o Drive cuida do upload e tem fila offline própria.
- `backend = "rclone"`: `rclone copyto` para um remoto configurado uma vez (`rclone config`). Útil se não quiser o Drive for desktop.

### Curadoria
`curation = "all"` (padrão, até o Levi decidir) ou `"flagged"` (só fotos com nota na câmera ≥ `min_rating`). A nota é lida do EXIF Rating ou do XMP. **Validar no piloto** se a câmera grava a nota no JPEG enviado; na Sony também dá para filtrar na própria câmera (transferir só protegidas/avaliadas).

### Aviso de hora em hora
```bash
.venv/bin/python -m liveframe notify events/minha-feira.toml --open
```
Conta as fotos que foram para `sent/` desde o último aviso. Se N = 0, não faz nada. Se N > 0, monta a mensagem em inglês (*"📸 +N new photos from {event} are live in your gallery: {link}"*), mostra notificação no Mac e, com `--open`, abre o WhatsApp com o texto já preenchido para o Levi tocar em enviar. **Nada é enviado sozinho** (API oficial é V3). Agendar com `launchd/com.ecs.liveframe.notify.plist` (instruções no arquivo). Log em `<evento>/notices.log`.

### Recap reel (30–60 s, 9:16)
```bash
.venv/bin/python -m liveframe recap events/minha-feira.toml --music trilha.m4a --bpm 118 --duration 45
```
Pega os clipes de `video/`, corta no ritmo (padrão 4 batidas por corte), aplica o **mesmo .cube** das fotos, enquadra 1080×1920, põe a assinatura (`signature_portrait`, senão `signature`) e a trilha com fade. Sai `recap/<evento>_recap.mp4` + `.json` com qual trecho de qual clipe entrou. `--music-start` escolhe o ponto da música; `--dry-run` mostra o plano sem renderizar. O Levi aprova antes de qualquer envio.

## Testar sem câmera

```bash
.venv/bin/python -m liveframe samples samples/        # LUT, PNG de moldura/logo e trilha de exemplo
.venv/bin/python -m unittest discover -s tests -v     # 18 testes: LUT, pipeline, fila offline, curadoria, aviso, recap
```

## Pendências para o piloto
- LUT (.cube) aprovado e PNG de assinatura reais (hoje: amostras geradas por código).
- Trilha licenciada escolhida pelo Levi.
- Modo de curadoria: "all" ou "flagged".
- Ok do envio manual por wa.me na V1.
- Câmera com FTP automático (A7 IV / A7C II); a A7 III só manda em lote.

V2: painel local (criar evento, play/pause, status) e galeria com marca no Hub. V3: WhatsApp Business API oficial, segundo operador.
