// Run against an isolated Odoo copy with credentials from test_todo_browser_setup.py.
const { chromium } = require(process.env.AIIS_PLAYWRIGHT_PACKAGE);
const fs = require('fs');
const assert = require('assert/strict');
const meta = JSON.parse(fs.readFileSync(process.env.AIIS_TODO_BROWSER_META || '/tmp/aiis-todo-browser-private.json'));
const base = process.env.AIIS_TEST_URL;
const output = process.env.AIIS_BROWSER_OUTPUT || 'backups/test-results/20261104-todo';
assert(base, 'An isolated AIIS_TEST_URL is required');
assert(meta.database.startsWith('aiis_flow_test_'), 'An isolated database is required');
fs.mkdirSync(output, { recursive: true });

(async () => {
    const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const results = [];
    for (const login of ['demo', 'jeffrey', 'procurement', 'admin', 'cfo', 'chairman']) {
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        try {
            const auth = await context.request.post(`${base}/web/session/authenticate`, {
                data: { jsonrpc: '2.0', params: { db: meta.database, login, password: meta.password } },
            });
            const session = await auth.json();
            assert(!session.error, session.error?.data?.message);
            assert.equal(session.result.uid, meta.users[login].id);
            await page.goto(`${base}/web?db=${meta.database}`);
            await page.locator('.o_web_client').waitFor({ timeout: 45000 });
            await page.locator('.o_navbar_apps_menu').first().click();
            await page.getByRole('menuitem', { name: 'To do', exact: true }).click();
            await page.locator('.o_list_view .o_data_row').first().waitFor({ timeout: 30000 });
            assert(page.url().includes(`action=${meta.action}`), 'To do must open the unified action directly');
            assert.equal(await page.locator('.o_list_view .o_data_row').count(), meta.users[login].count);
            assert(!await page.getByRole('menuitem', { name: 'Waiting for Me', exact: true }).count());
            assert(!await page.getByRole('menuitem', { name: 'Returned to Me', exact: true }).count());
            assert(!await page.getByRole('menuitem', { name: 'Bills to Process', exact: true }).count());
            const text = await page.locator('.o_list_view').innerText();
            assert(!text.includes('Document unavailable'), 'All assigned fixtures must be readable');
            await page.screenshot({ path: `${output}/${login}-todo.png`, fullPage: true });
            await page.locator('.o_list_view .o_data_row').first().locator('[name="document_name"]').click();
            await page.locator('.o_form_view').waitFor({ timeout: 20000 });
            assert(!page.url().includes('model=aiis.todo'), 'Clicking a row must open the source document');
            const legacyResponse = await context.request.post(`${base}/web/action/load`, {
                data: { jsonrpc: '2.0', params: { action_id: 'aiis_procurement_request.action_vendor_bills_to_process' } },
            });
            const legacy = await legacyResponse.json();
            assert(!legacy.error, legacy.error?.data?.message);
            assert.equal(legacy.result.res_model, 'aiis.todo', 'Old bill link must not open all vendor bills');
            await page.goto(`${base}/web?db=${meta.database}#action=${legacy.result.id}`);
            await page.reload();
            await page.locator('.o_list_view .o_data_row').first().waitFor({ timeout: 30000 });
            assert.equal(await page.locator('.o_list_view .o_data_row').count(), meta.users[login].count);
            assert.deepEqual(errors, []);
            results.push({ login, status: 'PASS', tasks: meta.users[login].count,
                checks: ['app opens inbox directly', 'personal task count', 'no old submenus', 'row opens source document', 'old bill link opens personal inbox', 'no JS errors'] });
            console.log('PASS', login, meta.users[login].count);
        } catch (error) {
            const details = page.getByText('See details', { exact: true });
            if (await details.count()) await details.click();
            const dialog = page.locator('.o_error_dialog');
            const detail = await dialog.count() ? await dialog.innerText() : '';
            results.push({ login, status: 'FAIL', error: error.message, detail, errors });
            console.log('FAIL', login, error.message);
            await page.screenshot({ path: `${output}/${login}-failure.png`, fullPage: true }).catch(() => {});
        } finally {
            await context.close();
            fs.writeFileSync(`${output}/browser-results.json`, JSON.stringify(results, null, 2));
        }
    }
    await browser.close();
    if (results.some(result => result.status !== 'PASS')) process.exitCode = 1;
})();
