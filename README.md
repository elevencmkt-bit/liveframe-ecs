# LiveFrame ECS

## V2 · app no Mac (padrão)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m liveframe app        # abre http://127.0.0.1:8711
```

1. **Uma vez:** colocar LUTs (.cube), PNGs de assinatura e trilhas licenciadas em `~/LiveFrame/assets`. Em Settings → *Show camera FTP settings*, copiar servidor/porta/usuário/senha para a A7 IV (FTP Transfer, modo passivo, envio automático). A senha é gerada pelo app e fica no Keychain.
2. **No Hub:** criar a galeria do cliente (Galleries), publicar, e em *LiveFrame* clicar *Create LiveFrame key* (mostrada uma vez).
3. **No app:** *New event* → nome, cliente, LUT, assinatura, tamanho, WhatsApp do cliente e a chave da galeria (vai para o Keychain, nunca para arquivo). Eventos anteriores servem de preset.
4. **▶ Play:** a câmera loga no FTP → "Camera connected". Cada foto chega tratada em *To review*. Aprovar/descartar com clique ou teclado (`A` / `R`, `←` `→`). Aprovadas vão para a galeria do Hub; sem internet ficam na fila e saem quando ela volta (sem duplicar: o Hub ignora nome repetido).
5. **Send gallery link** no início, **Notify client** (+N, link wa.me preenchido; o Levi toca em enviar; o botão acende quando passa 1 h com fotos novas), **Build recap** com a trilha escolhida, **Close event** no fim (a fila ainda esvazia).

- Um evento ao vivo por vez. Pausado: as fotos esperam em `~/LiveFrame/inbox`.
- Cabo (fallback): Settings → *Tether folder* (pasta onde o Imaging Edge salva).
- Drive (fallback da V1): no evento, Gallery = *Drive folder*.
- A tela só responde em 127.0.0.1; o FTP escuta na rede do travel router (porta 2121, passivas 60000–60099).

---

# LiveFrame ECS · V1 (motor / linha de comando)

Sistema por trás da Booth Session: **câmera → tratamento automático (LUT + assinatura do evento) → galeria ao vivo (Google Drive) → aviso de hora em hora no WhatsApp → reel de recap**.

> Nada vai a cliente real antes do Levi aprovar o piloto. Sem senhas ou tokens no repo: o acesso ao Drive fica no Google Drive for desktop ou no `rclone config` do Mac.

## Fluxo

```
câmera ──FTP (Wi-Fi, travel router 5 GHz)──▶ <evento>/incoming/
   JPEG  → LUT + tamanho + assinatura → review/ ──Levi aprova──▶ approved/ → ready/ → publica no Drive → sent/
                                          └──Levi descarta──▶ rejected/ (nunca publica)
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

### Curadoria (verificação antes do envio)
Padrão: `curation = "review"` (decisão do Levi, 19/11/2026). Toda foto chega já tratada em `<evento>/review/` e **nada vai para a galeria sem o ok do Levi**:

- **Aprovar:** arrastar no Finder de `review/` para `approved/`. O loop publica em segundos (→ `ready/` → Drive → `sent/`).
- **Descartar:** arrastar para `rejected/`. Nunca é publicada. Se mudar de ideia, arrastar de `rejected/` para `approved/`.
- **Pelo terminal:** `.venv/bin/python -m liveframe approve events/minha-feira.toml` aprova tudo que está em `review/`; com nomes de arquivo, só esses.
- `status` mostra quantas estão esperando em `review`.
- Dica: deixar a pasta `review/` aberta no Finder em modo galeria (ou no Preview) ao lado da câmera.

Outros modos: `"all"` (publica tudo direto) e `"flagged"` (só fotos com nota na câmera ≥ `min_rating`, lida do EXIF Rating ou do XMP; validar no piloto se a câmera grava a nota no JPEG enviado).

### Aviso de hora em hora
```bash
.venv/bin/python -m liveframe notify events/minha-feira.toml --open
```
Conta as fotos que foram para `sent/` (ou seja, já aprovadas e publicadas) desde o último aviso. Se N = 0, não faz nada. Se N > 0, monta a mensagem em inglês (*"📸 +N new photos from {event} are live in your gallery: {link}"*), mostra notificação no Mac e, com `--open`, abre o WhatsApp com o texto já preenchido para o Levi tocar em enviar. **Nada é enviado sozinho** (API oficial é V3; envio manual por wa.me confirmado pelo Levi para a V1). Agendar com `launchd/com.ecs.liveframe.notify.plist` (instruções no arquivo). Log em `<evento>/notices.log`.

### Recap reel (30–60 s, 9:16)
```bash
.venv/bin/python -m liveframe recap events/minha-feira.toml --music trilha.m4a --bpm 118 --duration 45
```
Pega os clipes de `video/`, corta no ritmo (padrão 4 batidas por corte), aplica o **mesmo .cube** das fotos, enquadra 1080×1920, põe a assinatura (`signature_portrait`, senão `signature`) e a trilha com fade. Sai `recap/<evento>_recap.mp4` + `.json` com qual trecho de qual clipe entrou. `--music-start` escolhe o ponto da música; `--dry-run` mostra o plano sem renderizar. O Levi aprova antes de qualquer envio.

## Testar sem câmera

```bash
.venv/bin/python -m liveframe samples samples/        # LUT, PNG de moldura/logo e trilha de exemplo
.venv/bin/python -m unittest discover -s tests -v     # 33 testes: V1 + app V2 (eventos, FTP, revisão, galeria do Hub, fila offline)
```

## Pendências para o piloto
- LUT (.cube) aprovado e PNG de assinatura reais (hoje: amostras geradas por código).
- Trilha licenciada escolhida pelo Levi.
- Câmera com FTP automático (A7 IV / A7C II); a A7 III só manda em lote.

V2: painel local (criar evento, play/pause, status) e galeria com marca no Hub. V3: WhatsApp Business API oficial, segundo operador.
