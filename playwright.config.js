const { defineConfig } = require('@playwright/test');

const port = Number(process.env.BT_E2E_PORT || 4173);
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;

module.exports = defineConfig({
    testDir: './e2e',
    fullyParallel: false,
    forbidOnly: Boolean(process.env.CI),
    retries: 0,
    workers: 1,
    reporter: 'line',
    timeout: 20_000,
    use: {
        baseURL: `http://127.0.0.1:${port}`,
        browserName: 'chromium',
        headless: true,
        locale: 'es-ES',
        serviceWorkers: 'block',
        screenshot: 'only-on-failure',
        trace: 'retain-on-failure',
        launchOptions: executablePath ? { executablePath } : {},
    },
    webServer: {
        command: 'node e2e/fixture-server.js',
        url: `http://127.0.0.1:${port}/library`,
        reuseExistingServer: false,
        timeout: 10_000,
        env: { BT_E2E_PORT: String(port) },
    },
});
