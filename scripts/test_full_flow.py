"""Run with Odoo shell against an isolated copy, NEVER a live database.
  docker exec -i mhtt-app odoo shell -d aiis_flow_test_20261027 --no-http < scripts/test_full_flow.py
Results and export files are written to container /tmp/aiis-flow-results.
"""
import json, os, traceback, zipfile, io, secrets
from datetime import date, timedelta
from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form
assert env.cr.dbname.startswith('aiis_flow_test_'), 'Isolated test database required'
os.makedirs('/tmp/aiis-flow-results', exist_ok=True)
# No outbound deliveries from this test session or its clone's background jobs.
type(env['ir.mail_server']).send_email = lambda *a, **k: 'isolated-test-no-delivery'
env['ir.cron'].search([]).write({'active': False})
env['ir.mail_server'].search([]).write({'active': False})
env = env(context=dict(env.context, mail_notify_force_send=False, tracking_disable=True, lang='en_US'))
users = {login: env['res.users'].search([('login','=',login)],limit=1) for login in ['admin','demo','jeffrey','procurement','cfo','chairman']}
assert all(users.values()), 'Missing planned users'
R, records = {}, {}
def ok(condition, detail='assertion failed'):
    assert condition, detail

def step(ids, fn):
    ids = ids.split(',')
    try:
        with env.cr.savepoint():
            detail = fn()
        for key in ids: R[key] = {'status':'PASS', 'detail':str(detail or 'Assertions passed')}
        print('PASS', ','.join(ids), str(detail or '')[:200], flush=True)
        return detail
    except Exception as exc:
        for key in ids: R[key] = {'status':'FAIL','detail':str(exc),'traceback':traceback.format_exc()}
        print('FAIL', ','.join(ids), repr(exc), flush=True)

def as_user(record, login): return record.with_user(users[login])
def model(name, login): return env[name].with_user(users[login])
def chain(record): return [(l.step,l.user_id.login,l.state) for l in record.sudo().approval_request_id.line_ids.sorted('step')]
def pending(record): return [x[1] for x in chain(record) if x[2]=='pending']
def approve(record, login): as_user(record,login).action_approval_approve()
def approve_all(record):
    for _ in range(12):
        p = pending(record)
        if not p: return
        for login in p: approve(record, login)
    raise AssertionError('Approval did not terminate')
def denied(fn):
    try:
        with env.cr.savepoint(): fn()
    except (AccessError, UserError, ValidationError): return True
    raise AssertionError('Forbidden operation unexpectedly succeeded')
def account(line, xmlid): ok(line.account_id == env.ref('aiis_finance.account_'+xmlid), str(line.account_id.display_name))
def remember(key, rec):
    records[key]={'model':rec._name,'id':rec.id,'name':rec.sudo().display_name}
    return rec

sales=users['demo'].employee_id.department_id
customer=env['res.partner'].create({'name':'FLOW20261027 Customer','aiis_customer_code':'flow1027','customer_rank':1})
vendor=env['res.partner'].create({'name':'FLOW20261027 Vendor','supplier_rank':1})
def setup_budget():
    budget=model('aiis.sales.budget','cfo').create({'department_id':sales.id,'date_from':date(2026,1,1),'date_to':date(2026,12,31),'amount':20000})
    remember('budget',budget)
    ok(budget.used==0, str(budget.used))
    return budget.display_name
step('0.1',setup_budget)
step('0.2',lambda: ok(sales.aiis_function=='sales' and env.company.aiis_rd_department_id.aiis_function=='rd'))
step('0.3',lambda: ok(model('aiis.account.mapping','cfo').search_count([])>0))

def new_pr(price=1200,product=None):
    pr=model('procurement.request','demo').create({'reason':'FLOW20261027 internal purchase', 'currency_id':env.company.currency_id.id, 'line_ids':[(0,0,{'description':'FLOW20261027 purchase','quantity':1, **({'product_id':product.id} if product else {})})]})
    pr.action_submit()
    return pr
