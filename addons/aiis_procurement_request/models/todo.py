from odoo import models


class Todo(models.Model):
    _inherit = 'aiis.todo'
    _depends = {
        'procurement.request': ['state', 'purchase_order_id'],
        'account.move': ['state', 'move_type', 'payment_state', 'amount_total', 'amount_residual', 'aiis_purchase_order_id'],
        'purchase.order': ['state', 'aiis_payment_status', 'aiis_payment_managed', 'aiis_acceptance_date', 'aiis_full_receipt_date'],
        'aiis.purchase.payment.stage': ['order_id', 'due_date', 'amount', 'cumulative_amount', 'sequence'],
        'purchase.order.line': ['order_id', 'display_type', 'product_qty', 'qty_received'],
    }

    def _todo_queries(self):
        return super()._todo_queries() + [
            self._activity_query(
                'purchase_acceptance', 'purchase.order', 'aiis_procurement_request', 'mail_activity_accept_order',
                'JOIN purchase_order doc ON doc.id = a.res_id',
                "doc.state IN ('purchase', 'done') AND doc.aiis_full_receipt_date IS NOT NULL AND doc.aiis_acceptance_date IS NULL"),
            self._activity_query(
                'purchase_revision', 'purchase.order', 'aiis_procurement_request', 'mail_activity_revise_order',
                'JOIN purchase_order doc ON doc.id = a.res_id', "doc.state IN ('draft', 'sent')"),
            self._activity_query(
                'convert', 'procurement.request', 'aiis_procurement_request', 'mail_activity_procurement',
                'JOIN procurement_request doc ON doc.id = a.res_id',
                "doc.state = 'approved' AND doc.purchase_order_id IS NULL"),
            self._activity_query(
                'bill', 'account.move', 'aiis_procurement_request', 'mail_activity_pay_bill',
                'JOIN account_move doc ON doc.id = a.res_id '
                'LEFT JOIN purchase_order plan_order ON plan_order.id = doc.aiis_purchase_order_id '
                'LEFT JOIN res_company plan_company ON plan_company.id = doc.company_id '
                'LEFT JOIN res_partner plan_partner ON plan_partner.id = plan_company.partner_id '
                + self._finance_activity_join(),
                "doc.move_type = 'in_invoice' AND doc.state IN ('draft', 'posted') "
                "AND doc.payment_state NOT IN ('paid', 'in_payment', 'reversed') "
                """AND (NOT COALESCE(plan_order.aiis_payment_managed, FALSE) OR doc.state = 'draft' OR EXISTS (
                    SELECT 1 FROM aiis_purchase_payment_stage stage
                    WHERE stage.order_id = plan_order.id
                      AND stage.cumulative_amount > doc.amount_total - doc.amount_residual + 0.000001
                      AND stage.due_date <= (CURRENT_TIMESTAMP AT TIME ZONE COALESCE(plan_partner.tz, 'Asia/Riyadh'))::date
                      AND NOT EXISTS (
                          SELECT 1 FROM aiis_purchase_payment_stage prev
                          WHERE prev.order_id = stage.order_id AND prev.sequence < stage.sequence
                            AND prev.cumulative_amount > doc.amount_total - doc.amount_residual + 0.000001)
                ))"""),
            self._activity_query(
                'receipt', 'purchase.order', 'aiis_procurement_request', 'mail_activity_receive_order',
                'JOIN purchase_order doc ON doc.id = a.res_id',
                """doc.state IN ('purchase', 'done') AND (
                    (NOT doc.aiis_payment_managed AND doc.aiis_payment_status = 'paid') OR
                    (doc.aiis_payment_managed AND
                        COALESCE((SELECT sum(b.amount_total - b.amount_residual) FROM account_move b
                                  WHERE b.aiis_purchase_order_id = doc.id AND b.move_type = 'in_invoice'
                                    AND b.state = 'posted' AND b.payment_state != 'reversed'), 0)
                        >= COALESCE((SELECT sum(s.amount) FROM aiis_purchase_payment_stage s
                                     WHERE s.order_id = doc.id AND s.trigger = 'approval'), 0) - 0.000001))
                   AND EXISTS (SELECT 1 FROM purchase_order_line line
                               WHERE line.order_id = doc.id AND line.display_type IS NULL
                                 AND line.product_qty > line.qty_received)"""),
        ]
