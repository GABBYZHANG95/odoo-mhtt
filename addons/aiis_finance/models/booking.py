from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    """Vendor bill lines of purchase requests are booked by the rules of
    design doc 6.4: project purchases to Cost of revenue, internal ones by
    the requester's department and the product's nature, equipment above
    the threshold as a fixed asset."""
    _inherit = 'purchase.order.line'

    def _aiis_booking(self):
        """(account, project, department) for this line, empty if not from a purchase request"""
        self.ensure_one()
        request = self.env['procurement.request'].sudo().search(
            [('purchase_order_id', '=', self.order_id.id)], limit=1)
        project = self.aiis_bom_line_id.project_id or request.aiis_project_id
        if not request and not project:
            return self.env['account.account'], project, self.env['hr.department']
        company = self.company_id
        unit_price = self.currency_id._convert(
            self.price_unit, company.currency_id, company, fields.Date.context_today(self), round=False)
        account = self.product_id.categ_id._aiis_expense_account(
            company, department=request.department_id, unit_price=unit_price, project=project)
        return account, project, request.department_id

    def _prepare_account_move_line(self, move=False):
        res = super()._prepare_account_move_line(move)
        if self.display_type or not self.product_id:
            return res
        account, project, department = self._aiis_booking()
        if account:
            res['account_id'] = account.id
        if not project and department:
            res['analytic_distribution'] = department._aiis_analytic_distribution(res.get('analytic_distribution'))
        return res


class HrExpense(models.Model):
    """Expense lines: delivery trips are project costs, sales trips and
    entertainment are Sales travel and entertainment, anything else follows
    the employee's department and the product's nature."""
    _inherit = 'hr.expense'

    @api.depends('sheet_id.aiis_travel_request_id', 'sheet_id.aiis_sales_request_id',
                 'employee_id.department_id')
    def _compute_account_id(self):
        super()._compute_account_id()
        for expense in self:
            account = expense._aiis_booking_account()
            if account:
                expense.account_id = account

    def _aiis_booking_account(self):
        self.ensure_one()
        sheet = self.sheet_id
        if sheet.aiis_travel_request_id:
            return self.env.ref('aiis_finance.account_project_travel', raise_if_not_found=False)
        if sheet.aiis_sales_request_id:
            return self.env.ref('aiis_finance.account_sales_travel', raise_if_not_found=False)
        if not self.product_id:
            return None
        return self.product_id.categ_id._aiis_expense_account(
            self.company_id, department=self.employee_id.department_id, unit_price=self.price_unit)

    def _prepare_move_lines_vals(self):
        vals = super()._prepare_move_lines_vals()
        department = self.employee_id.department_id
        if department and not self.sheet_id.aiis_travel_request_id:
            vals['analytic_distribution'] = department._aiis_analytic_distribution(vals.get('analytic_distribution'))
        return vals
