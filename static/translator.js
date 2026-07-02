/**
 * book-translator — Calibre-Web-Automated Translation Overlay
 */

(function () {
    'use strict';
    // ── Version & Telemetry ──────────────────────────────────────────
    const BT_UI_VERSION = '2.1.0';
    console.log(`[BookTranslator] loaded version ${BT_UI_VERSION}`);
    const cfg = (typeof window !== 'undefined' && window.BOOK_TRANSLATOR) || {};
    const TRANSLATOR_URL = (cfg.apiUrl && cfg.apiUrl.length)
        ? cfg.apiUrl
        : (window.location.protocol === 'https:' ? '' : `http://${window.location.hostname}:8390`);
    let SOURCE_LANG = cfg.sourceLang || 'English'; // Assume source is English

    // Map browser language codes to the full language name the backend expects
    // (used only to pick a sensible default target on first run).
    const langMap = {
        'es': 'Spanish', 'en': 'English', 'fr': 'French', 'de': 'German',
        'pt': 'Portuguese', 'it': 'Italian', 'ru': 'Russian', 'zh': 'Chinese',
        'ja': 'Japanese', 'hi': 'Hindi', 'ar': 'Arabic', 'bn': 'Bengali',
        'ur': 'Urdu', 'ko': 'Korean', 'tr': 'Turkish', 'pl': 'Polish',
        'nl': 'Dutch', 'uk': 'Ukrainian', 'vi': 'Vietnamese', 'th': 'Thai',
        'id': 'Indonesian', 'fa': 'Persian', 'he': 'Hebrew', 'el': 'Greek',
        'cs': 'Czech', 'sv': 'Swedish', 'da': 'Danish', 'fi': 'Finnish',
        'no': 'Norwegian', 'nb': 'Norwegian', 'hu': 'Hungarian',
        'ro': 'Romanian', 'ms': 'Malay', 'ta': 'Tamil', 'te': 'Telugu',
        'mr': 'Marathi', 'gu': 'Gujarati', 'pa': 'Punjabi', 'sw': 'Swahili',
        'tl': 'Tagalog', 'ca': 'Catalan', 'bg': 'Bulgarian', 'sk': 'Slovak',
        'sr': 'Serbian', 'hr': 'Croatian', 'sl': 'Slovenian', 'lt': 'Lithuanian',
        'lv': 'Latvian', 'et': 'Estonian'
    };

    const browserCode = (navigator.language || 'es').split('-')[0];
    const defaultLang = langMap[browserCode] || 'Spanish';
    let TARGET_LANG = localStorage.getItem('bt_lang') || cfg.targetLang || defaultLang;

    const BT_CLIENT_MAX_INFLIGHT = 1;
    const BT_CLIENT_MIN_REQUEST_GAP_MS = 500;
    const BT_CLIENT_RATE_LIMIT_BACKOFF_MS = 10000;

    let translationMode = localStorage.getItem('bt_mode') || 'off'; // 'off', 'bilingual', 'translated'
    let isTranslating = false;
    let isPrefetching = false;
    let visibleQueue = [];
    let prefetchQueue = [];
    let isPumpRunning = false;
    let rateLimitUntil = 0;
    let lastRequestEnd = 0;
    let lastFirstVisibleHash = null;
    let pendingFirstVisibleHash = null; // 2-poll debounce for the page-turn detector

    // UI / status state
    let prefetchEnabled = localStorage.getItem('bt_prefetch') !== '0'; // translate whole chapter ahead
    let chapterTotal = 0;     // paragraphs queued for the current chapter's background fill
    let errorCount = 0;       // consecutive failed requests (drives the error state)
    let doneHideTimer = null;
    let lastTriggerReason = 'init'; // why translateCurrentPage last ran (shown in the debug menu)

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
        visibleQueue = [];
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
            translatingPage: 'Translating…', translatingChapter: 'Chapter', done: '✓ Ready', error: '⚠ Retry',
            rateLimited: 'Waiting {n}s…',
            retrying: 'Retrying…',
            restoring: 'Restoring saved translations…',
            cycleHint: 'Click to cycle: Original → Bilingual → Translated', langHint: 'Target language', topLanguages: 'Most spoken', allLanguages: 'All languages (A–Z)', settings: 'Settings',
            prefetchWhole: 'Pre-translate whole chapter', clearLang: 'Clear this language\'s cache', clearAll: 'Clear all cache',
            cached: 'Cached', cleared: 'Cache cleared',
            bookTranslator: 'Book Translator', modeLabel: 'Mode', langLabel: 'Language',
            retryPage: 'Retry current page', debug: 'Debug',
            dbgQueue: 'Queue', dbgGen: 'Generation', dbgTrigger: 'Last trigger',
        },
        es: {
            off: 'Original', bilingual: 'Bilingüe', translated: 'Traducido',
            translatingPage: 'Traduciendo…', translatingChapter: 'Capítulo', done: '✓ Listo', error: '⚠ Reintentar',
            rateLimited: 'Esperando {n}s…',
            retrying: 'Reintentando…',
            cycleHint: 'Clic para cambiar: Original → Bilingüe → Traducido', langHint: 'Idioma destino', topLanguages: 'Más hablados', allLanguages: 'Todos los idiomas (A–Z)', settings: 'Ajustes',
            prefetchWhole: 'Pre-traducir capítulo completo', clearLang: 'Borrar caché de este idioma', clearAll: 'Borrar toda la caché',
            cached: 'En caché', cleared: 'Caché borrada',
            bookTranslator: 'Book Translator', modeLabel: 'Modo', langLabel: 'Idioma',
            retryPage: 'Reintentar página actual', debug: 'Depuración',
            dbgQueue: 'Cola', dbgGen: 'Generación', dbgTrigger: 'Último disparo',
        },
        fr: {
            off: 'Original', bilingual: 'Bilingue', translated: 'Traduit',
            translatingPage: 'Traduction…', translatingChapter: 'Chapitre', done: '✓ Terminé', error: '⚠ Réessayer',
            cycleHint: 'Cliquez pour changer : Original → Bilingue → Traduit', langHint: 'Langue cible', topLanguages: 'Les plus parlées', allLanguages: 'Toutes les langues (A–Z)', settings: 'Réglages',
            prefetchWhole: 'Pré-traduire tout le chapitre', clearLang: 'Vider le cache de cette langue', clearAll: 'Vider tout le cache',
            cached: 'En cache', cleared: 'Cache vidé',
        },
        de: {
            off: 'Original', bilingual: 'Zweisprachig', translated: 'Übersetzt',
            translatingPage: 'Übersetzen…', translatingChapter: 'Kapitel', done: '✓ Fertig', error: '⚠ Erneut',
            cycleHint: 'Klicken zum Wechseln: Original → Zweisprachig → Übersetzt', langHint: 'Zielsprache', topLanguages: 'Meistgesprochen', allLanguages: 'Alle Sprachen (A–Z)', settings: 'Einstellungen',
            prefetchWhole: 'Ganzes Kapitel vorübersetzen', clearLang: 'Cache dieser Sprache leeren', clearAll: 'Gesamten Cache leeren',
            cached: 'Im Cache', cleared: 'Cache geleert',
        },
        pt: {
            off: 'Original', bilingual: 'Bilíngue', translated: 'Traduzido',
            translatingPage: 'Traduzindo…', translatingChapter: 'Capítulo', done: '✓ Pronto', error: '⚠ Repetir',
            cycleHint: 'Clique para alternar: Original → Bilíngue → Traduzido', langHint: 'Idioma de destino', topLanguages: 'Mais falados', allLanguages: 'Todos os idiomas (A–Z)', settings: 'Ajustes',
            prefetchWhole: 'Pré-traduzir capítulo inteiro', clearLang: 'Limpar cache deste idioma', clearAll: 'Limpar todo o cache',
            cached: 'Em cache', cleared: 'Cache limpo',
        },
    };
    // English is the base; the locale (if any) overrides it, so menu-only keys
    // added only to `en` never come out undefined in another language.
    const t = Object.assign({}, strings.en, strings[browserCode] || {});

    // Language catalog. `code` is the English language name sent to the API
    // (and used as the cache key); `name` is the endonym shown in the picker.
    // The set mirrors the languages Gemma 4 (the default backend model) was
    // pre-trained on; the top-10 most spoken languages get their own group,
    // the rest are alphabetical. Native <select> gives type-to-search.
    // NOTE: must stay in sync with VALID_LANGUAGES in server.py (a test
    // asserts this).
    const TOP_LANGUAGES = [
        { code: 'English', name: 'English' },
        { code: 'Chinese', name: '中文' },
        { code: 'Hindi', name: 'हिन्दी' },
        { code: 'Spanish', name: 'Español' },
        { code: 'French', name: 'Français' },
        { code: 'Arabic', name: 'العربية' },
        { code: 'Bengali', name: 'বাংলা' },
        { code: 'Portuguese', name: 'Português' },
        { code: 'Russian', name: 'Русский' },
        { code: 'Urdu', name: 'اردو' }
    ];

    const MORE_LANGUAGES = [
        { code: 'Afrikaans', name: 'Afrikaans' },
        { code: 'Albanian', name: 'Shqip' },
        { code: 'Amharic', name: 'አማርኛ' },
        { code: 'Aymara', name: 'Aymar aru' },
        { code: 'Basque', name: 'Euskara' },
        { code: 'Bosnian', name: 'Bosanski' },
        { code: 'Bulgarian', name: 'Български' },
        { code: 'Burmese', name: 'မြန်မာ' },
        { code: 'Catalan', name: 'Català' },
        { code: 'Cebuano', name: 'Cebuano' },
        { code: 'Chewa', name: 'Chichewa' },
        { code: 'Chinese (Traditional)', name: '中文（繁體）' },
        { code: 'Croatian', name: 'Hrvatski' },
        { code: 'Czech', name: 'Čeština' },
        { code: 'Danish', name: 'Dansk' },
        { code: 'Dutch', name: 'Nederlands' },
        { code: 'Esperanto', name: 'Esperanto' },
        { code: 'Estonian', name: 'Eesti' },
        { code: 'Finnish', name: 'Suomi' },
        { code: 'Gaelic', name: 'Gàidhlig' },
        { code: 'Galician', name: 'Galego' },
        { code: 'Ganda', name: 'Luganda' },
        { code: 'German', name: 'Deutsch' },
        { code: 'Greek', name: 'Ελληνικά' },
        { code: 'Guarani', name: 'Avañe\'ẽ' },
        { code: 'Gujarati', name: 'ગુજરાતી' },
        { code: 'Hausa', name: 'Hausa' },
        { code: 'Hawaiian', name: 'ʻŌlelo Hawaiʻi' },
        { code: 'Hebrew', name: 'עברית' },
        { code: 'Hungarian', name: 'Magyar' },
        { code: 'Icelandic', name: 'Íslenska' },
        { code: 'Igbo', name: 'Igbo' },
        { code: 'Indonesian', name: 'Bahasa Indonesia' },
        { code: 'Italian', name: 'Italiano' },
        { code: 'Japanese', name: '日本語' },
        { code: 'Javanese', name: 'Basa Jawa' },
        { code: 'Kannada', name: 'ಕನ್ನಡ' },
        { code: 'Kazakh', name: 'Қазақша' },
        { code: 'Khmer', name: 'ខ្មែរ' },
        { code: 'Korean', name: '한국어' },
        { code: 'Kyrgyz', name: 'Кыргызча' },
        { code: 'Lao', name: 'ລາວ' },
        { code: 'Latin', name: 'Latina' },
        { code: 'Latvian', name: 'Latviešu' },
        { code: 'Lingala', name: 'Lingála' },
        { code: 'Lithuanian', name: 'Lietuvių' },
        { code: 'Macedonian', name: 'Македонски' },
        { code: 'Maithili', name: 'मैथिली' },
        { code: 'Malagasy', name: 'Malagasy' },
        { code: 'Malay', name: 'Bahasa Melayu' },
        { code: 'Malayalam', name: 'മലയാളം' },
        { code: 'Maori', name: 'Te Reo Māori' },
        { code: 'Marathi', name: 'मराठी' },
        { code: 'Mongolian', name: 'Монгол' },
        { code: 'Nahuatl', name: 'Nāhuatl' },
        { code: 'Navajo', name: 'Diné bizaad' },
        { code: 'Nepali', name: 'नेपाली' },
        { code: 'Norwegian', name: 'Norsk' },
        { code: 'Odia', name: 'ଓଡ଼ିଆ' },
        { code: 'Oromo', name: 'Afaan Oromoo' },
        { code: 'Pashto', name: 'پښتو' },
        { code: 'Persian', name: 'فارسی' },
        { code: 'Polish', name: 'Polski' },
        { code: 'Punjabi', name: 'ਪੰਜਾਬੀ' },
        { code: 'Quechua', name: 'Runa Simi' },
        { code: 'Romanian', name: 'Română' },
        { code: 'Samoan', name: 'Gagana Samoa' },
        { code: 'Serbian', name: 'Српски' },
        { code: 'Shona', name: 'chiShona' },
        { code: 'Sindhi', name: 'سنڌي' },
        { code: 'Sinhala', name: 'සිංහල' },
        { code: 'Slovak', name: 'Slovenčina' },
        { code: 'Slovenian', name: 'Slovenščina' },
        { code: 'Somali', name: 'Soomaali' },
        { code: 'Sundanese', name: 'Basa Sunda' },
        { code: 'Swahili', name: 'Kiswahili' },
        { code: 'Swedish', name: 'Svenska' },
        { code: 'Tagalog', name: 'Tagalog' },
        { code: 'Tajik', name: 'Тоҷикӣ' },
        { code: 'Tamil', name: 'தமிழ்' },
        { code: 'Telugu', name: 'తెలుగు' },
        { code: 'Thai', name: 'ไทย' },
        { code: 'Tibetan', name: 'བོད་སྐད' },
        { code: 'Turkish', name: 'Türkçe' },
        { code: 'Turkmen', name: 'Türkmençe' },
        { code: 'Ukrainian', name: 'Українська' },
        { code: 'Uzbek', name: 'Oʻzbekcha' },
        { code: 'Vietnamese', name: 'Tiếng Việt' },
        { code: 'Welsh', name: 'Cymraeg' },
        { code: 'Xhosa', name: 'isiXhosa' },
        { code: 'Yoruba', name: 'Yorùbá' },
        { code: 'Zulu', name: 'isiZulu' }
    ];

    const availableLangs = TOP_LANGUAGES.concat(MORE_LANGUAGES);

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

        // Build the language <option> list once: top-10 most spoken first,
        // then every other supported language A-Z. Native <select> provides
        // type-to-search within the open dropdown.
        const opt = l =>
            `<option value="${l.code}"${l.code === TARGET_LANG ? ' selected' : ''}>${l.name}</option>`;
        const langOptions =
            `<optgroup label="${t.topLanguages}">${TOP_LANGUAGES.map(opt).join('')}</optgroup>` +
            `<optgroup label="${t.allLanguages}">${MORE_LANGUAGES.map(opt).join('')}</optgroup>`;

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
            `<div id="bt-progress"><div id="bt-progress-fill"></div></div>`;

        document.body.appendChild(bar);

        // The settings popover lives at body level (NOT inside #bt-bar) because the
        // bar uses overflow:hidden to clip the progress bar, which would also clip
        // a child popover — that's why the gear "did nothing" before.
        const menu = document.createElement('div');
        menu.id = 'bt-menu';
        document.body.appendChild(menu);

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
        // Close on outside click (anywhere not on the bar or the menu)...
        document.addEventListener('click', (e) => {
            if (menu.classList.contains('bt-open') && !bar.contains(e.target) && !menu.contains(e.target)) {
                closeMenu();
            }
        });
        // ...and on Escape.
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && menu.classList.contains('bt-open')) closeMenu();
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
        const modeLabel = t[translationMode] || translationMode;
        const esc = (s) => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
        menu.innerHTML =
            `<div class="bt-menu-header">${t.bookTranslator}<span class="bt-menu-ver">v${BT_UI_VERSION}</span></div>` +
            `<div class="bt-menu-row"><span>${t.modeLabel}</span><span class="bt-menu-val">${modeLabel}</span></div>` +
            `<div class="bt-menu-row"><span>${t.langLabel}</span><span class="bt-menu-val">${TARGET_LANG}</span></div>` +
            `<div class="bt-menu-sep"></div>` +
            `<div class="bt-menu-item" data-action="prefetch">` +
                `<span>${t.prefetchWhole}</span>` +
                `<span class="bt-switch${prefetchEnabled ? ' bt-on' : ''}"></span>` +
            `</div>` +
            `<div class="bt-menu-item" data-action="retry"><span>↻ ${t.retryPage}</span></div>` +
            `<div class="bt-menu-item" data-action="clear-lang"><span>${t.clearLang}</span></div>` +
            `<div class="bt-menu-item" data-action="clear-all"><span>${t.clearAll}</span></div>` +
            `<div class="bt-menu-sep"></div>` +
            `<div class="bt-menu-note">${t.cached}: ${entryCount} · ${esc(TARGET_LANG)}</div>` +
            `<div class="bt-menu-note">${t.debug}: ${t.dbgQueue} ${prefetchQueue.length} · ${t.dbgGen} ${generation} · ${t.dbgTrigger} ${esc(lastTriggerReason)}</div>`;

        menu.querySelectorAll('.bt-menu-item').forEach(item => {
            item.onclick = (e) => {
                e.stopPropagation();
                const action = item.dataset.action;
                if (action === 'prefetch') {
                    prefetchEnabled = !prefetchEnabled;
                    localStorage.setItem('bt_prefetch', prefetchEnabled ? '1' : '0');
                    buildMenu();
                    if (prefetchEnabled && translationMode !== 'off') triggerPrefetch();
                } else if (action === 'retry') {
                    errorCount = 0;
                    closeMenu();
                    if (translationMode !== 'off') scheduleTranslate('manual_retry', { immediate: true, forceRediscover: true });
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

    function closeMenu() {
        const menu = document.getElementById('bt-menu');
        if (menu) menu.classList.remove('bt-open');
    }

    function toggleMenu() {
        const menu = document.getElementById('bt-menu');
        if (!menu) return;
        if (!menu.classList.contains('bt-open')) buildMenu(); // refresh snapshot (mode/queue/gen)
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
            const now = Date.now();
            if (rateLimitUntil > now) {
                state = 'ratelimit';
                const left = Math.ceil((rateLimitUntil - now) / 1000);
                text.textContent = (t.rateLimited || strings.en.rateLimited).replace('{n}', left);
                // Ensure UI updates countdown
                if (!window.btRateLimitTimer) {
                    window.btRateLimitTimer = setInterval(() => {
                        if (Date.now() > rateLimitUntil) { clearInterval(window.btRateLimitTimer); window.btRateLimitTimer = null; }
                        refreshStatus();
                    }, 1000);
                }
            } else if (errorCount > 0 && errorCount < 3) {
                state = 'page';
                text.textContent = t.retrying || strings.en.retrying;
            } else if (errorCount >= 3) {
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
    function getReaderDoc() {
        const iframe = document.querySelector('#viewer iframe, .epub-container iframe, iframe');
        if (iframe) {
            try { return iframe.contentDocument || iframe.contentWindow.document; } catch (e) { return null; }
        }
        return null;
    }

    const HEADING_CLASS_RE = /title|subtitle|chapter|heading|epigraph/i;

    function isHeading(el) {
        if (/^h[1-6]$/i.test(el.tagName)) return true;
        const c = (el.getAttribute && el.getAttribute('class')) || '';
        return HEADING_CLASS_RE.test(c);
    }

    function isCentered(el) {
        try {
            const win = el.ownerDocument.defaultView || window;
            return win.getComputedStyle(el).textAlign === 'center';
        } catch (e) { return false; }
    }

    function isPluginNode(el) {
        return !!(el.closest && el.closest('#bt-bar, #bt-menu, #bt-toast'))
            || (el.classList && (el.classList.contains('bt-translation') || el.classList.contains('bt-loading')));
    }

    // Canonical, de-duplicated set of translatable elements in a given document.
    function getTranslatableElements(doc) {
        if (!doc) return [];
        const rawElements = Array.from(doc.querySelectorAll(
            'p, blockquote, li, td, h1, h2, h3, h4, h5, h6, div.calibre1, div.text, a, ' +
            '[class*="title"], [class*="subtitle"], [class*="chapter"], [class*="author"], ' +
            '[class*="heading"], [class*="epigraph"], [class*="quote"], [class*="verse"]'
        ));

        // 1. Filter for content, layout, and exclusions.
        const filtered = rawElements.filter(el => {
            if (isPluginNode(el)) return false;                 // never translate our own UI
            const text = el.textContent.trim();
            if (text.length < 2) return false;

            const tagName = el.tagName.toLowerCase();

            if (tagName === 'a') {
                // Only standalone links (e.g. TOC entries); skip links inside prose.
                if (el.closest('p, div.calibre1, div.text, blockquote, li')) return false;
                return true;
            }

            // Blocks containing a link: let the link translate itself (keeps it clickable).
            if (['li', 'div', 'td'].includes(tagName) && el.querySelector('a')) return false;

            // Containers holding other block children: translate the children, not the wrapper.
            if (['div', 'blockquote', 'li', 'td'].includes(tagName)
                && el.querySelector('p, h1, h2, h3, h4, h5, h6, li, blockquote')) return false;

            return true;
        });

        // 2. De-duplicate hierarchy via a Set (O(n)): skip a child if an ancestor is
        // already selected, so we translate the logical block once.
        const filteredSet = new Set(filtered);
        return filtered.filter(el => {
            let parent = el.parentElement;
            while (parent) {
                if (filteredSet.has(parent)) return false;
                parent = parent.parentElement;
            }
            return true;
        });
    }

    function getParagraphs() {
        return getTranslatableElements(getReaderDoc() || document);
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
    const VISIBLE_CHUNK = 1;       // paragraphs per request for the on-screen page
    const PREFETCH_CHUNK = 3;      // paragraphs per request for background fill
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
            if (!resp.ok) {
                if (resp.status === 429) {
                    let r = {};
                    try { r = await resp.json(); } catch(e) {}
                    let after = r.retry_after || parseInt(resp.headers.get('Retry-After')) || (BT_CLIENT_RATE_LIMIT_BACKOFF_MS / 1000);
                    return { error: 'rate_limited', retry_after: after };
                }
                return null;
            }
            return await resp.json();
        } finally {
            clearTimeout(timer);
            activeControllers.delete(controller);
        }
    }

    async function pumpQueue() {
        if (isPumpRunning) return;
        isPumpRunning = true;
        
        try {
            while (translationMode !== 'off') {
                const now = Date.now();
                if (rateLimitUntil > now) {
                    refreshStatus();
                    await new Promise(r => setTimeout(r, Math.min(1000, rateLimitUntil - now)));
                    continue;
                }
                
                const gap = BT_CLIENT_MIN_REQUEST_GAP_MS - (now - lastRequestEnd);
                if (gap > 0) {
                    await new Promise(r => setTimeout(r, gap));
                    continue;
                }
                
                // Cleanup stale items and deduplicate
                const seenHash = new Set();
                visibleQueue = visibleQueue.filter(x => {
                    if (x.gen !== generation || translatedParagraphs[x.hash] || seenHash.has(x.hash)) return false;
                    seenHash.add(x.hash);
                    return true;
                });
                prefetchQueue = prefetchQueue.filter(x => {
                    if (x.gen !== generation || translatedParagraphs[x.hash] || seenHash.has(x.hash)) return false;
                    seenHash.add(x.hash);
                    return true;
                });
                
                if (visibleQueue.length === 0 && prefetchQueue.length === 0) {
                    break; // Nothing to do
                }
                
                let isVisible = false;
                let batch = [];
                if (visibleQueue.length > 0) {
                    batch = visibleQueue.slice(0, VISIBLE_CHUNK);
                    visibleQueue = visibleQueue.slice(VISIBLE_CHUNK);
                    isVisible = true;
                } else {
                    batch = prefetchQueue.slice(0, PREFETCH_CHUNK);
                    prefetchQueue = prefetchQueue.slice(PREFETCH_CHUNK);
                }
                
                isTranslating = isVisible;
                isPrefetching = !isVisible;
                refreshStatus();
                
                let data = null;
                try {
                    data = await postBatch(batch.map(b => b.text));
                } catch (e) {
                    if (e.name !== 'AbortError') { 
                        console.error("Translation request failed:", e); 
                        errorCount++; 
                    }
                    lastRequestEnd = Date.now();
                    continue;
                }
                
                lastRequestEnd = Date.now();
                
                if (data && data.error === 'rate_limited') {
                    rateLimitUntil = Date.now() + (data.retry_after * 1000);
                    // Put the batch back at the front of the corresponding queue
                    if (isVisible) visibleQueue.unshift(...batch);
                    else prefetchQueue.unshift(...batch);
                    // errorCount not incremented for rate limit
                    continue;
                }
                
                if (!data || !Array.isArray(data.translations)) {
                    errorCount++;
                    refreshStatus();
                    continue;
                }
                
                let stored = false, anyGood = false;
                data.translations.forEach((tr, idx) => {
                    if (!isBadTranslation(tr)) { 
                        translatedParagraphs[batch[idx].hash] = tr; 
                        stored = true; 
                        anyGood = true; 
                    }
                });
                
                errorCount = anyGood ? 0 : errorCount + 1;
                refreshStatus();
                
                if (stored) {
                    schedulePersist();
                    if (isVisible && batch[0].gen === generation) {
                        renderMode(batch.map(b => b.el));
                    }
                }
            }
        } finally {
            isPumpRunning = false;
            isTranslating = false;
            isPrefetching = false;
            refreshStatus();
        }
    }

    async function translateCurrentPage() {
        if (translationMode === 'off') return;
        
        const myGen = generation;
        const idoc = getReaderDoc();
        if (idoc) { ensureIframeStyles(idoc); applyIframeTheme(idoc); }

        const visibleEls = getVisibleParagraphs();
        
        // Paint any visible paragraphs that were already cached (revisited page).
        renderMode(visibleEls);

        visibleQueue = collectUncached(visibleEls).map(x => ({...x, gen: myGen}));
        
        const visibleSet = new Set(visibleEls);
        const prefetchEls = prefetchEnabled ? getParagraphs().filter(el => !visibleSet.has(el)) : [];
        prefetchQueue = collectUncached(prefetchEls).map(x => ({...x, gen: myGen}));
        chapterTotal = prefetchQueue.length;
        
        refreshStatus();
        pumpQueue();
    }

    function triggerPrefetch() {
        if (!prefetchEnabled || translationMode === 'off') return;
        pumpQueue();
    }

    // ── Iframe styling (parent-page CSS does not cascade into the EPUB iframe) ──
    const IFRAME_STYLE_ID = 'bt-injected-styles';
    const IFRAME_CSS = `
:root,html{--bt-translation-color:#1565c0;--bt-translation-border:#90caf9;--bt-translation-bg:rgba(21,101,192,0.06);}
html[data-bt-theme="dark"]{--bt-translation-color:#8ec0f9;--bt-translation-border:#1976d2;--bt-translation-bg:rgba(142,192,249,0.10);}
html[data-bt-theme="sepia"]{--bt-translation-color:#6d4c41;--bt-translation-border:#a1887f;--bt-translation-bg:rgba(109,76,65,0.08);}
.bt-translation{display:block;margin:0.5em 0 0.25em;padding:0.15em 0 0.15em 0.7em;border-left:3px solid var(--bt-translation-border);background:var(--bt-translation-bg);color:var(--bt-translation-color)!important;font-style:italic;font-weight:normal;line-height:1.5;}
.bt-heading-translation{border-left:none;background:transparent;padding-left:0;font-size:0.72em;opacity:0.92;margin-top:0.3em;break-inside:avoid;page-break-inside:avoid;}
.bt-center{text-align:center;}
.bt-loading{opacity:0.6;font-style:italic;}
`;

    function ensureIframeStyles(idoc) {
        try {
            if (!idoc || idoc.getElementById(IFRAME_STYLE_ID)) return;
            const style = idoc.createElement('style');
            style.id = IFRAME_STYLE_ID;
            style.textContent = IFRAME_CSS;
            (idoc.head || idoc.documentElement).appendChild(style);
        } catch (e) { /* cross-origin or detached doc — ignore */ }
    }

    function applyIframeTheme(idoc) {
        try {
            if (!idoc || !idoc.body) return;
            const win = idoc.defaultView || window;
            const m = (win.getComputedStyle(idoc.body).backgroundColor || '').match(/\d+/g);
            let theme = 'light';
            if (m && m.length >= 3) {
                const [r, g, b] = m.map(Number);
                const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
                if (lum < 110) theme = 'dark';
                else if (r >= g && g > b && (r - b) > 12) theme = 'sepia';
            }
            idoc.documentElement.dataset.btTheme = theme;
        } catch (e) { /* ignore */ }
    }

    // ── Rendering ──────────────────────────────────────────────────────
    function showTranslationsBilingual(paragraphs) {
        paragraphs.forEach((el) => {
            const text = getParagraphText(el);
            if (!text) return;
            const hash = hashText(text);
            const translated = translatedParagraphs[hash];
            if (isBadTranslation(translated) || translated === text) return;

            // If this element was previously inline-translated, restore the clean
            // original first so we never stack a bilingual block onto replaced text.
            if (el.dataset.originalText !== undefined) {
                el.textContent = el.dataset.originalText;
                delete el.dataset.originalText;
            }

            // Idempotent: update the existing direct-child translation instead of duplicating.
            let transEl = el.querySelector(':scope > .bt-translation');
            if (transEl) { transEl.textContent = translated; return; }

            const heading = isHeading(el);
            transEl = el.ownerDocument.createElement(heading ? 'div' : 'span');
            transEl.className = 'bt-translation ' + (heading ? 'bt-heading-translation' : 'bt-translation-bilingual');
            if (heading && isCentered(el)) transEl.className += ' bt-center';
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
        lastTriggerReason = reason;

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
                            ensureIframeStyles(idoc);   // inject our CSS into the new chapter doc
                            applyIframeTheme(idoc);
                            iframeObserver.observe(idoc.body, { childList: true, subtree: true });
                            scheduleTranslate('new_document', { immediate: true, forceRediscover: true });
                        }
                    }
                } catch (e) {}
            }

            // 2. Page turn detector.
            // BUG (root cause of the status bar flicker): inserting a bilingual
            // translation block under a paragraph increases that paragraph's
            // rendered height, which reflows the layout and can shift WHICH
            // paragraph counts as "first visible" — with no real page turn.
            // That false positive used to call scheduleTranslate(forceRediscover:true)
            // unconditionally, which hides the status pill (newGeneration resets
            // isTranslating/isPrefetching -> refreshStatus) and immediately shows
            // it again (translateCurrentPage sets isTranslating=true ->
            // refreshStatus) in the same tick. Because our own rendering keeps
            // shifting the layout throughout an active translation pass, this
            // repeated every ~350ms poll for as long as work was in progress —
            // the pill blinking on/off is that hide+show cycle repeating.
            //
            // Fix: only poll for page turns while genuinely idle (no
            // translation/prefetch in flight), so our own layout shifts can't
            // feed back into this detector. Real navigation while work is in
            // flight is still caught immediately via the epub.js relocated/
            // rendered hooks below (attachEpubHooks), which don't depend on
            // visual position at all. Also require the new position to be seen
            // on two consecutive polls (~700ms apart) before accepting it, as a
            // second line of defense against any other transient layout blip.
            if (!isTranslating && !isPrefetching) {
                const visible = getVisibleParagraphs();
                if (visible.length > 0) {
                    const firstText = getParagraphText(visible[0]);
                    if (firstText) {
                        const hash = hashText(firstText);
                        if (hash !== lastFirstVisibleHash) {
                            if (hash === pendingFirstVisibleHash) {
                                // Seen on the previous poll too — confirmed, not a blip.
                                lastFirstVisibleHash = hash;
                                pendingFirstVisibleHash = null;
                                scheduleTranslate('page_turn', { immediate: true, forceRediscover: true });
                            } else {
                                pendingFirstVisibleHash = hash;
                            }
                        } else {
                            pendingFirstVisibleHash = null;
                        }
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
