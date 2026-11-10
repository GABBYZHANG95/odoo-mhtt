from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        UPDATE mail_activity a SET activity_type_id = %s
        FROM ir_model m, hr_expense_sheet sheet
        WHERE m.id = a.res_model_id AND m.model = 'hr.expense.sheet'
          AND sheet.id = a.res_id AND sheet.aiis_sales_request_id IS NOT NULL
          AND a.activity_type_id = %s AND a.summary LIKE 'Post and pay %%'
    """, [env.ref('aiis_sales_expense.mail_activity_pay_expense').id,
          env.ref('mail.mail_activity_data_todo').id])
