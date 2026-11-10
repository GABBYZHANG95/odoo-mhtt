from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _message_compute_author(self, author_id=None, email_from=None, raise_on_email=True):
        # Asset creation logs a message while posting. Use the configured
        # company mailbox if the author has none, keeping the actual author
        # so the audit trail identifies the accountant who posted the bill.
        author = self.env['res.partner'].browse(author_id) if author_id is not None else self.env.user.partner_id
        if not email_from and author and not author.email:
            company = self.company_id if len(self) == 1 else self.env.company
            company_sender = company.partner_id.email_formatted
            if company_sender:
                email_from = company_sender
                author_id = author.id
        return super()._message_compute_author(author_id, email_from, raise_on_email)