pr=step('1.1',lambda: remember('pr',new_pr()))
if pr:
    step('1.1',lambda: ok(pr.state=='submitted' and pending(pr)==['jeffrey'],str(chain(pr))))
    def reject_pr():
        denied(lambda: as_user(pr,'jeffrey')._approval_reject(' '))
        as_user(pr,'jeffrey')._approval_reject('FLOW20261027: subscription justification required')
        ok(pr.state=='draft')
        return pr.name
    step('1.2',reject_pr)
    def resubmit():
        as_user(pr,'demo').action_submit(); approve(pr,'jeffrey'); ok(pr.state=='approved')
        return pr.name
    step('1.3',resubmit)
    step('1.3-notification',lambda:ok(pr.sudo().activity_ids.filtered(lambda a:a.user_id==users['procurement']), 'Procurement did not receive PR activity'))

def convert(pr,price,categ=None):
    wizard=model('procurement.request.create.po.wizard','procurement').create({'request_id':pr.id,'partner_id':vendor.id,**({'categ_id':categ.id} if categ else {})})
    wizard.action_confirm()
    po=as_user(pr.sudo().purchase_order_id,'procurement')
    po.order_line.write({'price_unit':price,'taxes_id':[(5,0,0)]})
    po.button_confirm()
    return po
po=False
if pr:
    def create_po():
        global po
        po=remember('po',convert(pr,1200,env.ref('aiis_finance.categ_nature_software')))
        ok(pending(po)==['admin'],str(chain(po)))
        as_user(pr,'demo').read(['po_approval_status','payment_status','receipt_status'])
        denied(lambda:as_user(po,'demo').read(['amount_total']))
        return po.name
    step('1.4',create_po)

def po_bill(po):
    approve(po,'admin')
    bill=as_user(po.sudo().order_line.invoice_lines.move_id,'cfo')
    ok(len(bill)==1 and bill.state=='draft')
    ok(bill.activity_ids.filtered(lambda a:a.user_id==users['cfo'] and 'Pay' in (a.summary or '')), 'Missing CFO payment activity')
    return bill

def pay(bill):
    bill.invoice_date=fields.Date.today(); bill.action_post()
    payment=model('account.payment.register','cfo').with_context(active_model='account.move',active_ids=bill.ids).create({})._create_payments()
    return payment
bill=payment=False
if po:
    def confirm_po():
        global bill
        bill=remember('bill',po_bill(po)); return bill.name
    step('1.5',confirm_po)
if bill:
    def booking():
        account(bill.invoice_line_ids,'sales_crm')
        ok(str(sales.aiis_analytic_account_id.id) in bill.invoice_line_ids.analytic_distribution)
        return bill.invoice_line_ids.account_id.display_name
    step('1.6',booking)
    def payment_step():
        global payment
        payment=remember('payment',pay(bill))
        ok(po.aiis_payment_status=='paid',po.aiis_payment_status)
        ok(po.sudo().activity_ids.filtered(lambda a:a.user_id==users['procurement']), 'Missing procurement activity')
    step('1.7',payment_step)
    step('1.8',lambda:(as_user(po,'procurement').action_mark_received(),ok(pr.state=='done'),pr.name))

def equipment(price):
    product=env['product.product'].create({'name':'FLOW20261027 Laptop '+str(price),'type':'service','categ_id':env.ref('aiis_procurement_request.product_category_internal_it_equipment').id})
    p=new_pr(price,product); approve(p,'jeffrey'); order=convert(p,price); b=po_bill(order)
    account(b.invoice_line_ids,'asset_computers' if price>=5000 else 'ga_other')
    pay(b)
    assets=env['account.asset'].search([('account_move_line_ids','in',b.invoice_line_ids.ids)])
    ok(len(assets)==(1 if price>=5000 else 0),str(assets))
    if assets: ok(assets.method_number==3 and assets.method_time=='year' and assets.method_period=='month', str((assets.method_number,assets.method_period)))
    remember('equipment_'+str(price),b)
    return b.name+' '+b.invoice_line_ids.account_id.display_name
