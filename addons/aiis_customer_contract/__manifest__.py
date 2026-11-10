{
    'name': 'MHTT Customer Contract',
    'version': '17.0.2.0.0',
    'category': 'Sales/Sales',
    'summary': '报价单 → 客户 PO → 合同审批（直属上级）→ 才能确认销售订单',
    'description': """
AIIS 客户合同审批（Customer Contract）
======================================

合同审批是"确认销售订单"前的一道闸门：

1. 报价单（Quotation）：在 Odoo 里做好报价发给客户
2. 客户回 PO：在报价单上点"Request Contract Approval"，生成合同申请，
   填客户 PO 号（写入报价单的 Customer Reference）并上传客户 PO 原件
3. 直属主管审批：复用 Employees 里的 Reporting To 汇报关系
4. 审批通过后，报价单才能确认为销售订单（SO）

合同不另存明细，直接显示报价单的行；提交时记录报价单总额，之后报价单
金额一旦变化，必须把合同退回草稿重新审批，才能确认 SO。

v1 的合同（合同 → 生成 SO）保留原有明细，状态显示为 SO Created (legacy)。
""",    'author': 'MHTT',
    'depends': [
        'sale',
        'hr',
        'analytic',
        'aiis_department_menu',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/customer_contract_security.xml',
        'data/ir_sequence_data.xml',
        'data/config_data.xml',
        'views/customer_contract_views.xml',
        'views/sale_order_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
