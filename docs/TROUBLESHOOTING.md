# Troubleshooting Guide

Here are solutions for common issues when running the book-translator plugin.

## 1. Button is visible, but translation does not happen

- **Check API health**: Run `curl -s http://<unraid-ip>:8390/health` to verify if the translator API is running.
- **Check Backend configuration**: Verify your `BT_LOCAL_URL` env variable.
  - If your local vLLM / Ollama server is on the same machine but outside Docker, `localhost` inside the container refers to the container itself. Use the host's LAN IP (e.g., `http://192.168.0.122:2819/v1/chat/completions`).
- **Check Browser Console**: Open developer tools (F12) and check if there are network request failures to `/translate/batch`.

## 2. Spinner/Progress indicator is missing

- **Clear Browser Cache**: The browser might be caching an older version of `translator.js`. Perform a hard refresh (`Ctrl + F12` or `Cmd + Shift + R`).
- **Check stylesheet**: Make sure `translator.css` is correctly loaded and mapped.

## 3. Chapter change does not translate automatically

- Verify that you are running the latest version of `translator.js` (look for `[BookTranslator] loaded version ...` in the browser console).
- The page-turn and chapter change observers rely on epub.js events and document iframe changes. If CWA has been updated and the structure changed, verify that the iframe can be detected.

## 4. Wrong BT_LOCAL_URL config

- **vLLM**: `BT_LOCAL_URL=http://<host>:2819/v1/chat/completions`
- **Ollama**: `BT_LOCAL_URL=http://<host>:11434/v1/chat/completions`
- **LM Studio**: `BT_LOCAL_URL=http://<host>:1234/v1/chat/completions`
