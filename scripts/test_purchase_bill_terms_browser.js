const { chromium } = require(process.env.AIIS_PLAYWRIGHT_PACKAGE);
const fs = require('fs');
const assert = require('assert/strict');
const meta = JSON.parse(fs.readFileSync('/tmp/aiis-purchase-bill-private.json'));
const base = process.env.AIIS_TEST_URL;
assert(base && meta.database.startsWith('aiis_flow_test_'), 'Isolated test server and database required');
const output = 'docs/test/results/20261104-purchase-bill-terms';
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const contexts = [];
    const checks = [];
    let page;
    async function login(user) {
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        contexts.push(context);
        const response = await context.request.post(`${base}/web/session/authenticate`, {
            data: { jsonrpc: '2.0', params: { db: meta.database, login: user, password: meta.password } },
        });
        const session = await response.json();
        assert(!session.error, session.error?.data?.message);
        page = await context.newPage();
        return context;
    }
    async function rpc(context, model, method, args, kwargs = {}) {
        const response = await context.request.post(`${base}/web/dataset/call_kw`, {
            data: { jsonrpc: '2.0', params: { model, method, args, kwargs } },
        });
        const data = await response.json();
        assert(!data.error, data.error?.data?.message);
        return data.result;
    }
    async function read(context, model, id, fields) { return (await rpc(context, model, 'read', [[id], fields]))[0]; }
    async function openBill(id) {
        await page.goto(`${base}/web?db=${meta.database}#action=${meta.bill_action}&model=account.move&id=${id}&view_type=form`);
        await page.locator('.o_form_view').waitFor({ timeout: 30000 });
    }
    try {
        let context = await login('cfo');
        await openBill(meta.purchase_bill_id);
        const lines = page.locator('[name="invoice_line_ids"] .o_data_row').first();
        await lines.locator('[name="price_unit"]').click();
        for (const name of ['price_unit', 'quantity', 'product_id']) {
            assert.equal(await lines.locator(`[name="${name}"] input`).count(), 0, `${name} must be readonly`);
        }
        assert.equal(await page.locator('.o_form_view [name="ref"] input').count(), 1);
        assert.equal(await page.locator('.o_form_view [name="invoice_date"] input').count(), 1);
        await page.locator('.o_form_view [name="ref"] input').fill('UI supplier reference');
        await page.screenshot({ path: `${output}/cfo-commercial-readonly.png`, fullPage: true });
        await page.getByRole('button', { name: 'Return to Procurement', exact: true }).click();
        const modal = page.locator('.modal');
        await modal.locator('[name="reason"] textarea').fill('Supplier price changed; please revise to 500 per unit.');
        await page.screenshot({ path: `${output}/return-reason.png`, fullPage: true });
        await modal.getByRole('button', { name: 'Return to Procurement', exact: true }).click();
        await modal.waitFor({ state: 'hidden', timeout: 15000 });
        assert.equal((await read(context, 'account.move', meta.purchase_bill_id, ['state'])).state, 'cancel');
        assert.equal((await read(context, 'purchase.order', meta.purchase_order_id, ['state'])).state, 'draft');
        checks.push('CFO commercial fields readonly; financial reference/date editable; return with reason cancels bill and resets PO');

        context = await login('procurement');
        await page.goto(`${base}/web?db=${meta.database}#action=${meta.action}`);
        await page.locator('.o_list_view').waitFor({ timeout: 30000 });
        const row = page.locator('.o_list_view .o_data_row').filter({ hasText: meta.purchase_order_name });
        await row.waitFor();
        assert((await row.innerText()).includes('Revise Purchase Order'));
        await page.screenshot({ path: `${output}/procurement-revision-todo.png`, fullPage: true });
        await row.locator('[name="document_name"]').click();
        await page.locator('.o_form_view').waitFor();
        const price = page.locator('[name="order_line"] .o_data_row [name="price_unit"]').first();
        await price.click();
        await price.locator('input').fill('500');
        const confirmed = page.waitForResponse(response => response.url().includes('/web/dataset/call_button')
            && response.request().postDataJSON()?.params?.method === 'button_confirm');
        await page.locator('button[name="button_confirm"]:visible').first().click();
        await confirmed;
        assert.equal((await read(context, 'purchase.order', meta.purchase_order_id, ['state'])).state, 'to approve');
        checks.push('Procurement opens revision To do, edits price and resubmits for approval');

        context = await login('admin');
        await rpc(context, 'purchase.order', 'action_approval_approve', [[meta.purchase_order_id]]);
        const bills = await rpc(context, 'account.move', 'search_read', [[
            ['aiis_purchase_order_id', '=', meta.purchase_order_id], ['move_type', '=', 'in_invoice'], ['state', '=', 'draft'],
        ], ['id', 'amount_total']]);
        assert.equal(bills.length, 1);
        const newBill = bills[0].id;
        assert.notEqual(newBill, meta.purchase_bill_id);
        assert.equal(bills[0].amount_total, 1500);
        checks.push('CEO reapproval creates a new bill at the approved price; old cancelled bill retained');

        context = await login('cfo');
        await openBill(newBill);
        await rpc(context, 'account.move', 'write', [[newBill], { invoice_date: new Date().toISOString().slice(0, 10) }]);
        await rpc(context, 'account.move', 'action_post', [[newBill]]);
        const wizard = await rpc(context, 'account.payment.register', 'create', [{}], {
            context: { active_model: 'account.move', active_ids: [newBill] },
        });
        await rpc(context, 'account.payment.register', 'action_create_payments', [[wizard]]);
        assert.equal((await read(context, 'purchase.order', meta.purchase_order_id, ['aiis_payment_status'])).aiis_payment_status, 'paid');
        const tasks = await rpc(context, 'aiis.todo', 'search_count', [[['kind', '=', 'bill'], ['res_id', '=', newBill]]]);
        assert.equal(tasks, 0);
        await page.reload();
        await page.locator('.o_form_view').waitFor();
        await page.screenshot({ path: `${output}/new-bill-paid.png`, fullPage: true });
        checks.push('CFO confirms and pays replacement bill; payment task leaves To do');
        fs.writeFileSync(`${output}/browser-results.json`, JSON.stringify({ status: 'PASS', checks }, null, 2));
        console.log('PASS', checks.length, 'purchase bill UI/workflow checks');
    } catch (error) {
        if (page) {
            const details = page.getByText('See details', { exact: true });
            if (await details.count()) await details.click();
            const dialog = page.locator('.o_error_dialog');
            if (await dialog.count()) console.log(await dialog.innerText());
            await page.screenshot({ path: `${output}/failure.png`, fullPage: true }).catch(() => {});
        }
        fs.writeFileSync(`${output}/browser-results.json`, JSON.stringify({ status: 'FAIL', checks, error: error.message }, null, 2));
        console.log(error.stack);
        process.exitCode = 1;
    } finally {
        for (const context of contexts) await context.close();
        await browser.close();
    }
})();