step('1.9',lambda:equipment(6000))
step('1.10',lambda:equipment(4000))
def high_po():
    p=model('purchase.order','procurement').create({'partner_id':vendor.id,'order_line':[(0,0,{'product_id':po.order_line.product_id.id,'product_qty':1,'price_unit':300001,'taxes_id':[(5,0,0)]})]})
    p.button_confirm(); ok(pending(p)==['admin']); approve(p,'admin'); ok(pending(p)==['chairman']); approve(p,'chairman'); ok(p.state=='purchase');remember('high_po',p)
    return p.name+' admin → chairman'
step('1.11',high_po)

project=False
attachment=env['ir.attachment'].create({'name':'FLOW20261027 project brief.txt','raw':b'Acceptance test project information'})
def initiation():
    global project
    project=remember('project',model('aiis.project','demo').create({'name':'FLOW20261027 Full Lifecycle','partner_id':customer.id,'info_attachment_ids':[(6,0,attachment.ids)]}))
    project.action_submit_initiation()
    ok([x[1] for x in chain(project)]==['jeffrey','admin'],str(chain(project)))
    return project.display_name
step('2.1',initiation)
if project:
    step('2.2',lambda:(approve(project,'jeffrey'),approve(project,'admin'),ok(project.state=='design',project.state)))
    step('2.3',lambda:denied(lambda:as_user(project,'demo').write({'bom_line_ids':[(0,0,{'name':'Unauthorised','quantity':1})]})))
    def solution():
        as_user(project,'jeffrey').write({'bom_line_ids':[(0,0,{'name':'FLOW20261027 Server','quantity':2})]})
        as_user(project,'jeffrey').action_submit_solution();ok(pending(project)==['admin']);return project.state
    step('2.4',solution)
    def costing():
        approve(project,'admin')
        as_user(project.sudo().bom_line_ids,'procurement').write({'unit_cost':1000,'quote_valid_until':date.today()+timedelta(days=30)})
        as_user(project,'procurement').action_submit_costing();ok(pending(project)==['admin'])
    step('2.5',costing)
    def quotation():
        as_user(project,'admin').write({'margin_percent':25}); approve(project,'admin')
        ok(abs(project.sudo().quotation_id.amount_untaxed-2000/.75)<.02)
        remember('quotation',project.sudo().quotation_id)
        return str(project.sudo().quotation_id.amount_untaxed)
    step('2.6',quotation)
    step('2.7',lambda:(as_user(project,'demo')._customer_accepted('FLOW-CUSTOMER-PO-1027'),ok(project.state=='contract_bom')))
    step('2.8',lambda:(as_user(project,'jeffrey').action_confirm_bom(),ok(project.state=='contract_terms')))
    def contract():
        as_user(project,'admin').write({'project_manager_id':users['demo'].id})
        as_user(project,'demo').write({'commercial_terms':'Delivery in 30 days','payment_cycle':'100% on delivery','payment_conditions':'Due in 30 days','maintenance_start':date.today(),'maintenance_end':date.today()+timedelta(days=365)})
        as_user(project,'demo').action_submit_contract();ok(set(pending(project))=={'cfo','jeffrey'},str(chain(project)))
        return str(chain(project))
    step('2.9',contract)
    # Exercise CFO rejection on the same project, then submit revised terms and continue.
    step('2.11',lambda:(as_user(project,'cfo')._approval_reject('FLOW20261027: amend payment terms'),ok(project.state=='contract_terms'),as_user(project,'demo').action_submit_contract()))
    def contract_approval():
        approve(project,'cfo');ok(pending(project)==['jeffrey']);approve(project,'jeffrey');ok(pending(project)==['admin']);approve(project,'admin')
        ok(project.state=='delivery');ok(project.sudo().quotation_id.state=='sale');return project.state
    step('2.10',contract_approval)
    project_pr=False
    def project_purchase():
        global project_pr
        a=as_user(project,'demo').action_open_pr_wizard()
        w=model('aiis.project.pr.wizard','demo').with_context(a['context']).create({})
        w.line_ids.write({'quantity':1})
        a=w.action_confirm();project_pr=remember('project_pr',env['procurement.request'].browse(a['res_id']))
        ok(project_pr.state=='approved' and not project_pr.approval_request_id.line_ids)
        return project_pr.name
    step('2.12',project_purchase)
    if project_pr:
        def project_po():
            p=convert(project_pr,1000);b=po_bill(p); account(b.invoice_line_ids,'project_cost')
            ok(str(project.sudo().analytic_account_id.id) in b.invoice_line_ids.analytic_distribution)
            pay(b);as_user(p,'procurement').action_mark_received()
            l=project.sudo().bom_line_ids;ok((l.qty_requested,l.qty_ordered,l.qty_received)==(1,1,1))
            remember('project_po',p); return p.name+' '+b.name
        step('2.13',project_po)
    def travel():
        t=model('aiis.travel.request','demo').create({'project_id':project.id,'destination':'Riyadh','purpose':'FLOW20261027 site visit','date_from':date.today(),'date_to':date.today()+timedelta(days=1),'budget':1200})
        t.action_submit();ok([x[1] for x in chain(t)]==['jeffrey','admin']);approve_all(t)
        e=model('hr.expense','demo').create({'name':'FLOW20261027 Hotel','employee_id':users['demo'].employee_id.id,'product_id':env.ref('hr_expense.expense_product_travel_accommodation').id,'total_amount_currency':1200})
        s=model('hr.expense.sheet','demo').create({'name':'FLOW20261027 Travel claim','employee_id':users['demo'].employee_id.id,'expense_line_ids':[(6,0,e.ids)],'aiis_travel_request_id':t.id})
        s.action_submit_sheet();ok([x[1] for x in chain(s)]==['jeffrey','admin']);approve_all(s);account(e,'project_travel');remember('travel',s)
        return s.display_name
    step('2.14',travel)
    step('2.15',lambda:(as_user(project,'demo').action_request_delivery_acceptance(),approve(project,'admin'),ok(project.state=='maintenance',project.state)))
    hours=False
    def service():
        global hours
        hours=remember('hours',model('aiis.service.hours.request','demo').create({'project_id':project.id,'hours':10,'description':'FLOW20261027 support'}))
        hours.action_submit();ok(pending(hours)==['jeffrey']);approve(hours,'jeffrey')
        hours.write({'record_ids':[(0,0,{'hours':10,'description':'Visit'})]})
        denied(lambda:hours.write({'record_ids':[(0,0,{'hours':1,'description':'Exceeds budget'})]}))
        return hours.display_name
    step('2.16',service)
    if hours:
        def extend():
            a=hours.action_new_extension()
            with Form(model('aiis.service.hours.request','demo').with_context(a['context'])) as f:f.hours=5
            extension=f.save();extension.action_submit();approve(extension,'jeffrey')
            hours.write({'record_ids':[(0,0,{'hours':3,'description':'Additional visit'})]})
            ok(hours.hours_used==13 and hours.hours_remaining==2)
        step('2.17',extend)
    step('2.18',lambda:(as_user(project,'demo').action_request_closing(),approve(project,'admin'),ok(project.state=='done')))

