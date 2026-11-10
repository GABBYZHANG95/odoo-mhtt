"""Historical pre-fix fault diagnostics; all database changes roll back.

These assertions reproduce the original faults and are not expected to pass
after the fixes. Use the module regressions and browser checks for verification.
"""
import json,traceback,os
from datetime import date
from odoo.tests import Form
from odoo.exceptions import UserError,AccessError
assert env.cr.dbname.startswith('aiis_flow_test_')
type(env['ir.mail_server']).send_email=lambda *a,**k:'isolated-test-no-delivery'
env=env(context=dict(env.context,mail_notify_force_send=False,tracking_disable=True,lang='en_US'))
u={x:env['res.users'].search([('login','=',x)],limit=1) for x in ['admin','demo','procurement','cfo','chairman']}
results={}
class Rollback(Exception):pass

def run(name,fn):
    try:
        with env.cr.savepoint():
            result=fn();results[name]={'status':'PASS','detail':result}
            raise Rollback()
    except Rollback:pass
    except Exception as e:results[name]={'status':'FAIL','detail':str(e),'traceback':traceback.format_exc()}
    print(name,results[name],flush=True)

def asset():
    cfo=u['cfo'];had_email=bool(cfo.email)
    v=env['res.partner'].search([('name','=','FLOW20261027 Vendor')],limit=1)
    p=env['product.product'].create({'name':'FLOW20261027 diagnostic laptop','type':'service','categ_id':env.ref('aiis_procurement_request.product_category_internal_it_equipment').id})
    b=env['account.move'].with_user(cfo).create({'move_type':'in_invoice','partner_id':v.id,'invoice_date':date.today(),'invoice_line_ids':[(0,0,{'name':'FLOW20261027 diagnostic laptop','product_id':p.id,'quantity':1,'price_unit':6000,'tax_ids':[(5,0,0)],'account_id':env.ref('aiis_finance.account_asset_computers').id,'asset_profile_id':env.ref('aiis_finance.asset_profile_computers').id})]})
    error=None
    try:
        with env.cr.savepoint():b.action_post()
    except UserError as e:error=str(e)
    assert error and not had_email,(error,had_email)
    cfo.with_context(no_reset_password=True).email='flow-test@example.invalid'
    b.action_post()
    assets=env['account.asset'].search([('account_move_line_ids','in',b.invoice_line_ids.ids)])
    assert len(assets)==1 and assets.method_number==3 and assets.method_time=='year' and assets.method_period=='month'
    return {'cfo_email_configured':had_email,'original_error':error,'after_temporary_email':'posted; one 3-year asset','rolled_back':True}
run('asset_email_root_cause',asset)

def cash():
    meta=json.load(open('/tmp/aiis-flow-results/results.json'))
    payment=env['account.payment'].browse(meta['records']['payment']['id'])
    board=env['aiis.finance.board'].with_user(u['cfo']);params={'year':2026,'cutoff':'2026-12-31'}
    before=board.get_cash_flow(params)['columns'][3]
    bank=payment.journal_id
    env['account.reconcile.model'].search([('auto_reconcile','=',True)]).write({'auto_reconcile':False})
    st=env['account.bank.statement.line'].create({'journal_id':bank.id,'date':date(2026,10,27),'amount':-1200,'payment_ref':'FLOW20261027 diagnostic reconciliation','partner_id':payment.partner_id.id})
    unmatched=board.get_cash_flow(params)['columns'][3]
    outstanding=payment.move_id.line_ids.filtered(lambda l:l.account_id.reconcile and l.credit)
    with Form(st,view='account_reconcile_oca.bank_statement_line_form_reconcile_view') as f:f.add_account_move_line_id=outstanding
    st.reconcile_bank_line();assert st.is_reconciled
    after=board.get_cash_flow(params)['columns'][3]
    assert abs(after['opex']-before['opex']+1200)<.01,(before,after)
    assert abs(unmatched['other']-before['other']+1200)<.01,(before,unmatched)
    return {'before':before,'unreconciled':unmatched,'reconciled':after,'note':'Statement created by superuser only to diagnose algorithm; CFO permission issue remains; rollback'}
run('cash_flow_algorithm_with_privileged_fixture',cash)

def bypass():
    pr=env['procurement.request'].with_user(u['demo']).create({'reason':'FLOW20261027 bypass check','line_ids':[(0,0,{'description':'Unapproved purchase','quantity':1})]})
    assert pr.state=='draft'
    vendor=env['res.partner'].search([('name','=','FLOW20261027 Vendor')],limit=1)
    blocked=False
    try:pr.with_user(u['procurement']).action_create_po()
    except UserError:blocked=True
    assert blocked
    w=env['procurement.request.create.po.wizard'].with_user(u['procurement']).create({'request_id':pr.id,'partner_id':vendor.id})
    w.action_confirm()
    assert pr.purchase_order_id and pr.state=='converted'
    return {'problem':'Procurement can invoke wizard action_confirm on an unapproved draft PR','normal_button_guard':True,'wizard_guard':False,'po':pr.purchase_order_id.name,'rolled_back':True}
run('draft_pr_conversion_bypass',bypass)

def visibility():
    customer=env['res.partner'].search([('name','=','FLOW20261027 Customer')],limit=1)
    p=env['aiis.project'].with_user(u['admin']).create({'name':'FLOW20261027 outsider control','salesperson_id':u['admin'].id,'partner_id':customer.id})
    assert not env['aiis.project'].with_user(u['demo']).search([('id','=',p.id)])
    assert env['aiis.project'].with_user(u['procurement']).search([('id','=',p.id)])
    return 'Unrelated project hidden from demo; visible to procurement; rollback'
run('project_negative_visibility',visibility)

def chairman():
    lines=env['aiis.approval.line'].with_user(u['chairman']).search([('user_id','=',u['chairman'].id),('state','=','pending')])
    for l in lines:
        assert l.res_model=='purchase.order',(l.res_model,l.res_id)
        order=env['purchase.order'].browse(l.res_id)
        assert order.amount_total>300000
    return {'pending_qualifying_orders':len(lines)}
run('chairman_only_large_pending_po',chairman)
open('/tmp/aiis-flow-results/diagnostics.json','w').write(json.dumps(results,ensure_ascii=False,indent=2,default=str))
env.cr.rollback()
