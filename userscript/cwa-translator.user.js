// ==UserScript==
// @name         CWA Book Translator
// @namespace    http://felix.homelab/
// @version      2.0
// @description  Traducción bilingüe en Calibre-Web con vLLM gemma4-12b
// @author       Felix
// @match        http://192.168.0.*:8383/read/*
// @match        http://192.168.0.*:8083/read/*
// @match        https://*.felitounraid.de/read/*
// @grant        GM_xmlhttpRequest
// @grant        GM_addStyle
// @run-at       document-end
// ==/UserScript==

(function() {
    'use strict';

    const TRANSLATOR_URL = 'http://192.168.0.180:8390';
    const SOURCE_LANG = 'English';
    let targetLang = 'Spanish';
    let translationMode = 'off'; // off | bilingual | translated
    let translatedParagraphs = {};

    // === CONTENT-BASED HASHING (H3) ===
    // Use content hash instead of positional indexing so translations
    // survive DOM reflows and page navigations.
    function hashText(text) {
        let hash = 0;
        for (let i = 0; i < text.length; i++) {
            const char = text.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash |= 0;
        }
        return 'h' + Math.abs(hash).toString(36);
    }

    // === STYLES ===
    GM_addStyle(`
        .bt-translation {
            color: #1565c0 !important;
            font-style: italic;
            padding: 4px 0 12px 16px;
            margin-bottom: 8px;
            border-left: 3px solid #90caf9;
            font-size: 0.95em;
            line-height: 1.7;
            opacity: 0;
            animation: bt-slide-in 0.3s ease forwards;
        }
        .bt-loading {
            animation: bt-pulse 1.5s infinite;
            color: #90a4ae;
            font-style: italic;
            padding: 4px 0 4px 16px;
            font-size: 0.9em;
        }
        @keyframes bt-pulse {
            0%, 100% { opacity: 0.6; }
            50% { opacity: 1; }
        }
        @keyframes bt-slide-in {
            from { opacity: 0; transform: translateY(-8px); }
            to   { opacity: 0.92; transform: translateY(0); }
        }
        #bt-toolbar {
            position: fixed; bottom: 20px; right: 20px; z-index: 99999;
            background: rgba(26, 115, 232, 0.85);
            backdrop-filter: blur(12px) saturate(180%);
            -webkit-backdrop-filter: blur(12px) saturate(180%);
            color: white; padding: 12px 20px;
            border-radius: 28px; cursor: pointer; font-size: 16px;
            font-family: sans-serif;
            box-shadow: 0 8px 32px rgba(26, 115, 232, 0.35), 0 2px 8px rgba(0,0,0,0.15);
            border: 1px solid rgba(255,255,255,0.18);
            user-select: none; transition: transform 0.15s, box-shadow 0.25s;
        }
        #bt-toolbar:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 40px rgba(26, 115, 232, 0.45), 0 4px 12px rgba(0,0,0,0.2);
        }
        #bt-toolbar:active { transform: scale(0.95); }
        #bt-toolbar.bt-active { background: rgba(13, 71, 161, 0.9); }
        #bt-toolbar select {
            margin-left: 8px; padding: 4px 8px; border-radius: 12px;
            border: 1px solid rgba(255,255,255,0.3); background: rgba(255,255,255,0.15);
            color: white; font-size: 14px;
        }
        #bt-toast {
            position: fixed; bottom: 80px; right: 24px; z-index: 100000;
            background: rgba(30, 30, 30, 0.9); backdrop-filter: blur(8px);
            color: #fff; padding: 12px 24px; border-radius: 12px;
            font-size: 14px; font-family: sans-serif; font-weight: 500;
            box-shadow: 0 4px 20px rgba(0,0,0,0.25);
            opacity: 0; transform: translateY(16px) scale(0.95);
            transition: opacity 0.3s ease, transform 0.3s ease;
            pointer-events: none;
        }
        #bt-toast.bt-toast-visible {
            opacity: 1; transform: translateY(0) scale(1);
        }
        @media (prefers-color-scheme: dark) {
            .bt-translation { color: #64b5f6 !important; border-left-color: #1565c0; }
        }
    `);

    // === TOAST NOTIFICATION ===
    function showToast(message) {
        const existing = document.getElementById('bt-toast');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.id = 'bt-toast';
        toast.textContent = message;
        document.body.appendChild(toast);

        requestAnimationFrame(() => toast.classList.add('bt-toast-visible'));

        setTimeout(() => {
            toast.classList.remove('bt-toast-visible');
            setTimeout(() => toast.remove(), 400);
        }, 2500);
    }

    // === LOADING INDICATORS (M7) ===
    function addLoadingIndicator(el) {
        const next = el.nextElementSibling;
        if (next && (next.classList.contains('bt-loading') || next.classList.contains('bt-translation'))) {
            return null;
        }
        const loader = document.createElement('div');
        loader.className = 'bt-loading';
        loader.textContent = 'Traduciendo...';
        el.parentNode.insertBefore(loader, el.nextSibling);
        return loader;
    }

    function removeLoadingIndicator(el) {
        const next = el.nextElementSibling;
        if (next && next.classList.contains('bt-loading')) {
            next.remove();
        }
    }

    // === TOOLBAR ===
    function createToolbar() {
        const div = document.createElement('div');
        div.id = 'bt-toolbar';
        div.innerHTML = '🌐 <span id="bt-label">Traducir</span> ' +
            '<select id="bt-lang">' +
            '<option value="Spanish">🇪🇸</option>' +
            '<option value="English">🇬🇧</option>' +
            '<option value="German">🇩🇪</option>' +
            '<option value="French">🇫🇷</option>' +
            '<option value="Italian">🇮🇹</option>' +
            '</select>';
        div.onclick = (e) => {
            if (e.target.tagName === 'SELECT') return;
            cycleMode();
        };
        document.body.appendChild(div);

        document.getElementById('bt-lang').onchange = function() {
            targetLang = this.value;
            translatedParagraphs = {};
            removeTranslations();
            if (translationMode !== 'off') translatePage();
        };
    }

    function cycleMode() {
        switch(translationMode) {
            case 'off':
                translationMode = 'bilingual';
                document.getElementById('bt-toolbar').classList.add('bt-active');
                document.getElementById('bt-label').textContent = 'Bilingüe';
                showToast('📖 Modo bilingüe activado');
                translatePage();
                break;
            case 'bilingual':
                translationMode = 'translated';
                document.getElementById('bt-label').textContent = 'Traducido';
                showToast('📝 Modo solo traducción');
                showTranslatedOnly();
                break;
            case 'translated':
                translationMode = 'off';
                document.getElementById('bt-toolbar').classList.remove('bt-active');
                document.getElementById('bt-label').textContent = 'Traducir';
                showToast('🔤 Traducción desactivada');
                removeTranslations();
                break;
        }
    }

    // === PARAGRAPH EXTRACTION ===
    function getParagraphs() {
        // CWA epub.js reader renders inside iframe
        const iframe = document.querySelector('iframe');
        let doc = iframe ? iframe.contentDocument : document;
        if (!doc) return [];
        const container = doc.querySelector('body') || doc;
        return Array.from(container.querySelectorAll('p, h1, h2, h3, h4, blockquote'));
    }
    function getParagraphText(el) {
        if (el.dataset.btOriginal) {
            return el.dataset.btOriginal.trim();
        }
        return el.textContent.trim();
    }

    // === TRANSLATION ===
    async function translatePage() {
        const paras = getParagraphs();
        if (!paras.length) {
            console.log('[BT] No paragraphs found in reader');
            return;
        }
        console.log(`[BT] Translating ${paras.length} paragraphs to ${targetLang}...`);

        for (let i = 0; i < paras.length; i++) {
            const text = getParagraphText(paras[i]);
            if (!text || text.length < 3) continue;

            const hash = hashText(text);

            if (translatedParagraphs[hash]) {
                removeLoadingIndicator(paras[i]);
                showBilingual(paras[i], translatedParagraphs[hash]);
                continue;
            }

            // M7: Add loading indicator
            addLoadingIndicator(paras[i]);

            try {
                const resp = await fetch(`${TRANSLATOR_URL}/translate`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        text: text,
                        source_lang: SOURCE_LANG,
                        target_lang: targetLang,
                    }),
                });
                const data = await resp.json();
                translatedParagraphs[hash] = data.translated;

                // M7: Remove loading indicator
                removeLoadingIndicator(paras[i]);

                if (translationMode === 'bilingual') {
                    showBilingual(paras[i], data.translated);
                } else if (translationMode === 'translated') {
                    if (!paras[i].dataset.btOriginal) {
                        paras[i].dataset.btOriginal = paras[i].textContent;
                    }
                    // C4: Use textContent to prevent XSS
                    paras[i].textContent = data.translated;
                }
            } catch(e) {
                removeLoadingIndicator(paras[i]);
                console.error(`[BT] Failed para ${i} (hash ${hash}):`, e);
            }
        }
        console.log('[BT] Done!');
    }

    function showBilingual(el, translation) {
        // Skip if already has translation
        let next = el.nextElementSibling;
        if (next && next.classList.contains('bt-translation')) {
            // C4: Use textContent to prevent XSS
            next.textContent = translation;
            return;
        }
        const div = document.createElement('div');
        div.className = 'bt-translation';
        // C4: Use textContent to prevent XSS
        div.textContent = translation;
        el.parentNode.insertBefore(div, el.nextSibling);
    }

    function showTranslatedOnly() {
        const paras = getParagraphs();
        paras.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            if (translatedParagraphs[hash]) {
                if (!el.dataset.btOriginal) el.dataset.btOriginal = el.textContent;
                // C4: Use textContent to prevent XSS
                el.textContent = translatedParagraphs[hash];
            }
        });
    }

    function removeTranslations() {
        // Remove translation divs and loading indicators from main document
        document.querySelectorAll('.bt-translation, .bt-loading').forEach(el => el.remove());

        // Also check inside iframe
        const iframe = document.querySelector('iframe');
        if (iframe && iframe.contentDocument) {
            iframe.contentDocument.querySelectorAll('.bt-translation, .bt-loading').forEach(el => el.remove());
        }

        // Restore original text from data-bt-original
        const restoreIn = (root) => {
            root.querySelectorAll('[data-bt-original]').forEach(el => {
                el.textContent = el.dataset.btOriginal;
                delete el.dataset.btOriginal;
            });
        };
        restoreIn(document);
        if (iframe && iframe.contentDocument) {
            restoreIn(iframe.contentDocument);
        }
    }

    // === KEYBOARD SHORTCUT (M6) ===
    function setupKeyboardShortcut() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+T (Windows/Linux) or Cmd+T (Mac)
            if ((e.ctrlKey || e.metaKey) && e.key === 't') {
                e.preventDefault();
                e.stopPropagation();
                cycleMode();
            }
        });
    }

    // === INIT ===
    function init() {
        createToolbar();
        setupKeyboardShortcut();
        console.log('[BT] CWA Translator v2.0 ready — click 🌐 or press Ctrl+T to translate');
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        setTimeout(init, 1500); // Wait for reader iframe to load
    }
})();
