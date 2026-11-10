from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Preserve existing assignments; classify only workflow-created reminders."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    todo_type = env.ref('mail.mail_activity_data_todo').id
    for activity_name, model, condition in [
        ('mail_activity_pay_bill', 'account.move', """
            a.summary LIKE 'Pay %%' AND EXISTS (
                SELECT 1 FROM account_move_line l
                WHERE l.move_id = a.res_id AND l.purchase_line_id IS NOT NULL)
        """),
        ('mail_activity_receive_order', 'purchase.order', "a.summary LIKE 'Track shipment of %%'"),
    ]:
        cr.execute(f"""
            UPDATE mail_activity a SET activity_type_id = %s
            FROM ir_model m
            WHERE m.id = a.res_model_id AND m.model = %s
              AND a.activity_type_id = %s AND ({condition})
        """, [env.ref('aiis_procurement_request.' + activity_name).id, model, todo_type])
