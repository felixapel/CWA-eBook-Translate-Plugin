/**
 * book-translator — Calibre-Web-Automated Translation Overlay
 */

(function () {
    // ── Configuration ──────────────────────────────────────────────────
    // Optional overrides injected by the CWA template (window.BOOK_TRANSLATOR).
    // An empty/absent apiUrl falls back to dynamic host-based resolution so the
    // overlay keeps working when accessed over the LAN (not just localhost).
    const cfg = (typeof window !== 'undefined' && window.BOOK_TRANSLATOR) || {};
    const TRANSLATOR_URL = (cfg.apiUrl && cfg.apiUrl.length)
        ? cfg.apiUrl
        : (window.location.protocol === 'https:' ? '' : `http://${window.location.hostname}:8390`);
    let SOURCE_LANG = cfg.sourceLang || 'English'; // Assume source is English

    // Map browser language to full language name for the backend
    const langMap = {
        'es': 'Spanish', 'en': 'English', 'fr': 'French', 'de': 'German',
        'pt': 'Portuguese', 'it': 'Italian', 'ru': 'Russian', 'zh': 'Chinese',
        'ja': 'Japanese'
    };

    const browserCode = (navigator.language || 'es').split('-')[0];
    const defaultLang = langMap[browserCode] || 'Spanish';
    let TARGET_LANG = localStorage.getItem('bt_lang') || cfg.targetLang || defaultLang;

    let translationMode = localStorage.getItem('bt_mode') || 'off'; // 'off', 'bilingual', 'translated'
    let isTranslating = false;
    let translatedParagraphs = {}; // hash -> text
    let prefetchQueue = [];
    let isPrefetching = false;
    let lastFirstVisibleHash = null;

    // ── In-flight request control (responsive buttons + language switches) ──
    // `generation` is bumped whenever the user changes mode/language so that
    // stale in-flight responses are ignored. `activeControllers` lets us abort
    // pending fetches immediately instead of blocking the UI until they finish.
    let generation = 0;
    const activeControllers = new Set();

    function newGeneration() {
        generation++;
        for (const c of activeControllers) {
            try { c.abort(); } catch (e) { /* ignore */ }
        }
        activeControllers.clear();
        prefetchQueue = [];
        isTranslating = false;
        isPrefetching = false;
        updateLoadingIndicator();
        return generation;
    }

    function isBadTranslation(tr) {
        // Treat backend error markers and empty results as "not translated" so
        // they are neither rendered nor cached client-side — letting them retry.
        return !tr || typeof tr !== 'string'
            || tr.startsWith('[TRANSLATION ERROR')
            || tr.startsWith('[ERROR');
    }

    function renderMode(elements) {
        if (translationMode === 'bilingual') showTranslationsBilingual(elements);
        else if (translationMode === 'translated') showTranslationsInline('translated', elements);
    }

    // ── i18n ───────────────────────────────────────────────────────────
    const strings = {
        en: { translate: '📖 View: Original', bilingual: '🌐 View: Bilingual', translated: '🌐 View: Translated', off: '📖 Translation disabled', loading: 'Translating...' },
        es: { translate: '📖 Vista: Original', bilingual: '🌐 Vista: Bilingüe', translated: '🌐 Vista: Traducido', off: '📖 Traducción desactivada', loading: 'Traduciendo...' },
        fr: { translate: '📖 Vue: Original', bilingual: '🌐 Vue: Bilingue', translated: '🌐 Vue: Traduit', off: '📖 Traduction désactivée', loading: 'Traduction...' },
        de: { translate: '📖 Ansicht: Original', bilingual: '🌐 Ansicht: Zweisprachig', translated: '🌐 Ansicht: Übersetzt', off: '📖 Übersetzung deaktiviert', loading: 'Übersetzen...' },
        pt: { translate: '📖 Vista: Original', bilingual: '🌐 Vista: Bilíngue', translated: '🌐 Vista: Traduzido', off: '📖 Tradução desativada', loading: 'Traduzindo...' },
    };
    const t = strings[browserCode] || strings.en;

    const availableLangs = [
        { code: 'Spanish', name: 'Español' },
        { code: 'English', name: 'English' },
        { code: 'French', name: 'Français' },
        { code: 'German', name: 'Deutsch' },
        { code: 'Portuguese', name: 'Português' },
        { code: 'Italian', name: 'Italiano' },
        { code: 'Russian', name: 'Русский' }
    ];

    // ── UI Components ──────────────────────────────────────────────────
    function createFloatingUI() {
        if (document.getElementById('translator-float-container')) return;

        const container = document.createElement('div');
        container.id = 'translator-float-container';
        container.style.cssText = 'position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 99999; display: flex; align-items: center; background: rgba(50, 50, 50, 0.9); backdrop-filter: blur(10px); color: white; padding: 6px 12px; border-radius: 24px; box-shadow: 0 4px 16px rgba(0,0,0,0.25); gap: 8px; user-select: none; transition: background 0.3s;';

        const indicator = document.createElement('div');
        indicator.id = 'translator-loading-indicator';
        indicator.innerHTML = '<style>@keyframes bt-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style><div style="width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3); border-top-color: white; border-radius: 50%; animation: bt-spin 1s linear infinite;"></div>';
        indicator.style.cssText = 'display: none; margin-left: 4px; margin-right: 4px;';
        
        const btn = document.createElement('div');
        btn.id = 'translator-float-btn';
        btn.style.cssText = 'cursor: pointer; font-weight: 600; font-family: system-ui, sans-serif; padding: 6px 12px; border-radius: 16px; transition: background 0.2s; text-align: center;';
        
        const sel = document.createElement('select');
        sel.id = 'translator-float-lang';
        sel.style.cssText = 'background: transparent; color: white; border: 1px solid rgba(255,255,255,0.4); border-radius: 12px; padding: 4px 8px; font-size: 13px; cursor: pointer; outline: none; font-family: system-ui;';
        
        availableLangs.forEach(lang => {
            const opt = document.createElement('option');
            opt.value = lang.code;
            opt.textContent = lang.name;
            opt.style.color = '#000';
            if (lang.code === TARGET_LANG) opt.selected = true;
            sel.appendChild(opt);
        });

        sel.onchange = (e) => {
            TARGET_LANG = e.target.value;
            localStorage.setItem('bt_lang', TARGET_LANG);
            newGeneration();              // abort in-flight old-language requests
            translatedParagraphs = {};    // clear client cache
            if (translationMode !== 'off') {
                removeAllTranslations();
                translateCurrentPage();
            }
        };

        const updateBtnState = () => {
            if (translationMode === 'bilingual') {
                btn.textContent = t.bilingual;
                container.style.background = 'rgba(15, 157, 88, 0.9)'; // green
            } else if (translationMode === 'translated') {
                btn.textContent = t.translated;
                container.style.background = 'rgba(244, 180, 0, 0.9)'; // yellow
            } else {
                btn.textContent = t.translate;
                container.style.background = 'rgba(50, 50, 50, 0.9)'; // grey
            }
        };

        updateBtnState();

        btn.onclick = () => {
            const prevMode = translationMode;
            if (translationMode === 'off') translationMode = 'bilingual';
            else if (translationMode === 'bilingual') translationMode = 'translated';
            else translationMode = 'off';

            localStorage.setItem('bt_mode', translationMode);
            updateBtnState();

            if (translationMode === 'off') {
                newGeneration();            // cancel in-flight work so the next ON works immediately
                removeAllTranslations();
                showToast(t.off);
            } else if (prevMode === 'off') {
                translateCurrentPage();     // fresh start
            } else {
                // Switching bilingual <-> translated: re-render cached paragraphs
                // instantly (no waiting on the in-flight batch), then keep filling gaps.
                renderMode(getParagraphs());
                translateCurrentPage();
            }
        };

        btn.onmouseover = () => btn.style.background = 'rgba(255,255,255,0.15)';
        btn.onmouseout = () => btn.style.background = 'transparent';

        container.appendChild(sel);
        container.appendChild(indicator);
        container.appendChild(btn);
        document.body.appendChild(container);
    }

    function updateLoadingIndicator() {
        const ind = document.getElementById('translator-loading-indicator');
        if (ind) {
            ind.style.display = (isTranslating || isPrefetching) ? 'block' : 'none';
        }
    }

    // ── Toast Notifications ────────────────────────────────────────────
    function showToast(message) {
        let toast = document.getElementById('bt-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'bt-toast';
            toast.style.cssText = 'position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); color: white; padding: 8px 16px; border-radius: 20px; font-family: system-ui; font-size: 14px; z-index: 100000; opacity: 0; transition: opacity 0.3s; pointer-events: none;';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.style.opacity = '1';
        setTimeout(() => toast.style.opacity = '0', 3000);
    }

    // ── DOM Helpers ────────────────────────────────────────────────────
    function getParagraphs() {
        let doc = document;
        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        if (iframe && iframe.contentDocument) {
            doc = iframe.contentDocument;
        }
        
        // Match standard tags and custom title/chapter classes commonly used in EPUBs
        const rawElements = Array.from(doc.querySelectorAll(
            'p, h1, h2, h3, h4, h5, h6, li, td, div.calibre1, div.text, a, ' +
            '[class*="title"], [class*="subtitle"], [class*="chapter"], [class*="author"], [class*="heading"]'
        ));
        
        // 1. Initial filter for content and layout constraints
        const filtered = rawElements.filter(el => {
            const text = el.textContent.trim();
            if (text.length < 2) return false;
            
            const tagName = el.tagName.toLowerCase();
            
            // If it's a link
            if (tagName === 'a') {
                // Only translate standalone links (like in TOC). Filter out links inside body paragraphs.
                const insideParagraph = el.closest('p, div.calibre1, div.text');
                if (insideParagraph) return false;
                return true;
            }
            
            // If it's a block that contains links, let the links translate themselves to preserve clickability
            if (['li', 'div', 'td'].includes(tagName)) {
                const hasLink = el.querySelector('a');
                if (hasLink) return false; 
            }
            
            // If it's a container holding other block children, don't translate the container itself
            if (tagName === 'div') {
                const hasBlockChildren = el.querySelector('p, h1, h2, h3, h4, h5, h6, li');
                if (hasBlockChildren) return false;
            }
            
            return true;
        });

        // 2. De-duplicate hierarchy: If a parent/ancestor is already in the list to be translated,
        // we skip the child element to translate the parent as a single contextual block.
        return filtered.filter(el => {
            let parent = el.parentElement;
            while (parent) {
                if (filtered.includes(parent)) {
                    return false; // Skip, parent will be translated
                }
                parent = parent.parentElement;
            }
            return true;
        });
    }

    function getVisibleParagraphs() {
        let doc = document;
        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        if (!iframe || !iframe.contentDocument) {
            return Array.from(doc.querySelectorAll('p, div.calibre1, div.text')).slice(0, 5);
        }
        
        doc = iframe.contentDocument;
        const paragraphs = Array.from(doc.querySelectorAll('p, div.calibre1, div.text'));
        const iframeWidth = iframe.clientWidth || window.innerWidth;
        const iframeHeight = iframe.clientHeight || window.innerHeight;
        
        return paragraphs.filter(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
            
            // Check if element is horizontally or vertically visible inside the viewport
            const isHorizVisible = (rect.left >= -100 && rect.left < iframeWidth - 20);
            const isVertVisible = (rect.top >= -100 && rect.top < iframeHeight - 20);
            
            return isHorizVisible && isVertVisible;
        });
    }

    function getParagraphText(el) {
        if (el.dataset.originalText) return el.dataset.originalText;
        const clone = el.cloneNode(true);
        clone.querySelectorAll('.bt-loading, .bt-translation').forEach(n => n.remove());
        return clone.textContent.trim();
    }

    function hashText(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            hash = ((hash << 5) - hash) + str.charCodeAt(i);
            hash = hash & hash;
        }
        return (hash >>> 0).toString(36);
    }

    // ── Translation engine ─────────────────────────────────────────────
    // Tunables for fluidity. The current page is translated 1 paragraph at a
    // time so the first line appears as fast as possible; the rest of the
    // chapter is filled in afterwards, in the background, at low priority.
    const VISIBLE_CHUNK = 1;       // paragraphs per request for the on-screen page
    const PREFETCH_CHUNK = 3;      // paragraphs per request for background fill
    const PREFETCH_GAP_MS = 600;   // pause between background requests
    const REQUEST_TIMEOUT_MS = 90000; // client-side safety net so a hung request can't freeze the UI

    function collectUncached(elements) {
        const out = [];
        const seen = new Set();
        for (const el of elements) {
            const text = getParagraphText(el);
            if (!text || text.length < 2) continue;
            const hash = hashText(text);
            if (translatedParagraphs[hash] || seen.has(hash)) continue;
            seen.add(hash);
            out.push({ el, text, hash });
        }
        return out;
    }

    async function postBatch(texts) {
        const controller = new AbortController();
        activeControllers.add(controller);
        const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
        try {
            const resp = await fetch(`${TRANSLATOR_URL}/translate/batch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paragraphs: texts, source_lang: SOURCE_LANG, target_lang: TARGET_LANG }),
                signal: controller.signal,
            });
            if (!resp.ok) return null;
            return await resp.json();
        } finally {
            clearTimeout(timer);
            activeControllers.delete(controller);
        }
    }

    // Translate a list of {el,text,hash} in chunks, rendering each chunk as soon
    // as it arrives (progressive paint). Bails immediately if superseded.
    async function translateElements(items, myGen, chunkSize) {
        for (let i = 0; i < items.length; i += chunkSize) {
            if (myGen !== generation || translationMode === 'off') return;
            const chunk = items.slice(i, i + chunkSize);
            let data = null;
            try {
                data = await postBatch(chunk.map(b => b.text));
            } catch (e) {
                if (e.name !== 'AbortError') console.error("Translation request failed:", e);
                return; // network/abort/timeout — stop this run; a later trigger retries
            }
            if (myGen !== generation) return; // superseded — drop stale result
            if (data && Array.isArray(data.translations)) {
                data.translations.forEach((tr, idx) => {
                    // Don't cache errors/empties so they retry on the next pass.
                    if (!isBadTranslation(tr)) translatedParagraphs[chunk[idx].hash] = tr;
                });
                renderMode(chunk.map(b => b.el));
            }
        }
    }

    async function translateCurrentPage() {
        if (isTranslating || translationMode === 'off') return;
        isTranslating = true;
        updateLoadingIndicator();
        const myGen = generation;

        // 1. CURRENT PAGE FIRST — progressive, so the first line shows quickly.
        const visibleEls = getVisibleParagraphs();
        const visibleUncached = collectUncached(visibleEls);
        if (visibleUncached.length > 0) {
            await translateElements(visibleUncached, myGen, VISIBLE_CHUNK);
        }
        // Paint any visible paragraphs that were already cached (revisited page).
        if (myGen === generation) renderMode(visibleEls);

        if (myGen === generation) isTranslating = false;
        updateLoadingIndicator();

        if (myGen !== generation || translationMode === 'off') return;

        // 2. REST OF CHAPTER — background fill, low priority and preemptible.
        const visibleSet = new Set(visibleEls);
        prefetchQueue = getParagraphs().filter(el => !visibleSet.has(el));
        triggerPrefetch();
    }

    // ── Background Prefetching ─────────────────────────────────────────
    async function triggerPrefetch() {
        if (isPrefetching || prefetchQueue.length === 0) return;
        isPrefetching = true;
        updateLoadingIndicator();
        const myGen = generation;

        // Yield to the visible page: pause whenever a page translation is active.
        while (prefetchQueue.length > 0 && translationMode !== 'off'
               && myGen === generation && !isTranslating) {
            const batch = prefetchQueue.slice(0, PREFETCH_CHUNK);
            prefetchQueue = prefetchQueue.slice(PREFETCH_CHUNK);

            const items = collectUncached(batch);
            if (items.length > 0) {
                await translateElements(items, myGen, PREFETCH_CHUNK);
            }
            if (myGen !== generation) break;

            await new Promise(resolve => setTimeout(resolve, PREFETCH_GAP_MS));
        }

        if (myGen === generation) isPrefetching = false;
        updateLoadingIndicator();
    }

    // ── Rendering ──────────────────────────────────────────────────────
    function showTranslationsBilingual(paragraphs) {
        paragraphs.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            const translated = translatedParagraphs[hash];
            if (isBadTranslation(translated) || translated === text) return;

            let transEl = el.querySelector('.bt-translation');
            if (transEl) {
                transEl.textContent = translated;
                return;
            }

            transEl = document.createElement('span');
            transEl.className = 'bt-translation';
            transEl.style.cssText = 'display: block; margin-top: 8px; color: #1565c0; font-style: italic; border-left: 3px solid #90caf9; padding-left: 12px; font-weight: normal;';
            transEl.textContent = translated;
            
            el.style.pageBreakInside = 'avoid';
            el.style.breakInside = 'avoid';
            
            el.appendChild(transEl);
        });
    }

    function showTranslationsInline(mode, paragraphs) {
        paragraphs.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            const translated = translatedParagraphs[hash];
            if (isBadTranslation(translated)) return;

            // Store the CLEAN original (getParagraphText strips any bt spans) so
            // toggling back off restores correctly even after bilingual rendering.
            if (!el.dataset.originalText) {
                el.dataset.originalText = text;
            }
            // Remove any bilingual/loading spans before replacing the text.
            el.querySelectorAll('.bt-translation, .bt-loading').forEach(n => n.remove());
            el.textContent = translated;
        });
    }

    function removeAllTranslations() {
        document.querySelectorAll('.bt-translation, .bt-loading').forEach(el => el.remove());

        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        if (iframe && iframe.contentDocument) {
            iframe.contentDocument.querySelectorAll('.bt-translation, .bt-loading').forEach(el => el.remove());
        }

        const restoreIn = (root) => {
            root.querySelectorAll('[data-original-text]').forEach(el => {
                el.textContent = el.dataset.originalText;
                delete el.dataset.originalText;
            });
        };
        restoreIn(document);
        if (iframe && iframe.contentDocument) restoreIn(iframe.contentDocument);
    }

    // ── Observers & Polling ────────────────────────────────────────────
    const isBtNode = (n) => n.nodeType === 1 && n.classList &&
        (n.classList.contains('bt-translation') || n.classList.contains('bt-loading'));

    function startMutationObserver() {
        let translateTimeout = null;
        const observer = new MutationObserver((mutations) => {
            if (translationMode === 'off') return;
            // Ignore mutations caused by our own inserted translations, otherwise
            // appending a translation would re-trigger translation forever.
            let shouldTranslate = false;
            for (const m of mutations) {
                for (const n of m.addedNodes) {
                    if (!isBtNode(n)) { shouldTranslate = true; break; }
                }
                if (shouldTranslate) break;
            }
            if (shouldTranslate) {
                clearTimeout(translateTimeout);
                translateTimeout = setTimeout(() => {
                    translateCurrentPage();
                }, 200);
            }
        });

        const observeIframes = () => {
            const iframes = document.querySelectorAll('iframe');
            iframes.forEach(iframe => {
                try {
                    const idoc = iframe.contentDocument || iframe.contentWindow.document;
                    if (idoc && idoc.body && !idoc.body.dataset.btObserved) {
                        observer.observe(idoc.body, { childList: true, subtree: true });
                        idoc.body.dataset.btObserved = "true";
                        if (translationMode !== 'off') translateCurrentPage();
                    }
                } catch (e) {}
            });
        };

        observer.observe(document.body, { childList: true, subtree: true });
        setInterval(observeIframes, 500);
        observeIframes();
    }

    function startPageTurnDetector() {
        setInterval(() => {
            if (translationMode === 'off') return;
            const visible = getVisibleParagraphs();
            if (visible.length > 0) {
                const firstText = getParagraphText(visible[0]);
                if (firstText) {
                    const hash = hashText(firstText);
                    if (hash !== lastFirstVisibleHash) {
                        lastFirstVisibleHash = hash;
                        // Page/chapter changed: preempt any stale background prefetch
                        // so the LLM is freed up to translate the NEW visible page now.
                        newGeneration();
                        translateCurrentPage();
                    }
                }
            }
        }, 350);
    }

    // ── Start ──────────────────────────────────────────────────────────
    function init() {
        createFloatingUI();
        startMutationObserver();
        startPageTurnDetector();
        if (translationMode !== 'off') {
            translateCurrentPage();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
