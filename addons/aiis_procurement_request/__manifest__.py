{
    'name': 'MHTT Procurement Request',
    'version': '17.0.2.13.0',
    'category': 'Inventory/Purchase',
    'summary': 'Purchase requests (PR) -> reporting-line approval -> purchase order (PO) -> CEO / Chairman approval -> receipt',
    'description': """
AIIS Procurement Request
========================

Adds an internal request step in front of the purchase order sent to the
vendor - two separate documents, both traceable:

1. **Employee submits a purchase request (PR)**: what to buy, specification,
   quantity, needed-by date. No vendor and no price.
2. **Reporting-line approval**: every manager up to the department head
   approves, through the approval engine (aiis_approval); approvals waiting
   for someone are in My Approvals.
3. **Procurement** converts the approved PR into a draft Odoo purchase
   order and fills in vendor, prices and dates.
4. **PO approval**: the CEO approves every PO; above 300,000 SAR (tax
   included, converted to SAR) the Chairman approves after the CEO. Who
   approves is the holder of the CEO / Chairman position; the threshold is
   on the "Purchase Order" approval template. Rejecting needs a reason and
   sends the PO back to draft.
5. Once approved the order goes to the vendor.
6. **Receipt**: Procurement records received quantities on the PO (or
   Mark as Received on the PR); when everything is in, the PR moves to Done
   and the requester is notified.

The PR only records what we need; the PO is the formal "which vendor, at
what price" document. They link to each other.
    """,
    'author': 'MHTT',
    'depends': [
        'purchase',
        'hr',
        'analytic',
        'aiis_department_menu',
        'aiis_approval',
    ],
    'data': [
        'security/po_approval_groups.xml',
        'security/ir.model.access.csv',
        'security/procurement_request_security.xml',
        'data/ir_sequence_data.xml',
        'data/config_data.xml',
        'data/po_approval_data.xml',
        'data/procurement_activity_data.xml',
        'data/product_category_data.xml',
        'data/currency_rate_data.xml',
        'views/procurement_request_views.xml',
        'views/procurement_request_wizard_views.xml',
        'views/purchase_order_views.xml',
        'views/res_company_views.xml',
        'views/account_move_views.xml',
        'views/payment_plan_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
    'post_init_hook': 'post_init_hook',
    'license': 'LGPL-3',
}