req=False

def sales_request(amount):
    r=model('aiis.sales.expense.request','demo').create({'expense_type':'entertainment','partner_id':customer.id,'purpose':'FLOW20261027 customer dinner','amount':amount,'date_from':date(2026,10,27)})
    r.action_submit();return r

def first_expense():
    global req
    req=remember('sales_request',sales_request(3000));ok(not req.show_budget);return req.display_name
step('3.1',first_expense)
if req:
    def budget_box():
        r=as_user(req,'jeffrey');ok(r.show_budget);ok(r.budget_amount==20000,str(r.budget_amount));ok(r.budget_this==3000);approve(req,'jeffrey');ok(pending(req)==['admin'])
    step('3.2',budget_box)
    step('3.3',lambda:(approve(req,'admin'),ok(req.state=='approved')))
    def over_budget():
        r=sales_request(18000);remember('over_budget',r);ok(as_user(r,'jeffrey').budget_over);approve(r,'jeffrey');ok(pending(r)==['admin'])
    step('3.4',over_budget)
    def reimbursement(amount):
        e=model('hr.expense','demo').create({'name':'FLOW20261027 Dinner','employee_id':users['demo'].employee_id.id,'product_id':env.ref('hr_expense.expense_product_meal').id,'total_amount_currency':amount})
        s=model('hr.expense.sheet','demo').create({'name':'FLOW20261027 Sales claim','employee_id':users['demo'].employee_id.id,'expense_line_ids':[(6,0,e.ids)],'aiis_sales_request_id':req.id})
        s.action_submit_sheet();return s
    sheet=False
    def submit_sheet():
        global sheet
        sheet=remember('sales_claim',reimbursement(2500));ok([x[1] for x in chain(sheet)]==['jeffrey','admin','cfo']);return str(chain(sheet))
    step('3.5',submit_sheet)
    if sheet:
        def approved_sheet():
            approve_all(sheet);ok(sheet.state=='approve');account(sheet.sudo().expense_line_ids,'sales_travel');ok(req.sudo().budget_consumption==2500)
            ok(sheet.sudo().activity_ids.filtered(lambda a:a.user_id==users['cfo']))
        step('3.6',approved_sheet)
    def rejected_sheet():
        s=reimbursement(100);approve(s,'jeffrey');approve(s,'admin');as_user(s,'cfo')._approval_reject('FLOW20261027 unreadable receipt');ok(s.state=='draft');remember('rejected_claim',s)
    step('3.7',rejected_sheet)

