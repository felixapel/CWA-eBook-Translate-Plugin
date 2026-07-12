const fs = require('fs');
const assert = require('assert');
const jsdom = require("jsdom");
const { JSDOM } = jsdom;

const code = fs.readFileSync('static/translator.js', 'utf-8');
const loaderCode = fs.readFileSync('static/loader.js', 'utf-8');

assert(!/getItem\(['"]bt_token['"]\)/.test(loaderCode),
    'The proxy loader must never recover a JavaScript-readable API token from localStorage');
assert(/apiToken:\s*existing\.apiToken\s*\|\|\s*''/.test(loaderCode),
    'Token compatibility must require explicit trusted bootstrap configuration');
assert(/authMode:\s*existing\.authMode\s*\|\|\s*\(existing\.apiToken\s*\?\s*'token'\s*:\s*'cwa_session'\)/.test(loaderCode),
    'The proxy loader must declare the browser authentication transport');
assert(/AUTH_MODE\s*===\s*'token'\s*&&\s*cfg\.apiToken/.test(code),
    'The browser must send X-BT-Token only in explicit token mode');
assert(/AUTH_MODE\s*===\s*'cwa_session'[\s\S]{0,160}?['"]omit['"]/.test(code),
    'Only cwa_session mode may attach browser cookies; other modes must omit them');

let fetchCalls = [];
let fetchResponses = [];
let activeFetches = 0;
let maxActiveFetches = 0;

global.fetch = async (url, options) => {
    activeFetches++;
    if (activeFetches > maxActiveFetches) {
        maxActiveFetches = activeFetches;
    }
    fetchCalls.push({url, options, time: Date.now()});
    
    // Simulate network delay
    await new Promise(r => setTimeout(r, 50));
    
    const nextResp = fetchResponses.shift();
    activeFetches--;
    
    if (nextResp instanceof Error) throw nextResp;
    if (typeof nextResp === 'function') return nextResp();
    
    return {
        ok: nextResp ? nextResp.status < 400 : false,
        status: nextResp ? nextResp.status : 500,
        json: async () => nextResp.body,
        headers: { get: (k) => nextResp.headers?.[k] }
    };
};

const dom = new JSDOM(`
<!DOCTYPE html>
<html>
<body>
    <div id="viewer">
        <iframe></iframe>
    </div>
</body>
</html>
`, {
    url: "http://localhost/",
    runScripts: "dangerously"
});

const iframeDoc = dom.window.document.querySelector("iframe").contentDocument;
// The paragraphs live inside a <section class="chapter"> wrapper — the shape
// Calibre-converted epubs actually ship. The wrapper matches the
// [class*="chapter"] candidate selector; a regression here means the whole
// chapter gets translated as ONE mega-block (seen in production, v2.1.1).
// The fetch-order assertions below double as the regression test: with the
// wrapper bug, the first fetched "paragraph" would be the concatenated text.
iframeDoc.body.innerHTML = `
  <section class="chapter">
    <p class="calibre1">visible 1</p>
    <p class="calibre1">visible 2</p>
    <p class="calibre1">prefetch 1</p>
    <p class="calibre1">prefetch 2</p>
    <p class="calibre1">prefetch 3</p>
    <p class="calibre1">prefetch 4</p>
  </section>
`;

iframeDoc.querySelectorAll('p').forEach((p, idx) => {
    p.getBoundingClientRect = () => ({
        width: 100, height: 20,
        left: 0, top: idx < 2 ? 0 : 1000 // First two are visible
    });
});
iframeDoc.querySelector('section').getBoundingClientRect = () => ({
    width: 100, height: 2000, left: 0, top: 0 // wrapper is "visible" too — worst case
});
dom.window.innerWidth = 800;
dom.window.innerHeight = 600;
dom.window.localStorage.setItem('bt_mode', 'translated');
dom.window.localStorage.setItem('bt_prefetch', '1');
dom.window.localStorage.setItem('bt_lang', 'English');

dom.window.requestAnimationFrame = (cb) => setTimeout(cb, 16);
dom.window.fetch = global.fetch;

const scriptEl = dom.window.document.createElement("script");
scriptEl.textContent = code;
dom.window.document.body.appendChild(scriptEl);

async function wait(ms) {
    return new Promise(r => setTimeout(r, ms));
}

async function captureAuthTransport(config) {
    const authDom = new JSDOM(`
<!DOCTYPE html><html><body><div id="viewer"><iframe></iframe></div></body></html>
`, { url: 'http://reader.example.test/read/1', runScripts: 'dangerously' });
    authDom.window.BOOK_TRANSLATOR = Object.assign({ apiUrl: '/bt-api' }, config);
    authDom.window.localStorage.setItem('bt_mode', 'translated');
    authDom.window.localStorage.setItem('bt_prefetch', '0');
    authDom.window.localStorage.setItem('bt_lang', 'Spanish');
    authDom.window.requestAnimationFrame = (cb) => setTimeout(cb, 0);

    const authDoc = authDom.window.document.querySelector('iframe').contentDocument;
    authDoc.body.innerHTML = '<p>credential transport probe</p>';
    authDoc.querySelector('p').getBoundingClientRect = () => ({
        width: 100, height: 20, left: 0, top: 0
    });

    let captured = null;
    authDom.window.fetch = async (url, options) => {
        captured = captured || { url, options };
        const count = JSON.parse(options.body).paragraphs.length;
        return {
            ok: true,
            status: 200,
            json: async () => ({ translations: Array(count).fill('translated') }),
            headers: { get: () => null }
        };
    };

    const authScript = authDom.window.document.createElement('script');
    authScript.textContent = code;
    authDom.window.document.body.appendChild(authScript);
    const deadline = Date.now() + 2000;
    while (!captured && Date.now() < deadline) await wait(20);
    authDom.window.close();
    assert(captured, `Expected a request for auth mode ${config.authMode}`);
    return captured.options;
}

async function runTest() {
    console.log("Starting frontend assertions test...");
    
    // 1. Initial page load will trigger visible queue (1 chunk) then prefetch queue (3 chunks).
    // Let's provide a 429 response first for the visible request.
    fetchResponses.push({
        status: 429,
        body: { error: 'rate_limited', retry_after: 1 } // wait 1s
    });
    
    await wait(800);
    
    // Wait for the UI state to update after 429
    const btBar = dom.window.document.getElementById('bt-bar');
    const statusText = dom.window.document.getElementById('bt-status-text').textContent;
    
    console.log("Status text after 429:", statusText);
    assert(btBar.dataset.state === 'ratelimit', 'State should be ratelimit');
    // Copy is intentionally short (see i18n): "Waiting {n}s…". The point of this
    // assertion is that a 429 shows a benign waiting state, not a fatal error —
    // matched via the ratelimit state above plus the countdown text below.
    assert(/waiting/i.test(statusText) && !statusText.includes('Error'), 'Should show a benign waiting message, not a fatal error');
    assert(maxActiveFetches <= 1, 'Only one fetch active at a time');
    
    // Provide a valid response for when it resumes (for visible 1)
    fetchResponses.push({
        status: 200,
        body: { translations: ["Translated visible 1"] }
    });
    // Provide a valid response for visible 2
    fetchResponses.push({
        status: 200,
        body: { translations: ["Translated visible 2"] }
    });
    
    // Provide responses for the remaining prefetch blocks
    fetchResponses.push({
        status: 200,
        body: { translations: ["Translated prefetch 1", "Translated prefetch 2", "Translated prefetch 3"] }
    });
    fetchResponses.push({
        status: 200,
        body: { translations: ["Translated prefetch 4"] }
    });
    
    const timeBeforeResume = Date.now();
    await wait(3000); // Wait for the 1s retry_after + gap + all fetches to complete

    // The second and third fetch calls should happen AFTER the retry_after delay
    assert(fetchCalls.length >= 2, 'Queue should resume after 429');
    
    const delay = fetchCalls[1].time - fetchCalls[0].time;
    console.log("Delay before retry (ms):", delay);
    assert(delay >= 950, 'Retry-After delay should be honored approximately'); // >= 1000ms theoretically
    
    // Look at the bodies of the first few fetch calls to ensure visible happens before prefetch
    const call1Body = JSON.parse(fetchCalls[0].options.body); // was 429
    const call2Body = JSON.parse(fetchCalls[1].options.body); // visible 1 retry
    const call3Body = JSON.parse(fetchCalls[2].options.body); // visible 2
    
    assert(call1Body.paragraphs[0] === 'visible 1', 'First fetch should be visible 1');
    assert(call2Body.paragraphs[0] === 'visible 1', 'Second fetch (retry) should be visible 1');
    assert(call3Body.paragraphs[0] === 'visible 2', 'Third fetch should be visible 2');
    
    const allFetchedParagraphs = fetchCalls.map(c => JSON.parse(c.options.body).paragraphs).flat();
    
    fetchCalls.forEach((c, i) => {
        console.log(`Fetch ${i}:`, JSON.parse(c.options.body).paragraphs);
    });

    fetchCalls.forEach((c) => {
        const body = JSON.parse(c.options.body);
        assert(typeof body.book_id === 'string' && body.book_id.length > 0,
            'Every translation request must carry a bounded book cache scope');
        assert(typeof body.chapter_id === 'string' && body.chapter_id.length > 0,
            'Every translation request must carry a bounded chapter cache scope');
        assert.strictEqual(c.options.credentials, 'same-origin',
            'Cookie credentials must not be sent cross-origin without explicit opt-in');
    });
    
    // visible 1, visible 1, visible 2, prefetch 1, prefetch 2, prefetch 3, prefetch 4
    // We expect 7 paragraphs in total passed to fetch
    const uniqueParagraphs = new Set(allFetchedParagraphs);
    console.log("All fetched paragraphs:", allFetchedParagraphs);
    
    // Ensure no accidental duplicates beyond the retry
    const nonRetryParagraphs = allFetchedParagraphs.slice(1);
    const hasDups = new Set(nonRetryParagraphs).size !== nonRetryParagraphs.length;
    assert(!hasDups, 'There should be no duplicate translation blocks requested');
    
    assert(maxActiveFetches === 1, 'Never exceeded one active fetch');

    // An ambiguous transport failure must not automatically create duplicate
    // provider work. It remains failed until the user explicitly retries.
    const failedParagraph = iframeDoc.createElement('p');
    failedParagraph.textContent = 'network failure';
    failedParagraph.getBoundingClientRect = () => ({
        width: 100, height: 20, left: 0, top: 0
    });
    fetchResponses.push(new Error('synthetic network failure'));
    iframeDoc.querySelector('section').appendChild(failedParagraph);
    await wait(1800);
    const failureCalls = fetchCalls.filter(c =>
        JSON.parse(c.options.body).paragraphs.includes('network failure'));
    assert.strictEqual(failureCalls.length, 1,
        'Ambiguous transport failures must wait for an explicit user retry');
    assert.strictEqual(btBar.dataset.state, 'error',
        'Terminal transport failures must surface an actionable error state');

    // Regression guard for the status-bar flicker bug: the position-based
    // page-turn poll (every 350ms) used to run unconditionally, including
    // while a translation pass was actively inserting bilingual blocks --
    // which changes paragraph heights and can shift which element counts as
    // "first visible" with no real page turn. That false positive forced a
    // full newGeneration() reset (hides the status pill) immediately followed
    // by a fresh translateCurrentPage() (shows it again) on every poll tick
    // for as long as work was in progress -- visible as the status pill
    // blinking on/off rapidly. The fix gates that poll on genuinely being
    // idle; real navigation while work is in flight is still caught
    // immediately via the epub.js relocated/rendered hooks, which don't
    // depend on visual position. A full behavioral reproduction needs a
    // real layout engine (jsdom does not compute live reflow from DOM
    // changes), so this locks the source-level guard in place instead.
    assert(
        /if\s*\(\s*!isTranslating\s*&&\s*!isPrefetching\s*\)\s*\{[\s\S]{0,400}?getVisibleParagraphs/.test(code),
        'Page-turn poll must be gated on being idle (regression: status-bar flicker while translating)'
    );

    // Regression guard: "Translated" mode used to store only PLAIN TEXT of the
    // original and restore via textContent, permanently stripping the
    // paragraph's markup (italics/bold/links) when toggling back. The fix
    // keeps the original innerHTML in a WeakMap and restores from it.
    assert(
        /const originalHtml = new WeakMap\(\)/.test(code)
            && /function restoreOriginal\(el\)/.test(code)
            && /originalHtml\.set\(el,\s*clone\.innerHTML\)/.test(code)
            && /el\.innerHTML = html/.test(code),
        'Inline mode must preserve and restore the original markup (regression: italics/links lost)'
    );

    // Regression guard: ambiguous timeouts/network failures must never spawn a
    // second provider request automatically. Only admission-rejected 429s may
    // requeue, and those responses have a strict bound.
    assert(
        !/requeueForRetry/.test(code)
            && /function|const markBatchFailed/.test(code)
            && /requeueRateLimited/.test(code)
            && /BT_CLIENT_MAX_RATE_LIMIT_RESPONSES/.test(code),
        'Only bounded 429 admission retries may be automatic'
    );

    // Regression guard: the client safety-net timeout must be distinguishable
    // from a deliberate abort (mode/language/page change), so timeouts become
    // visible terminal failures while deliberate aborts discard stale work.
    assert(
        /btTimedOut/.test(code) && /'timeout'/.test(code) && /'aborted'/.test(code),
        'Timeout failures must be distinguished from deliberate aborts'
    );

    assert(
        /const PERSIST_CACHE = cfg\.persistCache === true/.test(code)
            && /function cacheKeyForText\(text/.test(code)
            && /function elementContextId\(el\)/.test(code)
            && /scope\.book_id, scope\.chapter_id, elementContext, text/.test(code)
            && /BT_UI_VERSION, SOURCE_LANG, TARGET_LANG/.test(code)
            && !/BT_UI_VERSION, generation, SOURCE_LANG, TARGET_LANG/.test(code),
        'Persistent browser caching must be opt-in and keys must be context-scoped'
    );

    const [tokenTransport, forwardedTransport, cwaTransport] = await Promise.all([
        captureAuthTransport({ authMode: 'token', apiToken: 'browser-token' }),
        captureAuthTransport({ authMode: 'forwarded', apiToken: 'must-not-leak' }),
        captureAuthTransport({ authMode: 'cwa_session', sendCredentials: true })
    ]);
    assert.strictEqual(tokenTransport.credentials, 'omit',
        'Token mode must omit CWA cookies');
    assert.strictEqual(tokenTransport.headers['X-BT-Token'], 'browser-token',
        'Token mode must send only its explicit compatibility token');
    assert.strictEqual(forwardedTransport.credentials, 'omit',
        'Forwarded mode must omit CWA cookies');
    assert(!('X-BT-Token' in forwardedTransport.headers),
        'Forwarded mode must not leak an accidentally configured token');
    assert.strictEqual(cwaTransport.credentials, 'include',
        'Explicit cross-origin CWA-session mode must include its HttpOnly cookie');
    assert(!('X-BT-Token' in cwaTransport.headers),
        'CWA-session mode must not send a compatibility token');

    console.log("All assertions passed.");
    process.exit(0);
}

runTest().catch(err => {
    console.error("Test failed:", err);
    process.exit(1);
});
