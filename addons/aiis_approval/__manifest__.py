{
    'name': 'MHTT Approval Engine',
    'version': '17.0.1.9.0',
    'category': 'Productivity',
    'summary': 'Configurable approval chains: reporting line, department head and positions (CEO, Chairman, CFO)',
    'description': """
AIIS Approval Engine
====================

One approval mechanism shared by every document that needs sign-off
(purchase requests, purchase orders, and later project gates, travel and
expenses). Design: docs/design/审批流程设计.md, section 7.

- **Positions**: CEO, Chairman, CFO (more can be added), each held by one
  user who approves for it. Holding a position does not grant any access
  rights - groups still do that.
- **Approval templates**: per document type (and project gate), a list of
  nodes. A node is the requester's reporting line up to a level
  (department head or CEO), a given department's head, or a position.
  Nodes with the same sequence run in parallel; a node can apply only
  above an amount.
- On submission the approver list is generated and frozen. Each approver
  gets a to-do; rejecting needs a reason and sends the document back;
  the submitter can withdraw; only an administrator can reassign.
- **To do** opens the current user’s approvals, revisions and assigned business tasks.

A document plugs in by inheriting `aiis.approval.mixin` and telling the
engine who the requester is, which amount the conditions look at, and
what to do once approved or rejected.
    """,
    'author': 'MHTT',
    'depends': ['mail', 'hr'],
    'data': [
        'security/approval_security.xml',
        'security/ir.model.access.csv',
        'data/approval_data.xml',
        'views/approval_position_views.xml',
        'views/approval_template_views.xml',
        'views/approval_request_views.xml',
        'wizard/approval_wizard_views.xml',
        'views/todo_views.xml',
        'views/menu.xml',
    ],
    'assets': {'web.assets_backend': ['aiis_approval/static/src/views/todo_list.js']},
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
