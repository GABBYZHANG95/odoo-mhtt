from odoo import models


class ResCompany(models.Model):
    _inherit = 'res.company'

    def write(self, vals):
        """Choosing the R&D department in the company settings also makes it
        an R&D department for the P&L (design doc 6.3), unless the CFO has
        already given it a function. Install only guesses it by name, and a
        production database gets its departments after the install."""
        result = super().write(vals)
        if vals.get('aiis_rd_department_id'):
            self.aiis_rd_department_id.filtered(lambda d: not d.aiis_function).sudo().aiis_function = 'rd'
        return result