cost=False
def personnel():
    global cost
    cost=remember('personnel',model('aiis.personnel.cost','admin').create({'employee_id':users['jeffrey'].employee_id.id,'type_id':env.ref('aiis_finance.personnel_type_iqama').id,'amount':650,'partner_id':users['jeffrey'].partner_id.id}))
    cost.action_post(); debit=cost.move_id.line_ids.filtered('debit');credit=cost.move_id.line_ids.filtered('credit')
    account(debit,'sales_personnel');account(credit,'payroll_payable');ok(debit.debit==650);ok(str(sales.aiis_analytic_account_id.id) in debit.analytic_distribution)
    return cost.move_id.name
step('4.1',personnel)
if cost:step('4.2',lambda:denied(lambda:cost.write({'amount':700})))
step('4.3',lambda:[denied(lambda:model('aiis.personnel.cost',u).search([])) for u in ['demo','jeffrey']])

def finance_menus():
    for login in ['demo','jeffrey','procurement']:
        ids=env['ir.ui.menu'].with_user(users[login])._visible_menu_ids()
        for xml in ['menu_finance_overview','menu_finance_reports']:ok(env.ref('aiis_finance.'+xml).id not in ids,login+' sees '+xml)
        denied(lambda:model('aiis.finance.board',login).get_overview({'year':2026}))
step('5.1',finance_menus)
board=model('aiis.finance.board','cfo');params={'year':2026,'cutoff':'2026-12-31'}
step('5.2',lambda:ok([x['future'] for x in board.get_overview(dict(params,cutoff='2026-04-30'))['quarters']]==[False,False,True,True]))

def overview():
    data=board.get_overview(dict(params,project_id=project.id));order=project.sudo().quotation_id
    expected=order.currency_id._convert(order.amount_untaxed,env.company.currency_id,env.company,order.date_order.date())
    ok(abs(data['kpis']['order_value']-expected)<.02,str(data['kpis']))
    for k in ['order_value','revenue','gross_profit','backlog']:
        a=board.open_drill(k,dict(params,project_id=project.id));model(a['res_model'],'cfo').search(a['domain'])
    return data['kpis']
