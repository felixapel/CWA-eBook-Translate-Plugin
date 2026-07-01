#!/bin/bash
# Run this from inside a clone of the repo (it copies the overlay files that
# live alongside this script — it does not download anything from the network).
set -e

echo "=========================================================="
echo "   📖 Calibre-Web-Automated Book Translator Installer"
echo "=========================================================="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in "$SCRIPT_DIR/overlay/read.html" "$SCRIPT_DIR/static/translator.js" "$SCRIPT_DIR/static/translator.css" "$SCRIPT_DIR/my-book-translator-api.xml"; do
    if [ ! -f "$f" ]; then
        echo "❌ Error: $f not found. Run this script from inside a clone of the repo"
        echo "   (it copies the overlay + template files that live next to install_unraid.sh)."
        exit 1
    fi
done

# 1. Ask for CWA appdata path
read -p "Enter your Calibre-Web-Automated appdata path [default: /mnt/user/appdata/calibre-web-automated]: " CWA_PATH
CWA_PATH=${CWA_PATH:-/mnt/user/appdata/calibre-web-automated}

if [ ! -d "$CWA_PATH" ]; then
    echo "❌ Error: Directory $CWA_PATH does not exist."
    exit 1
fi

echo "✅ Using CWA path: $CWA_PATH"
mkdir -p "$CWA_PATH/overlay"

# 2. Copy overlay files from this checkout
echo "📥 Copying frontend plugin files..."
cp "$SCRIPT_DIR/overlay/read.html" "$CWA_PATH/overlay/read.html"
cp "$SCRIPT_DIR/static/translator.js" "$CWA_PATH/overlay/translator.js"
cp "$SCRIPT_DIR/static/translator.css" "$CWA_PATH/overlay/translator.css"

# 3. Build the backend image locally (no published registry image — see README)
echo "🔨 Building the book-translator-api image locally..."
if command -v docker >/dev/null 2>&1; then
    (cd "$SCRIPT_DIR" && docker build -t local/book-translator-api:latest .)
else
    echo "⚠️  docker not found on this shell; build it yourself:"
    echo "    cd $SCRIPT_DIR && docker build -t local/book-translator-api:latest ."
fi

# 4. Install the Unraid Docker Template for the API container (kept in sync as
#    the single source of truth in my-book-translator-api.xml — no inline copy).
echo "📥 Installing Unraid Docker Template for Translator API..."
TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"
mkdir -p "$TEMPLATE_DIR"
cp "$SCRIPT_DIR/my-book-translator-api.xml" "$TEMPLATE_DIR/my-book-translator-api.xml"

echo "=========================================================="
echo "🎉 Installation almost complete!"
echo "Next steps:"
echo "1. Go to your Unraid Docker tab."
echo "2. Edit your 'calibre-web-automated' container and add 3 new Paths:"
echo "   - Container Path: /app/calibre-web-automated/cps/templates/read.html | Host Path: $CWA_PATH/overlay/read.html"
echo "   - Container Path: /app/calibre-web-automated/cps/static/js/translator.js | Host Path: $CWA_PATH/overlay/translator.js"
echo "   - Container Path: /app/calibre-web-automated/cps/static/css/translator.css | Host Path: $CWA_PATH/overlay/translator.css"
echo "3. Click 'Add Container' at the bottom of the Docker page."
echo "4. Select 'book-translator-api' from the 'Template' dropdown to install the backend API."
echo "=========================================================="
