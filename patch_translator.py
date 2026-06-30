import re

with open("static/translator.js", "r") as f:
    content = f.read()

# Replace strings block
content = re.sub(
    r'''        en: \{
            off: 'Original', bilingual: 'Bilingual', translated: 'Translated',
            translatingPage: 'Translating page…', translatingChapter: 'Preparing next text…', done: '✓ Ready', error: '⚠ Translation error — click to retry',''',
    r'''        en: {
            off: 'Original', bilingual: 'Bilingual', translated: 'Translated',
            translatingPage: 'Translating current page…', translatingChapter: 'Preparing next paragraphs…', done: '✓ Ready', error: '⚠ Error — click to retry',
            rateLimited: 'Rate limited — waiting {n}s…',
            retrying: 'Retrying…',''',
    content
)

# And similarly for other languages if necessary, or just rely on fallback to EN for missing keys.
content = re.sub(
    r'''        es: \{
            off: 'Original', bilingual: 'Bilingüe', translated: 'Traducido',
            translatingPage: 'Traduciendo página…', translatingChapter: 'Capítulo', done: '✓ Listo', error: '⚠ Error — reintentar',''',
    r'''        es: {
            off: 'Original', bilingual: 'Bilingüe', translated: 'Traducido',
            translatingPage: 'Traduciendo página actual…', translatingChapter: 'Preparando siguientes párrafos…', done: '✓ Listo', error: '⚠ Error — clic para reintentar',
            rateLimited: 'Límite alcanzado — esperando {n}s…',
            retrying: 'Reintentando…',''',
    content
)

# Insert queue variables
content = re.sub(
    r'''    let isTranslating = false;
    let prefetchQueue = \[\];
    let isPrefetching = false;
    let lastFirstVisibleHash = null;''',
    r'''    const BT_CLIENT_MAX_INFLIGHT = 1;
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
    let lastFirstVisibleHash = null;''',
    content, count=1
)

# Update newGeneration
content = re.sub(
    r'''    function newGeneration\(\) \{
        generation\+\+;
        for \(const c of activeControllers\) \{
            try \{ c\.abort\(\); \} catch \(e\) \{ /\* ignore \*/ \}
        \}
        activeControllers\.clear\(\);
        prefetchQueue = \[\];
        isTranslating = false;
        isPrefetching = false;''',
    r'''    function newGeneration() {
        generation++;
        for (const c of activeControllers) {
            try { c.abort(); } catch (e) { /* ignore */ }
        }
        activeControllers.clear();
        visibleQueue = [];
        prefetchQueue = [];
        isTranslating = false;
        isPrefetching = false;''',
    content
)

# Update refreshStatus
content = re.sub(
    r'''        let state = 'idle';
        if \(translationMode !== 'off'\) \{
            if \(errorCount >= 3\) \{
                state = 'error';
                text\.textContent = t\.error;
            \} else if \(isTranslating\) \{''',
    r'''        let state = 'idle';
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
            } else if (isTranslating) {''',
    content
)

# We also need to replace the entire Translation Engine section
# I'll use regex to match from `    // ── Translation engine ─────────────────────────────────────────────`
# to `    // ── Iframe styling`

engine_start = content.find("    // ── Translation engine")
engine_end = content.find("    // ── Iframe styling")

new_engine = """    // ── Translation engine ─────────────────────────────────────────────
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
                
                // Cleanup stale items
                visibleQueue = visibleQueue.filter(x => x.gen === generation && !translatedParagraphs[x.hash]);
                prefetchQueue = prefetchQueue.filter(x => x.gen === generation && !translatedParagraphs[x.hash]);
                
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

"""

content = content[:engine_start] + new_engine + content[engine_end:]

with open("static/translator.js", "w") as f:
    f.write(content)
