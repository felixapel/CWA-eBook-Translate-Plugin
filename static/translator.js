/**
 * book-translator — Calibre-Web-Automated Translation Overlay
 */

(function () {
    // ── Configuration ──────────────────────────────────────────────────
    // Optional overrides injected by the CWA template (window.BOOK_TRANSLATOR).
    // An empty/absent apiUrl falls back to dynamic host-based resolution so the
    // overlay keeps working when accessed over the LAN (not just localhost).
    const BT_UI_VERSION = '2026-06-30-opus-deploy-sync-v1';
    console.info(`[BookTranslator] loaded version ${BT_UI_VERSION}`);
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
    let prefetchQueue = [];
    let isPrefetching = false;
    let lastFirstVisibleHash = null;

    // UI / status state
    let prefetchEnabled = localStorage.getItem('bt_prefetch') !== '0'; // translate whole chapter ahead
    let chapterTotal = 0;     // paragraphs queued for the current chapter's background fill
    let errorCount = 0;       // consecutive failed requests (drives the error state)
    let doneHideTimer = null;

    // ── Persistent translation cache (survives page turns AND browser reloads) ──
    // Per-language map of contentHash -> translation, mirrored to localStorage so
    // the work/API cost already spent is never thrown away. (The backend also
    // caches in SQLite, so even a cleared client never re-pays for a paragraph.)
    const CACHE_PREFIX = 'bt_cache_v2_'; // v2: 53-bit hash keys (old caches ignored)
    const CACHE_MAX_ENTRIES = 5000;      // safety cap to stay under the localStorage quota

    function loadCacheForLang(lang) {
        try {
            const raw = localStorage.getItem(CACHE_PREFIX + lang);
            if (raw) return JSON.parse(raw) || {};
        } catch (e) { /* ignore corrupt/missing cache */ }
        return {};
    }

    let persistTimer = null;
    function schedulePersist() {
        if (persistTimer) return;
        persistTimer = setTimeout(persistCacheNow, 1500);
    }
    function persistCacheNow() {
        if (persistTimer) { clearTimeout(persistTimer); persistTimer = null; }
        try {
            let keys = Object.keys(translatedParagraphs);
            if (keys.length > CACHE_MAX_ENTRIES) {
                // Object string-keys keep insertion order: keep the most recent N.
                const trimmed = {};
                for (const k of keys.slice(keys.length - CACHE_MAX_ENTRIES)) trimmed[k] = translatedParagraphs[k];
                translatedParagraphs = trimmed;
            }
            localStorage.setItem(CACHE_PREFIX + TARGET_LANG, JSON.stringify(translatedParagraphs));
        } catch (e) {
            // Quota exceeded — drop the oldest half and retry once.
            try {
                const keys = Object.keys(translatedParagraphs);
                const trimmed = {};
                for (const k of keys.slice(Math.floor(keys.length / 2))) trimmed[k] = translatedParagraphs[k];
                translatedParagraphs = trimmed;
                localStorage.setItem(CACHE_PREFIX + TARGET_LANG, JSON.stringify(trimmed));
            } catch (e2) { /* give up persisting; in-memory cache still works */ }
        }
    }

    let translatedParagraphs = loadCacheForLang(TARGET_LANG); // hash -> text (restored from last session)

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
        refreshStatus();
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
        en: {
            off: 'Original', bilingual: 'Bilingual', translated: 'Translated',
            translatingPage: 'Translating page…', translatingChapter: 'Preparing next text…', done: '✓ Ready', error: '⚠ Translation error — click to retry',
            restoring: 'Restoring saved translations…',
            cycleHint: 'Click to cycle: Original → Bilingual → Translated', langHint: 'Target language', settings: 'Settings',
            prefetchWhole: 'Pre-translate whole chapter', clearLang: 'Clear this language\'s cache', clearAll: 'Clear all cache',
            cached: 'Cached', cleared: 'Cache cleared',
        },
        es: {
            off: 'Original', bilingual: 'Bilingüe', translated: 'Traducido',
            translatingPage: 'Traduciendo página…', translatingChapter: 'Capítulo', done: '✓ Listo', error: '⚠ Error — reintentar',
            cycleHint: 'Clic para cambiar: Original → Bilingüe → Traducido', langHint: 'Idioma destino', settings: 'Ajustes',
            prefetchWhole: 'Pre-traducir capítulo completo', clearLang: 'Borrar caché de este idioma', clearAll: 'Borrar toda la caché',
            cached: 'En caché', cleared: 'Caché borrada',
        },
        fr: {
            off: 'Original', bilingual: 'Bilingue', translated: 'Traduit',
            translatingPage: 'Traduction de la page…', translatingChapter: 'Chapitre', done: '✓ Terminé', error: '⚠ Erreur — réessayer',
            cycleHint: 'Cliquez pour changer : Original → Bilingue → Traduit', langHint: 'Langue cible', settings: 'Réglages',
            prefetchWhole: 'Pré-traduire tout le chapitre', clearLang: 'Vider le cache de cette langue', clearAll: 'Vider tout le cache',
            cached: 'En cache', cleared: 'Cache vidé',
        },
        de: {
            off: 'Original', bilingual: 'Zweisprachig', translated: 'Übersetzt',
            translatingPage: 'Seite wird übersetzt…', translatingChapter: 'Kapitel', done: '✓ Fertig', error: '⚠ Fehler — erneut',
            cycleHint: 'Klicken zum Wechseln: Original → Zweisprachig → Übersetzt', langHint: 'Zielsprache', settings: 'Einstellungen',
            prefetchWhole: 'Ganzes Kapitel vorübersetzen', clearLang: 'Cache dieser Sprache leeren', clearAll: 'Gesamten Cache leeren',
            cached: 'Im Cache', cleared: 'Cache geleert',
        },
        pt: {
            off: 'Original', bilingual: 'Bilíngue', translated: 'Traduzido',
            translatingPage: 'Traduzindo página…', translatingChapter: 'Capítulo', done: '✓ Pronto', error: '⚠ Erro — repetir',
            cycleHint: 'Clique para alternar: Original → Bilíngue → Traduzido', langHint: 'Idioma de destino', settings: 'Ajustes',
            prefetchWhole: 'Pré-traduzir capítulo inteiro', clearLang: 'Limpar cache deste idioma', clearAll: 'Limpar todo o cache',
            cached: 'Em cache', cleared: 'Cache limpo',
        },
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
    function setMode(mode, { silent = false } = {}) {
        const prevMode = translationMode;
        if (mode === prevMode) return;
        translationMode = mode;
        localStorage.setItem('bt_mode', mode);

        const bar = document.getElementById('bt-bar');
        if (bar) bar.dataset.mode = mode;
        const toggle = document.getElementById('bt-toggle-label');
        if (toggle) toggle.textContent = mode === 'bilingual' ? t.bilingual
            : mode === 'translated' ? t.translated : t.off;

        if (mode === 'off') {
            newGeneration();              // cancel in-flight work; next ON starts clean
            removeAllTranslations();
            refreshStatus();
            if (!silent) showToast(t.off);
        } else if (prevMode === 'off') {
            translateCurrentPage();       // fresh start
        } else {
            // bilingual <-> translated: re-render from cache instantly, keep filling gaps
            renderMode(getParagraphs());
            translateCurrentPage();
        }
    }

    function createFloatingUI() {
        if (document.getElementById('bt-bar')) return;

        const bar = document.createElement('div');
        bar.id = 'bt-bar';
        bar.dataset.mode = translationMode;
        bar.dataset.state = 'idle';

        // Build the language <option> list once.
        const langOptions = availableLangs.map(l =>
            `<option value="${l.code}"${l.code === TARGET_LANG ? ' selected' : ''}>${l.name}</option>`
        ).join('');

        bar.innerHTML =
            `<button id="bt-toggle" title="${t.cycleHint}">` +
                `<span class="bt-dot"></span>` +
                `<span id="bt-toggle-label">${translationMode === 'bilingual' ? t.bilingual : translationMode === 'translated' ? t.translated : t.off}</span>` +
            `</button>` +
            `<select id="bt-lang" title="${t.langHint}">${langOptions}</select>` +
            `<div id="bt-status">` +
                `<span id="bt-spinner"></span>` +
                `<span id="bt-status-text"></span>` +
            `</div>` +
            `<button id="bt-gear" title="${t.settings}" aria-label="${t.settings}">⚙</button>` +
            `<div id="bt-progress"><div id="bt-progress-fill"></div></div>` +
            `<div id="bt-menu"></div>`;

        document.body.appendChild(bar);

        document.getElementById('bt-toggle').onclick = () => {
            const next = translationMode === 'off' ? 'bilingual'
                : translationMode === 'bilingual' ? 'translated' : 'off';
            setMode(next);
        };

        const sel = document.getElementById('bt-lang');
        sel.onchange = (e) => {
            persistCacheNow();            // flush current language's cache before switching
            TARGET_LANG = e.target.value;
            localStorage.setItem('bt_lang', TARGET_LANG);
            newGeneration();              // abort in-flight old-language requests
            translatedParagraphs = loadCacheForLang(TARGET_LANG); // restore that language's work
            if (translationMode !== 'off') {
                removeAllTranslations();
                translateCurrentPage();
            }
            refreshStatus();
        };

        const gear = document.getElementById('bt-gear');
        gear.onclick = (e) => { e.stopPropagation(); toggleMenu(); };
        document.addEventListener('click', (e) => {
            const menu = document.getElementById('bt-menu');
            if (menu && menu.classList.contains('bt-open') && !bar.contains(e.target)) {
                menu.classList.remove('bt-open');
            }
        });

        // Click the error status to retry.
        document.getElementById('bt-status').onclick = () => {
            if (bar.dataset.state === 'error') {
                errorCount = 0;
                if (translationMode !== 'off') translateCurrentPage();
            }
        };

        buildMenu();
        refreshStatus();
    }

    function buildMenu() {
        const menu = document.getElementById('bt-menu');
        if (!menu) return;
        const entryCount = Object.keys(translatedParagraphs).length;
        menu.innerHTML =
            `<div class="bt-menu-item" data-action="prefetch">` +
                `<span>${t.prefetchWhole}</span>` +
                `<span class="bt-switch${prefetchEnabled ? ' bt-on' : ''}"></span>` +
            `</div>` +
            `<div class="bt-menu-sep"></div>` +
            `<div class="bt-menu-item" data-action="clear-lang"><span>${t.clearLang}</span></div>` +
            `<div class="bt-menu-item" data-action="clear-all"><span>${t.clearAll}</span></div>` +
            `<div class="bt-menu-sep"></div>` +
            `<div class="bt-menu-note">${t.cached}: ${entryCount} · ${TARGET_LANG}</div>` +
            `<div class="bt-menu-note">v${BT_UI_VERSION}</div>`;

        menu.querySelectorAll('.bt-menu-item').forEach(item => {
            item.onclick = (e) => {
                e.stopPropagation();
                const action = item.dataset.action;
                if (action === 'prefetch') {
                    prefetchEnabled = !prefetchEnabled;
                    localStorage.setItem('bt_prefetch', prefetchEnabled ? '1' : '0');
                    buildMenu();
                    if (prefetchEnabled && translationMode !== 'off') triggerPrefetch();
                } else if (action === 'clear-lang') {
                    translatedParagraphs = {};
                    try { localStorage.removeItem(CACHE_PREFIX + TARGET_LANG); } catch (e2) {}
                    showToast(t.cleared);
                    buildMenu();
                } else if (action === 'clear-all') {
                    translatedParagraphs = {};
                    try {
                        Object.keys(localStorage).filter(k => k.startsWith(CACHE_PREFIX))
                            .forEach(k => localStorage.removeItem(k));
                    } catch (e2) {}
                    showToast(t.cleared);
                    buildMenu();
                }
            };
        });
    }

    function toggleMenu() {
        const menu = document.getElementById('bt-menu');
        if (!menu) return;
        if (!menu.classList.contains('bt-open')) buildMenu();
        menu.classList.toggle('bt-open');
    }

    // Single source of truth for the status zone: derives display from state.
    function refreshStatus() {
        const bar = document.getElementById('bt-bar');
        const text = document.getElementById('bt-status-text');
        const fill = document.getElementById('bt-progress-fill');
        if (!bar || !text) return;

        if (doneHideTimer) { clearTimeout(doneHideTimer); doneHideTimer = null; }

        let state = 'idle';
        if (translationMode !== 'off') {
            if (errorCount >= 3) {
                state = 'error';
                text.textContent = t.error;
            } else if (isTranslating) {
                state = 'page';
                text.textContent = t.translatingPage;
            } else if (isPrefetching || prefetchQueue.length > 0) {
                state = 'chapter';
                const done = Math.max(0, chapterTotal - prefetchQueue.length);
                if (fill && chapterTotal > 0) fill.style.width = Math.round(done / chapterTotal * 100) + '%';
                text.textContent = `${t.translatingChapter} ${done}/${chapterTotal}`;
            } else if (chapterTotal > 0) {
                state = 'done';
                if (fill) fill.style.width = '100%';
                text.textContent = t.done;
                doneHideTimer = setTimeout(() => {
                    chapterTotal = 0;
                    const b = document.getElementById('bt-bar');
                    if (b && b.dataset.state === 'done') { b.dataset.state = 'idle'; }
                }, 2500);
            }
        }
        bar.dataset.state = state;
        if (fill && (state === 'idle' || state === 'page')) {
            // page state uses an indeterminate CSS animation; reset width otherwise
            if (state === 'idle') fill.style.width = '0%';
        }
    }

    // ── Toast Notifications ────────────────────────────────────────────
    function showToast(message) {
        let toast = document.getElementById('bt-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'bt-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        requestAnimationFrame(() => toast.classList.add('bt-toast-visible'));
        clearTimeout(toast._btHide);
        toast._btHide = setTimeout(() => toast.classList.remove('bt-toast-visible'), 2600);
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
        // Use a Set for O(1) ancestor lookups (was O(n²) via Array.includes — janky on big chapters).
        const filteredSet = new Set(filtered);
        return filtered.filter(el => {
            let parent = el.parentElement;
            while (parent) {
                if (filteredSet.has(parent)) {
                    return false; // Skip, parent will be translated
                }
                parent = parent.parentElement;
            }
            return true;
        });
    }

    function getVisibleParagraphs() {
        // Filter the SAME canonical, de-duplicated set used everywhere else, so
        // visible-first covers headings/lists too and the prefetch complement is
        // exact (no element falls through the cracks between the two selectors).
        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        const all = getParagraphs();
        if (!iframe || !iframe.contentDocument) {
            return all.slice(0, 5);
        }
        const iframeWidth = iframe.clientWidth || window.innerWidth;
        const iframeHeight = iframe.clientHeight || window.innerHeight;

        return all.filter(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;
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
        // cyrb53 — a 53-bit hash. The previous 32-bit hash could collide across a
        // long book and show the wrong cached translation for a paragraph.
        let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
        for (let i = 0; i < str.length; i++) {
            const ch = str.charCodeAt(i);
            h1 = Math.imul(h1 ^ ch, 2654435761);
            h2 = Math.imul(h2 ^ ch, 1597334677);
        }
        h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
        h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
        return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
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
            const headers = { 'Content-Type': 'application/json' };
            if (cfg.apiToken) headers['X-BT-Token'] = cfg.apiToken; // optional shared secret
            const resp = await fetch(`${TRANSLATOR_URL}/translate/batch`, {
                method: 'POST',
                headers,
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
                if (e.name !== 'AbortError') { console.error("Translation request failed:", e); errorCount++; refreshStatus(); }
                return; // network/abort/timeout — stop this run; a later trigger retries
            }
            if (myGen !== generation) return; // superseded — drop stale result
            if (!data || !Array.isArray(data.translations)) {
                errorCount++; refreshStatus();   // backend returned non-OK / unexpected payload
                return;
            }
            let stored = false, anyGood = false;
            data.translations.forEach((tr, idx) => {
                // Don't cache errors/empties so they retry on the next pass.
                if (!isBadTranslation(tr)) { translatedParagraphs[chunk[idx].hash] = tr; stored = true; anyGood = true; }
            });
            errorCount = anyGood ? 0 : errorCount + 1; // recover on first good chunk
            refreshStatus();
            renderMode(chunk.map(b => b.el));
            if (stored) schedulePersist(); // mirror to localStorage so work survives reloads
        }
    }

    async function translateCurrentPage() {
        if (isTranslating || translationMode === 'off') return;
        isTranslating = true;
        refreshStatus();
        const myGen = generation;

        // 1. CURRENT PAGE FIRST — progressive, so the first line shows quickly.
        const visibleEls = getVisibleParagraphs();
        const visibleUncached = collectUncached(visibleEls);
        if (visibleUncached.length > 0) {
            await translateElements(visibleUncached, myGen, VISIBLE_CHUNK);
        }
        // Paint any visible paragraphs that were already cached (revisited page).
        if (myGen === generation) renderMode(visibleEls);

        if (myGen !== generation || translationMode === 'off') {
            if (myGen === generation) isTranslating = false;
            refreshStatus();
            return;
        }

        // 2. REST OF CHAPTER — queue the background fill BEFORE clearing the
        // "translating" flag so the indicator stays on without flickering.
        // Skip entirely if the user turned off whole-chapter pre-translation.
        const visibleSet = new Set(visibleEls);
        prefetchQueue = prefetchEnabled ? getParagraphs().filter(el => !visibleSet.has(el)) : [];
        chapterTotal = prefetchQueue.length;
        isTranslating = false;
        refreshStatus();
        triggerPrefetch();
    }

    // ── Background Prefetching ─────────────────────────────────────────
    async function triggerPrefetch() {
        if (isPrefetching || prefetchQueue.length === 0) return;
        isPrefetching = true;
        refreshStatus();
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

            refreshStatus(); // live "remaining" count while filling the chapter
            await new Promise(resolve => setTimeout(resolve, PREFETCH_GAP_MS));
        }

        if (myGen === generation) isPrefetching = false;
        refreshStatus();
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
            // Styling lives in translator.css (.bt-translation) so the translation
            // colour adapts to the reader's light/dark/sepia theme.
            transEl.className = 'bt-translation';
            transEl.textContent = translated;
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

    let translateTimeout = null;
    let lastDocumentIdentity = null;
    let iframeObserver = null;
    let mainObserver = null;

    function scheduleTranslate(reason, { immediate = false, forceRediscover = false } = {}) {
        if (translationMode === 'off') return;
        
        if (forceRediscover) {
            newGeneration(); // Cancel stale work immediately if it's a chapter/page turn
            lastFirstVisibleHash = null; // force the detector to pick up the new page
        }

        clearTimeout(translateTimeout);
        if (immediate) {
            translateCurrentPage();
        } else {
            translateTimeout = setTimeout(() => {
                translateCurrentPage();
            }, 250);
        }
    }

    function setupObservers() {
        if (!mainObserver) {
            mainObserver = new MutationObserver((mutations) => {
                let shouldTranslate = false;
                for (const m of mutations) {
                    for (const n of m.addedNodes) {
                        if (!isBtNode(n)) { shouldTranslate = true; break; }
                    }
                    if (shouldTranslate) break;
                }
                if (shouldTranslate) scheduleTranslate('main_mutation');
            });
            mainObserver.observe(document.body, { childList: true, subtree: true });
        }

        // We check for iframe document changes or page turns
        setInterval(() => {
            if (translationMode === 'off') return;

            // 1. Iframe discovery and identity tracking
            const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
            if (iframe) {
                try {
                    const idoc = iframe.contentDocument || iframe.contentWindow.document;
                    if (idoc && idoc !== lastDocumentIdentity) {
                        lastDocumentIdentity = idoc;
                        
                        if (iframeObserver) iframeObserver.disconnect();
                        iframeObserver = new MutationObserver((mutations) => {
                            let shouldTranslate = false;
                            for (const m of mutations) {
                                for (const n of m.addedNodes) {
                                    if (!isBtNode(n)) { shouldTranslate = true; break; }
                                }
                                if (shouldTranslate) break;
                            }
                            if (shouldTranslate) scheduleTranslate('iframe_mutation');
                        });
                        
                        if (idoc.body) {
                            iframeObserver.observe(idoc.body, { childList: true, subtree: true });
                            scheduleTranslate('new_document', { immediate: true, forceRediscover: true });
                        }
                    }
                } catch (e) {}
            }

            // 2. Page turn detector
            const visible = getVisibleParagraphs();
            if (visible.length > 0) {
                const firstText = getParagraphText(visible[0]);
                if (firstText) {
                    const hash = hashText(firstText);
                    if (hash !== lastFirstVisibleHash) {
                        lastFirstVisibleHash = hash;
                        scheduleTranslate('page_turn', { immediate: true, forceRediscover: true });
                    }
                }
            }
        }, 350);
    }

    function attachEpubHooks() {
        if (window.reader && window.reader.rendition) {
            window.reader.rendition.on('relocated', () => {
                scheduleTranslate('epub_relocated', { immediate: true, forceRediscover: true });
            });
            window.reader.rendition.on('rendered', () => {
                scheduleTranslate('epub_rendered', { immediate: true, forceRediscover: true });
            });
        } else {
            setTimeout(attachEpubHooks, 1000);
        }
    }

    // ── Start ──────────────────────────────────────────────────────────
    function setupKeyboardShortcut() {
        // Alt+T cycles the mode (Ctrl/Cmd+T is reserved by the browser for new tabs).
        document.addEventListener('keydown', (e) => {
            if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === 't' || e.key === 'T')) {
                e.preventDefault();
                const next = translationMode === 'off' ? 'bilingual'
                    : translationMode === 'bilingual' ? 'translated' : 'off';
                setMode(next);
            }
        });
    }

    function init() {
        createFloatingUI();
        setupObservers();
        attachEpubHooks();
        setupKeyboardShortcut();
        // Persist any pending translations if the user closes/reloads the tab.
        window.addEventListener('beforeunload', persistCacheNow);
        // Brief version toast helps Felix confirm the correct JS is loaded after deploys.
        setTimeout(() => showToast(`BookTranslator ${BT_UI_VERSION}`), 1200);
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
