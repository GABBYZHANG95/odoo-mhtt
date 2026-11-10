{
    'name': 'MHTT Documents',
    'version': '17.0.1.0.1',
    'category': 'Sales/Sales',
    'summary': 'SO / PO 正式打印格式（品牌版式、签章栏）+ 销售订单一键导出 Excel',
    'description': """
AIIS 单据打印与导出
====================

1. 报表版式：统一使用公司 logo 和品牌深蓝（#233d7e / #122042）
2. 报价单 / 销售订单 PDF：显示客户 PO 号、合同编号、服务期间，末尾加
   双方签章栏（我方 / 客户：姓名、职务、签字、日期、盖章）
3. 采购订单 PDF：作为采购合同使用，显示付款条件、交货日期，末尾加
   双方签章栏（我方采购 / 供应商确认）
4. 销售订单导出 Excel：列表或表单里 动作 → Export to Excel，固定格式，
   含 Orders（每单一行）和 Lines（每行明细一行）两个工作表

合同条款请在 设置 → 会计 → 默认条款与条件（Default Terms & Conditions）里
维护，会自动带到每张新报价单上并打印出来。
""",
    'author': 'MHTT',
    'depends': [
        'aiis_sale_procurement',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/report_layout_data.xml',
        'data/sale_order_export_action.xml',
        'report/sale_order_report.xml',
        'report/purchase_order_report.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
