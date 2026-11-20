#!/bin/bash
# LiveFrame ECS · monta o LiveFrame.app e o LiveFrame.dmg neste Mac (duplo clique no Finder).
#
# Não usa GitHub Actions nem nada pago. Na primeira vez baixa um Python próprio (~50 MB) e as
# dependências para uma pasta de cache; nas próximas leva 1–2 minutos.
# No fim deixa o LiveFrame.dmg na Mesa e abre a janela "arraste para Aplicativos".
#
#   Cache:    ~/Library/Caches/LiveFrame-build   (pode apagar; é refeito na próxima vez)
#   Saída:    ~/Desktop/LiveFrame.dmg            (LIVEFRAME_DMG_OUT=/outra/pasta para mudar)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="${LIVEFRAME_BUILD_CACHE:-$HOME/Library/Caches/LiveFrame-build}"
OUT="${LIVEFRAME_DMG_OUT:-$HOME/Desktop}"
UV_VERSION="0.13.9"
PY_VERSION="3.12"

say()  { printf '\n\033[1;33m▸ %s\033[0m\n' "$*"; }
fail() {
  printf '\n\033[1;31m✗ %s\033[0m\n' "$*"
  printf 'O registro completo está em: %s\n' "$LOG"
  echo "Pode fechar esta janela."
  exit 1
}

mkdir -p "$CACHE"
LOG="$CACHE/build.log"
: > "$LOG"
trap 'fail "Parou no passo acima."' ERR

[[ "$(uname -s)" == Darwin ]] || fail "Este script roda só no Mac."
cd "$REPO"
VERSION=$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' liveframe/__init__.py)
echo "LiveFrame ECS $VERSION · montando neste Mac ($(uname -m))"

# 1) Python com Tk (a janela do app precisa dele) -------------------------------------
py_ok() {   # Python ≥ 3.10 com Tcl/Tk 8.6 funcionando
  "$1" - <<'EOF' >/dev/null 2>&1
import sys, tkinter
assert sys.version_info >= (3, 10)
assert float(tkinter.Tcl().eval("info tclversion")) >= 8.6
EOF
}

say "1/4 · Python"
PY=""
for c in /Library/Frameworks/Python.framework/Versions/3.1[0-3]/bin/python3; do   # python.org
  if [[ -x "$c" ]] && py_ok "$c"; then PY="$c"; fi
done
if [[ -z "$PY" ]]; then
  # Python próprio do build (uv + python-build-standalone): não instala nada no sistema
  UV="$CACHE/uv/uv"
  if [[ ! -x "$UV" ]] || [[ "$("$UV" --version 2>/dev/null)" != "uv $UV_VERSION"* ]]; then
    case "$(uname -m)" in arm64) T=aarch64-apple-darwin ;; *) T=x86_64-apple-darwin ;; esac
    echo "baixando o uv $UV_VERSION…"
    rm -rf "$CACHE/uv" && mkdir -p "$CACHE/uv"
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$T.tar.gz" \
      | tar -xz -C "$CACHE/uv" --strip-components 1
  fi
  export UV_PYTHON_INSTALL_DIR="$CACHE/python" UV_CACHE_DIR="$CACHE/uv-cache"
  echo "baixando o Python $PY_VERSION (só na primeira vez)…"
  "$UV" python install "$PY_VERSION" >>"$LOG" 2>&1
  PY="$("$UV" python find --managed-python "$PY_VERSION")"
  py_ok "$PY" || fail "O Python baixado não tem Tk."
fi
echo "usando $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])'))"

# 2) Ambiente de build ----------------------------------------------------------------
say "2/4 · Dependências"
VENV="$CACHE/venv"
if [[ "$(cat "$VENV/.liveframe-python" 2>/dev/null)" != "$PY" ]] || ! "$VENV/bin/python" -c 'import sys' >/dev/null 2>&1; then
  rm -rf "$VENV"
  "$PY" -m venv "$VENV" >>"$LOG" 2>&1
  echo "$PY" > "$VENV/.liveframe-python"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip >>"$LOG" 2>&1
"$VENV/bin/python" -m pip install --quiet -r requirements.txt -r packaging/requirements-build.txt >>"$LOG" 2>&1
echo "ok"

# 3) LiveFrame.app --------------------------------------------------------------------
say "3/4 · LiveFrame.app (2–5 min)"
B="$CACHE/build"
rm -rf "$B/dist" "$B/pyi"
"$VENV/bin/python" packaging/make_art.py "$B/art" >>"$LOG" 2>&1
LIVEFRAME_ART="$B/art" "$VENV/bin/pyinstaller" --noconfirm --clean --distpath "$B/dist" --workpath "$B/pyi" \
  packaging/LiveFrame.spec >>"$LOG" 2>&1
APP="$B/dist/LiveFrame.app"
codesign --force --deep --sign - "$APP" >>"$LOG" 2>&1          # assinatura ad-hoc (Apple Silicon exige)
echo "autoteste do app (foto, FTP, recap, janela)…"
env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin "$APP/Contents/MacOS/LiveFrame" --selftest >>"$LOG" 2>&1 \
  || fail "O autoteste do app falhou."
echo "ok ($(du -sh "$APP" | cut -f1))"

# 4) LiveFrame.dmg --------------------------------------------------------------------
say "4/4 · LiveFrame.dmg"
DMG="$B/dist/LiveFrame.dmg"
"$VENV/bin/dmgbuild" -s packaging/dmg_settings.py -D app="$APP" -D art="$B/art" "LiveFrame $VERSION" "$DMG" \
  >>"$LOG" 2>&1
hdiutil verify "$DMG" >>"$LOG" 2>&1
mkdir -p "$OUT"
for v in /Volumes/LiveFrame*; do [[ -d "$v" ]] && hdiutil detach -quiet "$v" || true; done
cp -f "$DMG" "$OUT/LiveFrame.dmg"
trap - ERR

printf '\n\033[1;32m✓ Pronto: %s (LiveFrame %s)\033[0m\n' "$OUT/LiveFrame.dmg" "$VERSION"
echo "Abrindo o .dmg: arraste o LiveFrame para Aplicativos (substitua se pedir). Seus eventos e ajustes ficam."
open "$OUT/LiveFrame.dmg"
echo "Pode fechar esta janela."
