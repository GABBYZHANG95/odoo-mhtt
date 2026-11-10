{
    'name': 'MHTT Projects',
    'version': '17.0.1.10.0',
    'category': 'Sales',
    'summary': 'Project-based approval from initiation to delivery and maintenance, with BOM, costing, contract, travel and service hours',
    'description': """
AIIS Projects
=============

Track 2 of the approval design (docs/design/审批流程设计.md, section 4),
phase 1:

- **Customer code** on customers (e.g. STC); project numbers are
  customer code + year + sequence, e.g. STC-2026-001, per customer per year.
- **Initiation**: the salesperson submits the opportunity with the
  project information attached; reporting line up to the CEO approves;
  the project number and its analytic account are created.
- **Solution design**: R&D keeps the BOM (a living list); each submission
  saves a version, which the CEO approves. Lines changed after approval are
  flagged and need a new quote.
- **Costing**: Procurement enters vendor, unit cost, currency and quote
  validity per BOM line; the CEO sets the margin (gross margin: price =
  cost / (1 - margin)) and approves; a sales quotation is generated and
  handed to the salesperson.
- **Customer decision**: accepted (customer PO recorded, on to the
  contract stage), or closed / paused with a restart date.

Phase 2, the contract:

- R&D confirms the BOM is still current (changed lines need a new quote);
  Procurement re-quotes changed and expired lines.
- Cost the same or lower: the quoted price stands. Cost higher: the
  salesperson talks to the customer and asks for a new quotation, which
  goes through the costing approval again.
- The salesperson fills in the commercial terms, payment cycle and payment
  conditions; R&D head and CFO approve in parallel, then the CEO. A
  rejection sends it back to the salesperson.
- Once approved, the sales order is confirmed - the project contract
  replaces the customer contract for project sales - and the project
  moves to delivery.

Phase 3, delivery and maintenance:

- The project manager raises purchase requests from the BOM, batch by
  batch; they skip approval and go straight to Procurement. The BOM shows
  requested / on PO / received / remaining per line.
- Delivery and maintenance trips: travel request (reporting line up to the
  CEO); afterwards the traveller lists the actual expenses and receipts on
  the request and submits the claim (#65), which builds the linked expense
  report - approved through the engine, costs on the project's analytic
  account. One claim per request; over the budget is flagged, not blocked.
- The CEO accepts the delivery; with maintenance dates the project goes
  into maintenance, otherwise it closes. Service engineers request hours
  (reporting line up to the department head) and log their work; extensions
  add hours. At the end the CEO approves the closing or renews.

Costs, vendors and margin are visible to Procurement, the CFO and the CEO
only. A project is visible to the people involved in it; Procurement,
CFO, CEO and Chairman see all projects.
    """,
    'author': 'MHTT',
    'depends': [
        'sale',
        'purchase',
        'analytic',
        'hr',
        'aiis_approval',
        'aiis_procurement_request',
        'aiis_customer_contract',
        'hr_expense',
    ],
    'data': [
        'security/project_security.xml',
        'security/ir.model.access.csv',
        'data/project_data.xml',
        'views/project_views.xml',
        'views/res_partner_views.xml',
        'views/res_company_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/service_hours_views.xml',
        'views/travel_views.xml',
        'wizard/project_wizard_views.xml',
        'views/menu.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
