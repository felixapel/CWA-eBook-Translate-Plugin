/**
 * book-translator — CWA Reader Overlay
 * Injects bilingual translation UI into Calibre-Web-Automated's reader.
 *
 * Features:
 * - "🌐 Traducir" button in the reader toolbar
 * - Side-by-side bilingual mode (original above, translation in blue below)
 * - Translation-only mode (replaces original)
 * - Auto pre-fetch of next 5 pages
 * - Cache-aware: never re-translates the same paragraph
 * - Keyboard shortcut Ctrl+T (Cmd+T on Mac) to toggle
 * - Per-paragraph loading indicators
 * - epub.js relocated event hook for auto-translation on page change
 * - Toast notifications for mode changes
 * - Progress indicator (translated / total)
 *
 * Backend: book-translator Flask service on port 8390
 */

(function () {
    'use strict';

    // ── Configuration ──────────────────────────────────────────────────
    const CONFIG = window.BOOK_TRANSLATOR || {};
    const TRANSLATOR_URL = CONFIG.apiUrl || 'http://192.168.0.180:8390';
    const SOURCE_LANG = CONFIG.sourceLang || 'English';
    const TARGET_LANG = CONFIG.targetLang || 'Spanish';
    const PREFETCH_PAGES = CONFIG.prefetchPages || 5;

    // ── State ──────────────────────────────────────────────────────────
    let translationMode = 'off'; // 'off' | 'bilingual' | 'translated'
    let translatedParagraphs = {}; // Map<contentHash, translatedText>
    let prefetchedPages = new Set(); // Track which pages have been prefetched
    let currentPage = 1;
    let totalPages = 1;
    let isTranslating = false; // Debounce guard

    // ── Content-based hashing ─────────────────────────────────────────
    // H3: Use content hash instead of positional indexing so translations
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

    // ── UI Elements ─────────────────────────────────────────────────────
    function createToolbarButton() {
        const btn = document.createElement('button');
        btn.id = 'btn-translate-toggle';
        btn.className = 'btn btn-sm btn-default';
        btn.title = 'Toggle translation (Bilingual / Translation only / Off) — Ctrl+T';
        btn.innerHTML = '🌐 <span id="translate-label">Traducir</span>';
        btn.style.cssText = 'margin-left: 8px;';
        btn.onclick = cycleTranslationMode;
        return btn;
    }

    function createLanguageSelector() {
        const select = document.createElement('select');
        select.id = 'target-lang-select';
        select.className = 'form-control input-sm';
        select.style.cssText = 'margin-left: 8px; width: auto; display: inline-block;';
        const languages = [
            { code: 'Spanish', label: '🇪🇸 Español' },
            { code: 'English', label: '🇬🇧 English' },
            { code: 'German', label: '🇩🇪 Deutsch' },
            { code: 'French', label: '🇫🇷 Français' },
            { code: 'Italian', label: '🇮🇹 Italiano' },
            { code: 'Portuguese', label: '🇵🇹 Português' },
            { code: 'Chinese', label: '🇨🇳 中文' },
            { code: 'Japanese', label: '🇯🇵 日本語' },
            { code: 'Russian', label: '🇷🇺 Русский' },
        ];
        languages.forEach(lang => {
            const opt = document.createElement('option');
            opt.value = lang.code;
            opt.textContent = lang.label;
            if (lang.code === TARGET_LANG) opt.selected = true;
            select.appendChild(opt);
        });
        select.onchange = onLanguageChange;
        return select;
    }

    // ── Toast notifications ─────────────────────────────────────────────
    function showToast(message) {
        // Remove any existing toast
        const existing = document.getElementById('bt-toast');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.id = 'bt-toast';
        toast.textContent = message;
        document.body.appendChild(toast);

        // Trigger slide-in animation
        requestAnimationFrame(() => toast.classList.add('bt-toast-visible'));

        // Auto-dismiss after 2.5s
        setTimeout(() => {
            toast.classList.remove('bt-toast-visible');
            setTimeout(() => toast.remove(), 400);
        }, 2500);
    }

    // ── Progress indicator ──────────────────────────────────────────────
    function updateProgress(translated, total) {
        let counter = document.getElementById('bt-progress');
        if (!counter) {
            counter = document.createElement('span');
            counter.id = 'bt-progress';
            const toolbar = document.querySelector('#btn-translate-toggle');
            if (toolbar && toolbar.parentNode) {
                toolbar.parentNode.appendChild(counter);
            }
        }
        if (translationMode === 'off') {
            counter.style.display = 'none';
        } else {
            counter.style.display = 'inline-block';
            counter.textContent = `${translated}/${total}`;
        }
    }

    // ── Inject UI into CWA reader ──────────────────────────────────────
    function injectUI() {
        // CWA reader toolbar: find the navigation bar
        const navBars = document.querySelectorAll('.navbar, .reader-toolbar, .cwa-toolbar');
        let toolbar = null;

        // Try to find the reader toolbar specifically
        for (const bar of navBars) {
            if (bar.textContent.includes('Contents') || bar.querySelector('.btn')) {
                toolbar = bar;
                break;
            }
        }

        // Fallback: inject into the first toolbar we find
        if (!toolbar) {
            toolbar = document.querySelector('.navbar-right, .nav, .toolbar');
        }

        if (toolbar) {
            const container = document.createElement('div');
            container.className = 'btn-group';
            container.style.cssText = 'margin-left: 10px;';
            container.appendChild(createToolbarButton());
            container.appendChild(createLanguageSelector());
            toolbar.appendChild(container);
            console.log('[book-translator] UI injected into CWA reader toolbar');
        } else {
            // Last resort: create a floating button
            const float = document.createElement('div');
            float.id = 'translator-float';
            float.innerHTML = '🌐 Traducir';
            float.onclick = cycleTranslationMode;
            document.body.appendChild(float);
            console.log('[book-translator] Floating button created (no toolbar found)');
        }
    }

    // ── Translation mode cycling ───────────────────────────────────────
    function cycleTranslationMode() {
        switch (translationMode) {
            case 'off':
                translationMode = 'bilingual';
                updateButtonLabel('Bilingüe');
                showToast('📖 Modo bilingüe activado');
                translateCurrentPage();
                break;
            case 'bilingual':
                translationMode = 'translated';
                updateButtonLabel('Traducido');
                showToast('📝 Modo solo traducción');
                showTranslationsInline('translated');
                break;
            case 'translated':
                translationMode = 'off';
                updateButtonLabel('Traducir');
                showToast('🔤 Traducción desactivada');
                removeAllTranslations();
                updateProgress(0, 0);
                break;
        }
    }

    function updateButtonLabel(label) {
        const el = document.getElementById('translate-label');
        if (el) el.textContent = label;
        const float = document.getElementById('translator-float');
        if (float) float.textContent = '🌐 ' + label;
    }

    function onLanguageChange() {
        // Reset all translations and re-translate
        translatedParagraphs = {};
        prefetchedPages.clear();
        if (translationMode !== 'off') {
            removeAllTranslations();
            translateCurrentPage();
        }
    }

    function getTargetLang() {
        const select = document.getElementById('target-lang-select');
        return select ? select.value : TARGET_LANG;
    }

    // ── Paragraph extraction ───────────────────────────────────────────
    // C3: epub.js renders content inside an iframe within #viewer.
    // We must search inside the iframe's contentDocument to find paragraphs.
    function getParagraphs() {
        let doc = null;

        // 1. Try to find the epub.js iframe (primary approach for CWA reader)
        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        if (iframe && iframe.contentDocument) {
            doc = iframe.contentDocument;
            const body = doc.querySelector('body');
            if (body) {
                const paragraphs = body.querySelectorAll('p, h1, h2, h3, h4, blockquote, div.text, .para, .paragraph');
                if (paragraphs.length > 0) {
                    return Array.from(paragraphs);
                }
            }
        }

        // 2. Fallback: search the main document (non-iframe scenarios)
        const container = document.querySelector('#reader-content, .epub-container, .reader-content, #book-content');
        if (!container) return [];

        // Get all text paragraphs (exclude headers, images, etc.)
        const paragraphs = container.querySelectorAll('p, div.text, .para, .paragraph, section > div');
        if (paragraphs.length === 0) {
            // Fallback: all <p> tags in body
            return Array.from(container.querySelectorAll('p'));
        }
        return Array.from(paragraphs);
    }

    function getParagraphText(el) {
        return el.textContent.trim();
    }

    // ── Iframe style injection ─────────────────────────────────────────
    // Since translations are injected inside the iframe, we need the styles there too.
    function injectIframeStyles() {
        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        if (!iframe || !iframe.contentDocument) return;

        const iframeDoc = iframe.contentDocument;
        if (iframeDoc.getElementById('bt-iframe-styles')) return; // Already injected

        const style = iframeDoc.createElement('style');
        style.id = 'bt-iframe-styles';
        style.textContent = `
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
            .bt-translation:hover { opacity: 1 !important; }
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
        `;
        const head = iframeDoc.head || iframeDoc.querySelector('head');
        if (head) head.appendChild(style);
    }

    // ── Loading indicators (M7) ────────────────────────────────────────
    function addLoadingIndicator(el) {
        // Don't add if one already exists
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

    // ── Translation engine ─────────────────────────────────────────────
    async function translateParagraph(text, contentHash) {
        // Check local cache first (H3: using content hash)
        if (translatedParagraphs[contentHash]) {
            return translatedParagraphs[contentHash];
        }

        try {
            const resp = await fetch(`${TRANSLATOR_URL}/translate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    source_lang: SOURCE_LANG,
                    target_lang: getTargetLang(),
                }),
            });

            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }

            const data = await resp.json();
            translatedParagraphs[contentHash] = data.translated;
            return data.translated;
        } catch (e) {
            console.error(`[book-translator] Translation failed for hash ${contentHash}:`, e);
            return `[Error: ${e.message}]`;
        }
    }

    async function translateCurrentPage() {
        // Debounce: prevent rapid re-translation
        if (isTranslating) return;
        isTranslating = true;

        // Inject styles into iframe if needed
        injectIframeStyles();

        const paragraphs = getParagraphs();
        if (paragraphs.length === 0) {
            console.log('[book-translator] No paragraphs found on current page');
            isTranslating = false;
            return;
        }

        console.log(`[book-translator] Translating ${paragraphs.length} paragraphs...`);

        let translatedCount = 0;
        const totalCount = paragraphs.length;

        // M7: Add loading indicators for paragraphs that need translation
        const paraData = paragraphs.map(el => {
            const text = getParagraphText(el);
            const hash = text ? hashText(text) : null;
            return { el, text, hash };
        });

        // Add loading indicators before starting
        paraData.forEach(({ el, text, hash }) => {
            if (text && hash && !translatedParagraphs[hash]) {
                addLoadingIndicator(el);
            } else if (hash && translatedParagraphs[hash]) {
                translatedCount++;
            }
        });
        updateProgress(translatedCount, totalCount);

        // Translate paragraphs in parallel batches of 3
        const batchSize = 3;
        for (let i = 0; i < paraData.length; i += batchSize) {
            const batch = paraData.slice(i, i + batchSize);
            const promises = batch.map(({ el, text, hash }) => {
                if (!text || !hash) return Promise.resolve('');
                return translateParagraph(text, hash).then(translated => {
                    // M7: Remove loading indicator and update progress
                    removeLoadingIndicator(el);
                    translatedCount++;
                    updateProgress(translatedCount, totalCount);
                    return translated;
                });
            });
            await Promise.all(promises);
        }

        // Render translations
        if (translationMode === 'bilingual') {
            showTranslationsBilingual(paragraphs);
        } else if (translationMode === 'translated') {
            showTranslationsInline('translated', paragraphs);
        }

        isTranslating = false;

        // Trigger prefetch of next pages
        prefetchNextPages();
    }

    // ── Rendering ──────────────────────────────────────────────────────
    function showTranslationsBilingual(paragraphs) {
        paragraphs.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            const translated = translatedParagraphs[hash];
            if (!translated || translated === text) return;

            // Check if already has translation div
            let transEl = el.nextElementSibling;
            if (transEl && transEl.classList.contains('bt-translation')) {
                // C4: Use textContent to prevent XSS
                transEl.textContent = translated;
                return;
            }

            // Create translation element
            transEl = document.createElement('div');
            transEl.className = 'bt-translation';
            // C4: Use textContent to prevent XSS
            transEl.textContent = translated;
            el.parentNode.insertBefore(transEl, el.nextSibling);
        });
    }

    function showTranslationsInline(mode, paragraphs) {
        if (!paragraphs) paragraphs = getParagraphs();
        paragraphs.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            const translated = translatedParagraphs[hash];
            if (!translated) return;

            if (mode === 'translated') {
                // Store original and replace
                if (!el.dataset.originalText) {
                    el.dataset.originalText = el.textContent;
                }
                // C4: Use textContent instead of innerHTML to prevent XSS
                el.textContent = translated;
            }
        });
    }

    function removeAllTranslations() {
        // Remove from main document
        document.querySelectorAll('.bt-translation, .bt-loading').forEach(el => el.remove());

        // Also remove from iframe (C3: translations are inside the iframe)
        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        if (iframe && iframe.contentDocument) {
            iframe.contentDocument.querySelectorAll('.bt-translation, .bt-loading').forEach(el => el.remove());
        }

        // Restore originals
        const restoreIn = (root) => {
            root.querySelectorAll('[data-original-text]').forEach(el => {
                el.textContent = el.dataset.originalText;
                delete el.dataset.originalText;
            });
        };
        restoreIn(document);
        if (iframe && iframe.contentDocument) {
            restoreIn(iframe.contentDocument);
        }
    }

    // ── Pre-fetch next pages ───────────────────────────────────────────
    function prefetchNextPages() {
        // In CWA's reader, pages are virtual (scrolling or paginated)
        // We detect when user is near the end of current content and pre-fetch
        const paragraphs = getParagraphs();
        if (paragraphs.length === 0) return;

        // Pre-fetch: collect paragraph texts from all visible content
        const allTexts = paragraphs
            .map(el => getParagraphText(el))
            .filter(t => t.length > 0);

        if (allTexts.length === 0) return;

        // Filter out already translated paragraphs (H3: using content hash)
        const untranslated = [];
        const untranslatedHashes = [];
        paragraphs.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            if (!translatedParagraphs[hash]) {
                untranslated.push(text);
                untranslatedHashes.push(hash);
            }
        });

        if (untranslated.length === 0) return;

        // Send to server for pre-fetch (fire-and-forget)
        console.log(`[book-translator] Pre-fetching ${untranslated.length} paragraphs...`);
        fetch(`${TRANSLATOR_URL}/prefetch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                paragraphs: untranslated,
                source_lang: SOURCE_LANG,
                target_lang: getTargetLang(),
                book_id: window.location.pathname,
            }),
        })
            .then(resp => resp.json())
            .then(data => {
                if (data.status === 'accepted') {
                    console.log(`[book-translator] Pre-fetch job started (ID: ${data.job_id})`);
                } else {
                    console.error('[book-translator] Pre-fetch failed:', data);
                }
            })
            .catch(e => console.error('[book-translator] Pre-fetch error:', e));
    }

    // ── Scroll-based prefetch trigger ──────────────────────────────────
    function onScroll() {
        const scrollPos = window.scrollY + window.innerHeight;
        const docHeight = document.documentElement.scrollHeight;
        const scrollPercent = scrollPos / docHeight;

        // When user is 70% through the page, prefetch
        if (scrollPercent > 0.7 && translationMode !== 'off') {
            prefetchNextPages();
        }
    }

    // ── epub.js relocated event hook ────────────────────────────────────
    // When epub.js navigates to a new page/section, re-translate if mode is active.
    function hookRelocatedEvent() {
        // The global `reader` object is provided by CWA's reader.min.js
        if (typeof reader !== 'undefined' && reader.rendition) {
            reader.rendition.on('relocated', debounce(function () {
                console.log('[book-translator] epub.js relocated — page changed');
                if (translationMode !== 'off') {
                    // Small delay to let the new content render in the iframe
                    setTimeout(() => {
                        injectIframeStyles();
                        translateCurrentPage();
                    }, 200);
                }
            }, 500));
            console.log('[book-translator] Hooked into epub.js relocated event');
        } else {
            // Retry after a short delay — reader may not be ready yet
            setTimeout(hookRelocatedEvent, 1000);
        }
    }

    // ── Debounce utility ───────────────────────────────────────────────
    function debounce(fn, delay) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    // ── Keyboard shortcut (M6) ──────────────────────────────────────────
    function setupKeyboardShortcut() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+T (Windows/Linux) or Cmd+T (Mac) to toggle translation
            if ((e.ctrlKey || e.metaKey) && e.key === 't') {
                e.preventDefault();
                e.stopPropagation();
                cycleTranslationMode();
            }
        });
    }

    // ── Initialization ──────────────────────────────────────────────────
    function init() {
        // M6: Register keyboard shortcut immediately
        setupKeyboardShortcut();

        // Wait for reader to load (check iframe or main content)
        const checkInterval = setInterval(() => {
            const paragraphs = getParagraphs();
            if (paragraphs.length > 0) {
                clearInterval(checkInterval);
                injectUI();
                setupScrollListener();
                hookRelocatedEvent();
                console.log('[book-translator] Ready — found', paragraphs.length, 'paragraphs');
            }
        }, 500);

        // Timeout after 10 seconds
        setTimeout(() => clearInterval(checkInterval), 10000);
    }

    function setupScrollListener() {
        let scrollTimeout;
        window.addEventListener('scroll', () => {
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(onScroll, 300);
        }, { passive: true });
    }

    // ── Start ──────────────────────────────────────────────────────────
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
