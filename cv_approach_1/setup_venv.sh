#!/usr/bin/env bash
# Creates ./.venv with everything the pipeline needs, without sudo:
#   1. the Python packages from requirements.txt
#   2. a private copy of the Tesseract OCR program (+ English data) unpacked into .venv/tesseract,
#      with a launcher at .venv/bin/tesseract (so it is on PATH whenever the venv is active)
# Ubuntu/Debian only (uses apt-get download + dpkg -x, which need no root rights).
# Delete .venv and run again to start from scratch.
set -euo pipefail
cd "$(dirname "$0")"
VENV=.venv

echo "== 1/4 creating $VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip -q

echo "== 2/4 installing Python packages (requirements.txt)"
"$VENV/bin/pip" install -r requirements.txt -q

echo "== 3/4 downloading Tesseract into $VENV/tesseract (no sudo)"
T="$VENV/tesseract"; DEB="$(mktemp -d)"
rm -rf "$T"; mkdir -p "$T"
CORE="tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd"
# the tesseract + leptonica libraries are always taken into the venv ...
LIBS=$(apt-cache depends tesseract-ocr | awk '/Depends:/{print $2}' | grep -E '^lib(tesseract|lept)' || true)
# ... plus any other dependency that is not already installed on this machine
ALL=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks \
        --no-replaces --no-enhances $CORE | grep -E '^[a-z0-9]' | sort -u)
NEED="$CORE $LIBS"
for p in $ALL; do case "$p" in fonts-*) continue;; esac; dpkg -s "$p" >/dev/null 2>&1 || NEED="$NEED $p"; done
NEED=$(echo $NEED | tr ' ' '\n' | sort -u | tr '\n' ' ')
echo "   packages: $NEED"
(cd "$DEB" && apt-get download $NEED >/dev/null)
for f in "$DEB"/*.deb; do dpkg -x "$f" "$T"; done
rm -rf "$DEB"

cat > "$VENV/bin/tesseract" <<'EOF'
#!/usr/bin/env bash
# launcher for the Tesseract copy inside this venv
T="$(cd "$(dirname "${BASH_SOURCE[0]}")/../tesseract" && pwd)"
LIBDIRS=$(find "$T/usr/lib" "$T/lib" -maxdepth 1 -type d -name '*-linux-gnu' 2>/dev/null | tr '\n' ':')
export LD_LIBRARY_PATH="${LIBDIRS}${LD_LIBRARY_PATH:-}"
export TESSDATA_PREFIX="$(find "$T/usr/share" -type d -name tessdata | head -1)"
exec "$T/usr/bin/tesseract" "$@"
EOF
chmod +x "$VENV/bin/tesseract"

echo "== 4/4 checking"
"$VENV/bin/tesseract" --version 2>&1 | head -1
echo "   languages: $("$VENV/bin/tesseract" --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' ')"
"$VENV/bin/python" -c "import lithology_pipeline as lp, pytesseract; lp.check_ocr('automatic_lithology_cv'); print('   pipeline uses:', pytesseract.pytesseract.tesseract_cmd)"
echo "done. Activate with:  source $VENV/bin/activate"
