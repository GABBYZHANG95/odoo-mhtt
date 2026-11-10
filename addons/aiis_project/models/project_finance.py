from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .project_bom import COST_GROUPS


class ProjectFxRate(models.Model):
    """Exchange rate the CFO confirms for a foreign cost currency before the
    quotation (#61): the quotation and its cost are converted at this rate."""
    _name = 'aiis.project.fx.rate'
    _description = 'Project Exchange Rate'
    _order = 'project_id, currency_id'

    project_id = fields.Many2one('aiis.project', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one('res.currency', string='Currency', required=True)
    company_currency_id = fields.Many2one(related='project_id.company_currency_id')
    rate = fields.Float(string='Rate', digits=(12, 6), required=True,
                        help='Company currency per 1 unit of this currency, e.g. 3.75 SAR per USD.')

    @api.model_create_multi
    def create(self, vals_list):
        self._check_cfo(self.env['aiis.project'].browse({v.get('project_id') for v in vals_list}))
        return super().create(vals_list)

    def write(self, vals):
        self._check_cfo(self.project_id)
        return super().write(vals)

    def unlink(self):
        self._check_cfo(self.project_id)
        return super().unlink()

    def _check_cfo(self, projects):
        if not self.env.su and projects.filtered(lambda p: not p.can_review_finance):
            raise UserError(_("Only the CFO confirms the exchange rates, after the CEO approved the margin."))

    _sql_constraints = [
        ('currency_unique', 'unique(project_id, currency_id)', 'One rate per currency.'),
        ('rate_positive', 'CHECK(rate > 0)', 'The rate must be positive.'),
    ]


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    aiis_budget_project_id = fields.Many2one(
        'aiis.project', compute='_compute_aiis_project_budget', compute_sudo=True, string='Budget Project')
    aiis_budget_warning = fields.Char(compute='_compute_aiis_project_budget', compute_sudo=True)

    @api.depends('order_line.aiis_bom_line_id', 'order_line.price_subtotal', 'state')
    def _compute_aiis_project_budget(self):
        """#61: project POs over the CFO's cost budget are flagged, not blocked"""
        for order in self:
            project = order.order_line.aiis_bom_line_id.project_id[:1]
            order.aiis_budget_project_id = project
            order.aiis_budget_warning = False
            if not project or not project.cost_budget:
                continue
            others = project._budget_po_lines().filtered(lambda l: l.order_id != order)
            total = project._budget_amount(others) + project._budget_amount(
                order.order_line.filtered(lambda l: l.aiis_bom_line_id.project_id == project))
            currency = project.company_currency_id
            if currency.compare_amounts(total, project.cost_budget) > 0:
                order.aiis_budget_warning = _(
                    "Over the cost budget of %(project)s: budget %(budget)s, with this PO %(total)s "
                    "(over by %(over)s).",
                    project=project.display_name,
                    budget=currency.format(project.cost_budget), total=currency.format(total),
                    over=currency.format(total - project.cost_budget))
