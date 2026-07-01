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

    var VERSION = '2.0.0';
    var BASE = '/bt-static/';

    // Same-origin defaults: the proxy serves the API under /bt-api, so no
    // CORS and no hardcoded host/port. An operator can still predefine
    // window.BOOK_TRANSLATOR before this script to override anything.
    var existing = window.BOOK_TRANSLATOR || {};
    window.BOOK_TRANSLATOR = {
        apiUrl: existing.apiUrl || '/bt-api',
        sourceLang: existing.sourceLang || 'English',
        targetLang: existing.targetLang || '',
        // Optional shared secret (BT_API_TOKEN); readable from localStorage so
        // it can be set per-browser without editing any server file:
        //   localStorage.setItem('bt_token', '<token>')
        apiToken: existing.apiToken || (function () {
            try { return localStorage.getItem('bt_token') || ''; } catch (e) { return ''; }
        })()
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
