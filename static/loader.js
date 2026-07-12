/**
 * book-translator — proxy-injection bootstrap.
 *
 * In proxy mode this is the ONLY tag injected into CWA's pages (before
 * </head> by nginx sub_filter). It self-guards to reader pages, provides the
 * same-origin default config, and loads the stylesheet + main overlay script.
 * Keeping the injected surface to one tag is what makes proxy mode survive
 * CWA template changes.
 */
(function () {
    'use strict';
    if (window.__BT_LOADER_RAN) { return; }
    window.__BT_LOADER_RAN = true;

    // Only activate on the ebook reader; every other CWA page is passed
    // through untouched.
    if (!/\/read\//.test(window.location.pathname)) { return; }

    // Inherit the version from our own ?v= query param (stamped by nginx from
    // the VERSION file). Hardcoding a version here caused a cache-busting bug
    // (loader busted, assets stale) — this way loader.js is version-free and
    // the container's VERSION file is the single source of truth.
    var VERSION = (function () {
        try {
            var src = document.currentScript && document.currentScript.src;
            if (!src) { return 'dev'; }
            return new URL(src, window.location.href).searchParams.get('v') || 'dev';
        } catch (e) { return 'dev'; }
    })();
    var BASE = '/bt-static/';

    // Same-origin defaults: the proxy serves the API under /bt-api, so no
    // CORS and no hardcoded host/port. An operator can still predefine
    // window.BOOK_TRANSLATOR before this script to override anything.
    var existing = window.BOOK_TRANSLATOR || {};
    window.BOOK_TRANSLATOR = {
        apiUrl: existing.apiUrl || '/bt-api',
        sourceLang: existing.sourceLang || 'English',
        targetLang: existing.targetLang || '',
        // Leave this undefined when the proxy cannot derive it; translator.js
        // will then use the /read/<book> path instead of collapsing to an
        // empty/unscoped identifier.
        bookId: existing.bookId,
        // Durable browser storage is opt-in because shared browsers may be
        // used by multiple CWA accounts. The scoped server-side cache is
        // persistent regardless of this setting.
        persistCache: existing.persistCache === true,
        // Cross-origin CWA-session deployments must opt in to cookie-bearing
        // fetches and enumerate the exact reader origin server-side. Proxy
        // mode is same-origin and sends its HttpOnly CWA cookie by default.
        sendCredentials: existing.sendCredentials === true,
        // Make credential transport explicit. A configured compatibility
        // token opts into token mode; otherwise the supported proxy topology
        // validates the existing CWA session cookie.
        authMode: existing.authMode || (existing.apiToken ? 'token' : 'cwa_session'),
        // Compatibility mode only. Never persist this JavaScript-readable
        // shared secret in localStorage; configure it in the trusted overlay
        // bootstrap or prefer cwa_session/forwarded authentication.
        apiToken: existing.apiToken || ''
    };

    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = BASE + 'translator.css?v=' + VERSION;
    (document.head || document.documentElement).appendChild(link);

    var script = document.createElement('script');
    script.src = BASE + 'translator.js?v=' + VERSION;
    script.defer = true;
    (document.head || document.documentElement).appendChild(script);
})();
