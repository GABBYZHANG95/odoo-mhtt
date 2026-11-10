const { chromium } = require(process.env.AIIS_PLAYWRIGHT_PACKAGE);
const fs = require('fs');
const assert = require('assert/strict');
const meta = JSON.parse(fs.readFileSync('/tmp/aiis-supplier-terms-private.json'));
const base = process.env.AIIS_TEST_URL;
assert(base && meta.database.startsWith('aiis_flow_test_'), 'Isolated server and database required');
const out = 'docs/test/results/20261104-supplier-terms';
fs.mkdirSync(out, { recursive: true });
(async () => {
    const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
    const contexts = [], checks = [];
    let page;
    async function login(login) {
        const context = await browser.newContext({ viewport: { width: 1500, height: 1100 } });
        contexts.push(context);
        const response = await context.request.post(`${base}/web/session/authenticate`, {
            data: { jsonrpc: '2.0', params: { db: meta.database, login, password: meta.password } },
        });
        const data = await response.json();
        assert(!data.error, data.error?.data?.message);
        page = await context.newPage();
        return context;
    }
    async function rpc(context, model, method, args, kwargs = {}) {
        const response = await context.request.post(`${base}/web/dataset/call_kw`, {
            data: { jsonrpc: '2.0', params: { model, method, args, kwargs } },
        });
        const result = await response.json();
        assert(!result.error, result.error?.data?.message);
        return result.result;
    }
    async function read(context, model, id, names) { return (await rpc(context, model, 'read', [[id], names]))[0]; }
    async function openOrderTodo() {
        await page.goto(`${base}/web?db=${meta.database}#action=${meta.action}`);
        const row = page.locator('.o_list_view .o_data_row').filter({ hasText: meta.terms_order_name });
        await row.first().waitFor({ timeout: 30000 });
        await row.first().locator('[name="document_name"]').click();
        await page.locator('.o_form_view').waitFor();
    }
    async function clickObject(name) {
        const done = page.waitForResponse(r => r.url().includes('/web/dataset/call_button')
            && r.request().postDataJSON()?.params?.method === name);
        await page.locator(`button[name="${name}"]:visible`).first().click();
        return done;
    }
    async function openBill(id) {
        await page.goto(`${base}/web?db=${meta.database}#action=${meta.bill_action}&model=account.move&id=${id}&view_type=form`);
        await page.locator('.o_form_view').waitFor({ timeout: 30000 });
    }
    async function pay(context, bill, expected) {
        await openBill(bill);
        await clickObject('action_register_payment');
        const modal = page.locator('.modal');
        const input = modal.locator('[name="amount"] input');
        await input.waitFor();
        assert.equal(Number((await input.inputValue()).replaceAll(',', '')), expected);
        await page.screenshot({ path: `${out}/payment-${expected}.png`, fullPage: true });
        await modal.locator('button[name="action_create_payments"]').click();
        await modal.waitFor({ state: 'hidden', timeout: 20000 });
    }
    try {
        let context = await login('cfo');
        await openOrderTodo();
        await page.getByRole('tab', { name: 'Supplier Terms & Payments', exact: true }).click();
        assert.equal(await page.locator('[name="aiis_supplier_offer"] textarea').count(), 0);
        await page.screenshot({ path: `${out}/cfo-review.png`, fullPage: true });
        await clickObject('action_request_supplier_changes');
        let modal = page.locator('.modal');
        await modal.locator('[name="reason"] textarea').fill('Negotiate a lower advance payment.');
        await modal.locator('[name="proposal"] textarea').fill('20% advance, 80% after acceptance.');
        await page.screenshot({ path: `${out}/cfo-negotiation-proposal.png`, fullPage: true });
        await modal.locator('button[name="action_return"]').click();
        await modal.waitFor({ state: 'hidden' });
        let order = await read(context, 'purchase.order', meta.terms_order_id,
            ['state', 'aiis_supplier_offer', 'aiis_cfo_proposal', 'aiis_supplier_confirmed']);
        assert.equal(order.state, 'draft');
        assert(order.aiis_supplier_offer.includes('30%'));
        assert(order.aiis_cfo_proposal.includes('20%'));
        assert(!order.aiis_supplier_confirmed);
        checks.push('CFO proposes changes without overwriting vendor terms; Procurement receives returned task');

        context = await login('procurement');
        await openOrderTodo();
        await page.getByRole('tab', { name: 'Supplier Terms & Payments', exact: true }).click();
        const rows = page.locator('[name="aiis_payment_stage_ids"] .o_data_row');
        await rows.nth(0).locator('[name="percentage"]').click();
        await rows.nth(0).locator('[name="percentage"] input').fill('20');
        await rows.nth(1).locator('[name="percentage"]').click();
        await rows.nth(1).locator('[name="percentage"] input').fill('80');
        await page.locator('[name="aiis_supplier_offer"] textarea').fill('Supplier confirms 20% advance, 80% after acceptance.');
        await page.locator('[name="aiis_supplier_evidence_ids"] input[type="file"]').setInputFiles({
            name: 'Vendor reconfirmed.txt', mimeType: 'text/plain', buffer: Buffer.from('Supplier accepts revised 20/80 settlement.'),
        });
        await page.locator('[name="aiis_supplier_confirmed"] input').check();
        await page.screenshot({ path: `${out}/procurement-supplier-reconfirmation.png`, fullPage: true });
        await clickObject('button_confirm');
        order = await read(context, 'purchase.order', meta.terms_order_id, ['aiis_terms_review_state']);
        assert.equal(order.aiis_terms_review_state, 'waiting');
        checks.push('Procurement updates 20/80 plan, uploads supplier confirmation and resubmits to CFO');

        context = await login('cfo');
        await openOrderTodo();
        await clickObject('action_confirm_supplier_terms');
        order = await read(context, 'purchase.order', meta.terms_order_id, ['state', 'aiis_terms_review_state']);
        assert.equal(order.state, 'to approve');
        assert.equal(order.aiis_terms_review_state, 'confirmed');
        context = await login('admin');
        await openOrderTodo();
        await clickObject('action_approval_approve');
        const bills = await rpc(context, 'account.move', 'search_read', [[
            ['aiis_purchase_order_id', '=', meta.terms_order_id], ['move_type', '=', 'in_invoice'], ['state', '=', 'draft'],
        ], ['id', 'amount_total']]);
        assert.equal(bills.length, 1);
        const bill = bills[0].id;
        checks.push('CFO confirmation precedes CEO approval and generation of full bill');

        context = await login('cfo');
        await openBill(bill);
        await rpc(context, 'account.move', 'write', [[bill], { invoice_date: new Date().toISOString().slice(0, 10) }]);
        await page.reload();
        await page.locator('.o_form_view').waitFor();
        await clickObject('action_post');
        await pay(context, bill, 200);
        order = await read(context, 'purchase.order', meta.terms_order_id, ['aiis_payment_status', 'aiis_available_payment']);
        assert.equal(order.aiis_payment_status, 'partial');
        assert.equal(order.aiis_available_payment, 0);
        checks.push('Advance wizard defaults to 200; after 20% paid the 80% balance remains blocked');

        context = await login('procurement');
        await openOrderTodo();
        await page.locator('button[name="action_mark_received"]:visible').click();
        await page.locator('.modal button.btn-primary').click();
        await page.locator('.modal').waitFor({ state: 'hidden' });
        order = await read(context, 'purchase.order', meta.terms_order_id, ['aiis_full_receipt_date', 'aiis_available_payment']);
        assert(order.aiis_full_receipt_date);
        assert.equal(order.aiis_available_payment, 0);
        await page.reload();
        await page.locator('.o_form_view').waitFor();
        await clickObject('action_confirm_purchase_acceptance');
        modal = page.locator('.modal');
        await modal.locator('[name="note"] textarea').fill('All items inspected and accepted.');
        await modal.locator('[name="evidence_ids"] input[type="file"]').setInputFiles({
            name: 'Acceptance result.txt', mimeType: 'text/plain', buffer: Buffer.from('Goods passed inspection.'),
        });
        await page.screenshot({ path: `${out}/procurement-acceptance.png`, fullPage: true });
        await modal.locator('button[name="action_accept"]').click();
        await modal.waitFor({ state: 'hidden' });
        order = await read(context, 'purchase.order', meta.terms_order_id, ['aiis_acceptance_date', 'aiis_available_payment']);
        assert(order.aiis_acceptance_date);
        assert.equal(order.aiis_available_payment, 800);
        checks.push('Full receipt alone does not unlock acceptance balance; evidence-backed acceptance releases 800');

        context = await login('cfo');
        await pay(context, bill, 800);
        order = await read(context, 'purchase.order', meta.terms_order_id, ['aiis_payment_status']);
        assert.equal(order.aiis_payment_status, 'paid');
        assert.equal(await rpc(context, 'aiis.todo', 'search_count', [[['kind', '=', 'bill'], ['res_id', '=', bill]]]), 0);
        await page.reload();
        await page.locator('.o_form_view').waitFor();
        await page.screenshot({ path: `${out}/fully-paid.png`, fullPage: true });
        checks.push('Tail wizard defaults to 800; full settlement closes CFO payment task');
        fs.writeFileSync(`${out}/browser-results.json`, JSON.stringify({ status: 'PASS', checks }, null, 2));
        console.log('PASS', checks.length, 'supplier terms / milestone payment checks');
    } catch (error) {
        if (page) {
            const details = page.getByText('See details', { exact: true });
            if (await details.count()) await details.click();
            const dialog = page.locator('.o_error_dialog');
            if (await dialog.count()) console.log(await dialog.allInnerTexts());
            await page.screenshot({ path: `${out}/failure.png`, fullPage: true }).catch(() => {});
        }
        fs.writeFileSync(`${out}/browser-results.json`, JSON.stringify({ status: 'FAIL', checks, error: error.message }, null, 2));
        console.log(error.stack);
        process.exitCode = 1;
    } finally {
        for (const context of contexts) await context.close();
        await browser.close();
    }
})();