if project:step('5.3',overview)

instances={}
def report(kind,columns='quarter',**kw):
    w=model('aiis.finance.report.wizard','cfo').create({'report':kind,'fiscal_year':2026,'cutoff_date':date(2026,12,31),'columns':columns,**kw})
    a=w.action_open();instance=model('mis.report.instance','cfo').browse(a['res_id']);matrix=instance._compute_matrix()
    rows={r.kpi.name:[c.val for c in r.iter_cells()] for r in matrix.iter_rows() if not r.account_id}
    return instance,rows

def pl():
    inst,rows=report('pl');instances['pl']=inst;remember('pl',inst);ok(len(rows)==40,str(len(rows)));return {'rows':len(rows),'sales_crm':rows.get('sales_crm'),'ga_other':rows.get('ga_other'),'sales_personnel':rows.get('sales_personnel')}
step('5.4',pl)
def filtered():
    i,r=report('pl','month',project_id=project.id);ok(len(i.period_ids)==13);ok(r['cost_of_revenue'][9]==1000,str(r['cost_of_revenue']))
    i,r=report('pl','month',department_id=sales.id);ok(r['sales_personnel'][9]>=650,str(r['sales_personnel']));remember('pl_month',i)
step('5.5',filtered)

def exports():
    instance=instances['pl'];action=instance.export_xls()
    data,fmt=env['ir.actions.report'].with_user(users['cfo'])._render_xlsx(action['report_name'],instance.ids,data=action.get('data'))
    ok(zipfile.is_zipfile(io.BytesIO(data)))
    with zipfile.ZipFile(io.BytesIO(data)) as z:ok('xl/workbook.xml' in z.namelist())
    open('/tmp/aiis-flow-results/profit_loss.xlsx','wb').write(data)
    action=instance.print_pdf();data,fmt=env['ir.actions.report'].with_user(users['cfo'])._render_qweb_pdf(action['report_name'],instance.ids,data=action.get('data'))
    ok(data.startswith(b'%PDF'));open('/tmp/aiis-flow-results/profit_loss.pdf','wb').write(data)
    from PyPDF2 import PdfFileReader
    reader=PdfFileReader(io.BytesIO(data));ok(len(reader.pages)>0)
    return str(len(reader.pages))+' PDF pages; valid XLSX workbook'
step('5.6',exports)
def bs():
    i,r=report('bs');remember('bs',i);ok(all(abs(x or 0)<.01 for x in r['check']),str(r['check']));return r['check']
step('5.7',bs)

def aging():
    config=env.ref('aiis_finance.age_configuration')
    ok(config.line_ids.sorted('inferior_limit').mapped('inferior_limit')==[30,60,90,36500])
    w=model('aged.partner.balance.report.wizard','cfo').create({'company_id':env.company.id,'date_at':date(2026,12,31),'age_partner_config_id':config.id,'target_move':'posted'})
    data=w._prepare_report_data()
    data={k:fields.Date.to_string(v) if isinstance(v,date) else v for k,v in data.items()}
    values=model('report.account_financial_report.aged_partner_balance','cfo')._get_report_values(w.ids,data)
    return 'Not due / '+str(config.line_ids.sorted('inferior_limit').mapped('name'))
step('5.8',aging)
def statement():
    partner=env['res.partner'].search([('customer_rank','>',0)],limit=1)
    w=model('activity.statement.wizard','cfo').with_context(active_model='res.partner',active_ids=partner.ids).create({'date_start':date(2026,1,1),'date_end':date(2026,12,31),'filter_partners_non_due':False})
    a=w.button_export_pdf()
    data,_=env['ir.actions.report'].with_user(users['cfo'])._render_qweb_pdf(a['report_name'],partner.ids,data=a['data'])
    ok(data.startswith(b'%PDF'));open('/tmp/aiis-flow-results/partner_statement.pdf','wb').write(data)
    return partner.display_name
