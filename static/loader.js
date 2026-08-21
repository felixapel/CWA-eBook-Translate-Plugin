(function () {
    'use strict';
    if (window.__BT_LOADER_RAN) { return; }
    window.__BT_LOADER_RAN = true;

    var VERSION = (function () {
        try {
            var src = document.currentScript && document.currentScript.src;
            if (!src) { return '2.1.1'; }
            return new URL(src, window.location.href).searchParams.get('v') || '2.1.1';
        } catch (e) { return '2.1.1'; }
    })();
    var BASE = '/bt-static/';

    var existing = window.BOOK_TRANSLATOR || {};
    window.BOOK_TRANSLATOR = {
        apiUrl: existing.apiUrl || '/bt-api',
        sourceLang: existing.sourceLang || 'English',
        targetLang: existing.targetLang || '',
        bookId: existing.bookId,
        persistCache: existing.persistCache === true,
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
