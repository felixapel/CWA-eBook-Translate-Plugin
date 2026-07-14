/**
 * book-translator — proxy-injection bootstrap.
 *
 * In proxy mode this is the ONLY tag injected into CWA's pages (before
 * </head> by nginx sub_filter). It self-guards to reader pages, provides the
 * a validated same-origin config, and loads the stylesheet + main overlay script.
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

    function loadAssets(managed) {
        var expectedCredentials = {
            cwa_session: 'same-origin',
            forwarded: 'include'
        };
        if (!managed || managed.apiUrl !== '/bt-api'
                || expectedCredentials[managed.authMode] !== managed.credentials) {
            throw new Error('unsupported browser authentication contract');
        }

        // Authentication settings are server-owned. Only non-security UI
        // preferences may be inherited from a trusted embedding page.
        var existing = window.BOOK_TRANSLATOR || {};
        window.BOOK_TRANSLATOR = {
            apiUrl: managed.apiUrl,
            sourceLang: existing.sourceLang || 'English',
            targetLang: existing.targetLang || '',
            bookId: existing.bookId,
            persistCache: existing.persistCache === true,
            authMode: managed.authMode,
            credentials: managed.credentials,
            apiToken: ''
        };

        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = BASE + 'translator.css?v=' + VERSION;
        (document.head || document.documentElement).appendChild(link);

        var script = document.createElement('script');
        script.src = BASE + 'translator.js?v=' + VERSION;
        script.defer = true;
        (document.head || document.documentElement).appendChild(script);
    }

    fetch('/bt-config.json', {
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        headers: { Accept: 'application/json' }
    }).then(function (response) {
        if (!response.ok) { throw new Error('browser configuration unavailable'); }
        return response.json();
    }).then(loadAssets).catch(function (error) {
        // Fail closed: without a server-approved transport, do not load the
        // overlay and do not send book text anywhere.
        console.error('[BookTranslator] disabled:', error.message);
    });
})();
