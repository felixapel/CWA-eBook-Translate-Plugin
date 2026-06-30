#!/bin/bash

echo "=========================================================="
echo "   📖 Calibre-Web-Automated Book Translator Installer"
echo "=========================================================="
echo ""

# 1. Ask for CWA appdata path
read -p "Enter your Calibre-Web-Automated appdata path [default: /mnt/user/appdata/calibre-web-automated]: " CWA_PATH
CWA_PATH=${CWA_PATH:-/mnt/user/appdata/calibre-web-automated}

if [ ! -d "$CWA_PATH" ]; then
    echo "❌ Error: Directory $CWA_PATH does not exist."
    exit 1
fi

echo "✅ Using CWA path: $CWA_PATH"
mkdir -p "$CWA_PATH/overlay"

# 2. Download overlay files
echo "📥 Downloading frontend plugin files..."
REPO_URL="https://raw.githubusercontent.com/username/CWA-translate-plugin/main" # User must replace this URL or we publish it somewhere
curl -sL "$REPO_URL/overlay/read.html" -o "$CWA_PATH/overlay/read.html"
curl -sL "$REPO_URL/static/translator.js" -o "$CWA_PATH/overlay/translator.js"
curl -sL "$REPO_URL/static/translator.css" -o "$CWA_PATH/overlay/translator.css"

# 3. Download the XML Template for the API container
echo "📥 Installing Unraid Docker Template for Translator API..."
TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"
mkdir -p "$TEMPLATE_DIR"

cat << 'EOF' > "$TEMPLATE_DIR/my-book-translator-api.xml"
<?xml version="1.0"?>
<Container version="2">
  <Name>book-translator-api</Name>
  <Repository>ghcr.io/username/book-translator-api:latest</Repository>
  <Network>bridge</Network>
  <Shell>sh</Shell>
  <Privileged>false</Privileged>
  <Overview>Backend API for Calibre-Web-Automated Book Translator plugin.</Overview>
  <Category>Tools:Utilities</Category>
  <Icon>https://raw.githubusercontent.com/JPDVM2014/icons/35eb799864f41502e741075abfc6457ba0edefd6/calibre-web-logo.png</Icon>
  <Config Name="Appdata (Database)" Target="/app/data" Default="/mnt/user/appdata/book-translator-api/data" Mode="rw" Description="Holds translations.db SQLite database" Type="Path" Display="always" Required="true" Mask="false">/mnt/user/appdata/book-translator-api/data</Config>
  <Config Name="Port" Target="8390" Default="8390" Mode="tcp" Description="API Port" Type="Port" Display="always" Required="true" Mask="false">8390</Config>
  <Config Name="LLM Provider" Target="LLM_PROVIDER" Default="local" Mode="" Description="Provider (e.g. local, openai, anthropic, gemini, groq)" Type="Variable" Display="always" Required="false" Mask="false">local</Config>
  <Config Name="LLM Model" Target="LLM_MODEL" Default="gemma4-12b" Mode="" Description="Model name" Type="Variable" Display="always" Required="false" Mask="false">gemma4-12b</Config>
  <Config Name="LLM API Key" Target="LLM_API_KEY" Default="" Mode="" Description="API Key" Type="Variable" Display="always" Required="false" Mask="true"></Config>
</Container>
EOF

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
