const http = require('http');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const port = Number(process.env.BT_E2E_PORT || 4173);

const contentTypes = {
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
};

function sendFile(response, relativePath) {
    const filePath = path.join(root, relativePath);
    response.writeHead(200, {
        'Content-Type': contentTypes[path.extname(filePath)] || 'application/octet-stream',
        'Cache-Control': 'no-store',
    });
    fs.createReadStream(filePath).pipe(response);
}

const server = http.createServer((request, response) => {
    const url = new URL(request.url, `http://127.0.0.1:${port}`);

    if (url.pathname === '/bt-static/loader.js') {
        sendFile(response, 'static/loader.js');
        return;
    }
    if (url.pathname === '/bt-static/translator.js') {
        sendFile(response, 'static/translator.js');
        return;
    }
    if (url.pathname === '/bt-static/translator.css') {
        sendFile(response, 'static/translator.css');
        return;
    }
    if (url.pathname === '/bt-config.json') {
        response.writeHead(200, {
            'Content-Type': 'application/json; charset=utf-8',
            'Cache-Control': 'no-store',
        });
        response.end(JSON.stringify({
            apiUrl: '/bt-api',
            authMode: 'cwa_session',
            credentials: 'same-origin',
        }));
        return;
    }
    if (url.pathname === '/chapter/1') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        response.end(`<!doctype html><html><body>
          <main class="chapter">
            <p id="paragraph-one">A quiet production test paragraph.</p>
            <p id="paragraph-two">A second paragraph checks queue order.</p>
          </main>
        </body></html>`);
        return;
    }
    if (url.pathname === '/read/42') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        response.end(`<!doctype html><html lang="en"><head>
          <meta charset="utf-8">
          <title>CWA reader fixture</title>
          <script src="/bt-static/loader.js?v=e2e"></script>
        </head><body>
          <main><div id="viewer"><iframe title="Book chapter" src="/chapter/1"></iframe></div></main>
        </body></html>`);
        return;
    }
    if (url.pathname === '/library') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        response.end(`<!doctype html><html lang="en"><head>
          <meta charset="utf-8"><title>CWA library fixture</title>
          <script src="/bt-static/loader.js?v=e2e"></script>
        </head><body><main><h1>Library</h1></main></body></html>`);
        return;
    }
    if (url.pathname === '/favicon.ico') {
        response.writeHead(204);
        response.end();
        return;
    }

    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('not found');
});

server.listen(port, '127.0.0.1', () => {
    process.stdout.write(`fixture listening on http://127.0.0.1:${port}\n`);
});

function stop() {
    server.close(() => process.exit(0));
}

process.on('SIGINT', stop);
process.on('SIGTERM', stop);