step('5.9',statement)

if payment:
    def cash():
        before=board.get_cash_flow(params)['columns'][3]
        bank=env['account.journal'].search([('type','=','bank'),('company_id','=',env.company.id)],limit=1)
        st=model('account.bank.statement.line','cfo').create({'journal_id':bank.id,'date':date(2026,10,27),'amount':-1200,'payment_ref':'FLOW20261027 subscription','partner_id':vendor.id})
        outstanding=payment.move_id.line_ids.filtered(lambda l:l.account_id.reconcile and l.credit)
        if not st.is_reconciled:
            with Form(st,view='account_reconcile_oca.bank_statement_line_form_reconcile_view') as f:
                suggested={line_id for line in f.reconcile_data_info.get('data',[]) for line_id in line.get('counterpart_line_ids',[])}
                if outstanding.id not in suggested:f.add_account_move_line_id=outstanding
            st.reconcile_bank_line()
        ok(st.is_reconciled)
        after=board.get_cash_flow(params)['columns'][3];ok(abs(after['opex']-before['opex']+1200)<.01,str((before,after)))
        remember('bank_statement',st);return {'before':before,'after':after}
    step('5.10',cash)
step('5.11',lambda:board.get_checks(params))
step('5.12',lambda:ok(board.get_overview(params)==model('aiis.finance.board','chairman').get_overview(params)))

def permissions():
    fields_sales=model('aiis.project','demo').fields_get()
    ok('total_cost' not in fields_sales)
    all_projects=env['aiis.project'].search([])
    visible=model('aiis.project','demo').search([])
    for p in visible:
        ok(users['demo'] in (p.salesperson_id | p.project_manager_id | p.member_ids | p.approval_user_ids))
    ok(set(model('aiis.project','procurement').search([]).ids)==set(all_projects.ids))
    for line in model('aiis.approval.line','jeffrey').search([('user_id','=',users['jeffrey'].id),('state','=','pending')]):
        model(line.res_model,'jeffrey').browse(line.res_id).read(['display_name'])
    return {'all_projects':len(all_projects),'demo_visible':len(visible)}
step('6.1,6.2,6.3',permissions)
def chairman_approval():
    lines=model('aiis.approval.line','chairman').search([('user_id','=',users['chairman'].id),('state','=','pending')])
    return [(l.res_model,l.res_id) for l in lines]
step('6.4',chairman_approval)

# Browser accesses this isolated copy with a fresh test-only password.
password=secrets.token_urlsafe(24)
for u in users.values():u.with_context(no_reset_password=True).write({'password':password,'lang':'en_US'})
preview=instances['pl'].preview()
preview_vals={k:v for k,v in preview.items() if k in env['ir.actions.act_window']._fields and k not in ['type','views','id']}
preview_vals['name']='FLOW20261027 P&L preview'
preview_action=env['ir.actions.act_window'].create(preview_vals)
meta={'database':env.cr.dbname,'users':{k:v.id for k,v in users.items()},'password':password,'records':records,'actions':{key:env.ref('aiis_finance.'+key).id for key in ['action_finance_overview','action_finance_cash_flow','action_finance_checks','action_aged_balance']}}
meta['actions']['pl_preview']=preview_action.id
meta['actions']['action_report_profit_loss']=env.ref('aiis_finance.action_report_profit_loss').id
meta['actions']['action_report_balance_sheet']=env.ref('aiis_finance.action_report_balance_sheet').id
open('/tmp/aiis-flow-results/browser_private.json','w').write(json.dumps(meta,default=str))
open('/tmp/aiis-flow-results/results.json','w').write(json.dumps({'checks':R,'records':records},ensure_ascii=False,indent=2,default=str))
env.cr.commit()
print('SUMMARY',json.dumps({s:sum(r['status']==s for r in R.values()) for s in ['PASS','FAIL']}),flush=True)
