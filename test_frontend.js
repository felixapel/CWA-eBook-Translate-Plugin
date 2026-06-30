const fs = require('fs');
const jsdom = require("jsdom");
const { JSDOM } = jsdom;

const code = fs.readFileSync('static/translator.js', 'utf-8');

// Mock fetch
let fetchCalls = [];
let fetchResponses = [];
global.fetch = async (url, options) => {
    fetchCalls.push({url, options});
    const nextResp = fetchResponses.shift();
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
    <p>para 1</p>
    <p>para 2</p>
    <p>para 3</p>
</body>
</html>
`, {
    url: "http://localhost/",
    runScripts: "dangerously"
});

// Setup mock iframe
const iframeDoc = dom.window.document.querySelector("iframe").contentDocument;
iframeDoc.body.innerHTML = `
    <p class="calibre1">visible 1</p>
    <p class="calibre1">visible 2</p>
    <p class="calibre1">prefetch 1</p>
    <p class="calibre1">prefetch 2</p>
`;

// Mock getBoundingClientRect
iframeDoc.querySelectorAll('p').forEach((p, idx) => {
    p.getBoundingClientRect = () => ({
        width: 100, height: 20,
        left: 0, top: idx < 2 ? 0 : 1000 // First two are visible
    });
});
dom.window.innerWidth = 800;
dom.window.innerHeight = 600;
dom.window.localStorage.setItem('bt_mode', 'translated');
dom.window.localStorage.setItem('bt_prefetch', '1');

dom.window.requestAnimationFrame = (cb) => setTimeout(cb, 16);
dom.window.fetch = global.fetch;

// Run the script
const scriptEl = dom.window.document.createElement("script");
scriptEl.textContent = code;
dom.window.document.body.appendChild(scriptEl);

async function wait(ms) {
    return new Promise(r => setTimeout(r, ms));
}

async function runTest() {
    console.log("Starting frontend test...");
    
    // Wait for init
    await wait(1000);
    
    // There should be a fetch call for the visible elements
    console.log("Fetch calls:", fetchCalls.length);
    
    // Provide a rate limit response
    fetchResponses.push({
        status: 429,
        body: { error: 'rate_limited', retry_after: 2 }
    });
    
    // Provide a success response later
    fetchResponses.push({
        status: 200,
        body: { translations: ["Trans 1"] }
    });

    await wait(3000); // Wait for rate limit to expire and next fetch
    console.log("Fetch calls after wait:", fetchCalls.length);
    const btBar = dom.window.document.getElementById('bt-bar');
    console.log("State:", btBar ? btBar.dataset.state : 'No bar');
    
    console.log("DONE");
    process.exit(0);
}

runTest().catch(console.error);
