/**
 * book-translator — Calibre-Web-Automated Translation Overlay
 */

(function () {
    // ── Configuration ──────────────────────────────────────────────────
    const TRANSLATOR_URL = window.location.protocol === 'https:' ? '' : '';
    let SOURCE_LANG = 'English'; // Assume source is English
    
    // Map browser language to full language name for the backend
    const langMap = {
        'es': 'Spanish', 'en': 'English', 'fr': 'French', 'de': 'German',
        'pt': 'Portuguese', 'it': 'Italian', 'ru': 'Russian', 'zh': 'Chinese',
        'ja': 'Japanese'
    };
    
    const browserCode = (navigator.language || 'es').split('-')[0];
    const defaultLang = langMap[browserCode] || 'Spanish';
    let TARGET_LANG = localStorage.getItem('bt_lang') || defaultLang;

    let translationMode = localStorage.getItem('bt_mode') || 'off'; // 'off', 'bilingual', 'translated'
    let isTranslating = false;
    let translatedParagraphs = {}; // hash -> text
    let prefetchQueue = [];
    let isPrefetching = false;
    let lastFirstVisibleHash = null;

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
            translatedParagraphs = {}; // clear cache
            prefetchQueue = []; // clear prefetch
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
            if (translationMode === 'off') translationMode = 'bilingual';
            else if (translationMode === 'bilingual') translationMode = 'translated';
            else translationMode = 'off';
            
            localStorage.setItem('bt_mode', translationMode);
            updateBtnState();

            if (translationMode === 'off') {
                prefetchQueue = [];
                removeAllTranslations();
                showToast(t.off);
            } else {
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
    async function translateCurrentPage() {
        if (isTranslating || translationMode === 'off') return;
        isTranslating = true;
        updateLoadingIndicator();

        // 1. Prioritize visible paragraphs
        const visible = getVisibleParagraphs();
        if (visible.length > 0) {
            await translateBatchOfElements(visible);
        }

        // 2. Queue remaining paragraphs in chapter for background prefetch
        const all = getParagraphs();
        const remaining = all.filter(el => !visible.includes(el));
        queuePrefetch(remaining);

        isTranslating = false;
        updateLoadingIndicator();
    }

    async function translateBatchOfElements(elements) {
        const toTranslate = [];
        elements.forEach(el => {
            const text = getParagraphText(el);
            if (!text || text.length < 2) return;
            const hash = hashText(text);
            if (!translatedParagraphs[hash]) {
                toTranslate.push({ el, text, hash });
            }
        });



            try {
                const resp = await fetch(`${TRANSLATOR_URL}/translate/batch`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        paragraphs: toTranslate.map(b => b.text),
                        source_lang: SOURCE_LANG,
                        target_lang: TARGET_LANG,
                    }),
                });

                if (resp.ok) {
                    const data = await resp.json();
                    data.translations.forEach((tr, idx) => {
                        translatedParagraphs[toTranslate[idx].hash] = tr;
                    });
                    
                    // Render
                    if (translationMode === 'bilingual') {
                        showTranslationsBilingual(elements);
                    } else if (translationMode === 'translated') {
                        showTranslationsInline('translated', elements);
                    }
                }
            } catch (e) {
                console.error("Batch translation error:", e);
            }
        } else {
            // Already cached, just render
            if (translationMode === 'bilingual') {
                showTranslationsBilingual(elements);
            } else if (translationMode === 'translated') {
                showTranslationsInline('translated', elements);
            }
        }
    }

    // ── Background Prefetching ─────────────────────────────────────────
    function queuePrefetch(elements) {
        const uncached = elements.filter(el => {
            const text = getParagraphText(el);
            if (!text || text.length < 2) return false;
            const hash = hashText(text);
            return !translatedParagraphs[hash];
        });

        prefetchQueue = uncached;
        triggerPrefetch();
    }

    async function triggerPrefetch() {
        if (isPrefetching || prefetchQueue.length === 0) return;
        isPrefetching = true;
        updateLoadingIndicator();

        while (prefetchQueue.length > 0 && translationMode !== 'off') {
            // Take 3 paragraphs at a time to keep it sequential and light
            const batch = prefetchQueue.slice(0, 3);
            prefetchQueue = prefetchQueue.slice(3);

            const toTranslate = batch.map(el => {
                const text = getParagraphText(el);
                return {
                    el: el,
                    text: text,
                    hash: hashText(text)
                };
            }).filter(b => b.text && b.text.length >= 2 && !translatedParagraphs[b.hash]);

            if (toTranslate.length > 0) {
                try {
                    const resp = await fetch(`${TRANSLATOR_URL}/translate/batch`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            paragraphs: toTranslate.map(b => b.text),
                            source_lang: SOURCE_LANG,
                            target_lang: TARGET_LANG,
                        }),
                    });

                    if (resp.ok) {
                        const data = await resp.json();
                        data.translations.forEach((tr, idx) => {
                            translatedParagraphs[toTranslate[idx].hash] = tr;
                        });
                        
                        // If they are currently visible, render them immediately
                        const batchElements = toTranslate.map(b => b.el);
                        if (translationMode === 'bilingual') {
                            showTranslationsBilingual(batchElements);
                        } else if (translationMode === 'translated') {
                            showTranslationsInline('translated', batchElements);
                        }
                    }
                } catch (e) {
                    console.error("Prefetch error:", e);
                }
            }
            
            // Sleep 800ms between prefetch requests to prevent API overload
            await new Promise(resolve => setTimeout(resolve, 800));
        }

        isPrefetching = false;
        updateLoadingIndicator();
    }

    // ── Rendering ──────────────────────────────────────────────────────
    function showTranslationsBilingual(paragraphs) {
        paragraphs.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            const translated = translatedParagraphs[hash];
            if (!translated || translated === text || translated.startsWith('[ERROR')) return;

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
            if (!translated || translated.startsWith('[ERROR')) return;

            if (!el.dataset.originalText) {
                el.dataset.originalText = el.textContent;
            }
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
    function startMutationObserver() {
        let translateTimeout = null;
        const observer = new MutationObserver((mutations) => {
            if (translationMode === 'off') return;
            let shouldTranslate = false;
            for (const m of mutations) {
                if (m.addedNodes.length > 0) {
                    shouldTranslate = true;
                    break;
                }
            }
            if (shouldTranslate) {
                clearTimeout(translateTimeout);
                translateTimeout = setTimeout(() => {
                    translateCurrentPage();
                }, 150);
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
                        console.log("[book-translator] Page change detected!");
                        translateCurrentPage();
                    }
                }
            }
        }, 400);
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
