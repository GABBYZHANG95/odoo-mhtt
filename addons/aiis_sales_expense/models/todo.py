from odoo import models


class Todo(models.Model):
    _inherit = 'aiis.todo'
    _depends = {'hr.expense.sheet': ['aiis_sales_request_id', 'approval_state', 'state']}

    def _todo_queries(self):
        return super()._todo_queries() + [self._activity_query(
            'expense', 'hr.expense.sheet', 'aiis_sales_expense', 'mail_activity_pay_expense',
            'JOIN hr_expense_sheet doc ON doc.id = a.res_id ' + self._finance_activity_join(),
            "doc.aiis_sales_request_id IS NOT NULL AND doc.approval_state = 'approve' "
            "AND doc.state IN ('approve', 'post')")]
