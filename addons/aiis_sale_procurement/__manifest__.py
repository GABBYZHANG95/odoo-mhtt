{
    'name': 'MHTT Sale → Procurement',
    'version': '17.0.1.1.0',
    'category': 'Inventory/Purchase',
    'summary': '已确认的销售订单 → 采购申请（PR）→ 采购订单（PO），全程关联可追溯',
    'description': """
AIIS 销售订单关联采购
======================

把客户合同 / 销售订单和采购申请 / 采购订单串起来：

1. 已确认的 SO 上点"Create Purchase Request"，勾选需要外采的行，生成 PR
   （带出产品、数量、预计成本），PR 照常走直属主管审批
2. PR 转 PO 时，PO 行自动关联回 SO 行（复用 Odoo 原生 sale_purchase 的
   关联字段），SO 和 PO 上都有原生的 smart button 互相跳转
3. 每个 SO 行显示"已申请 / 已采购数量"，已经采购满的行不会重复带出；
   原生 sale_purchase 自动生成的 PO 也算在内，不会重复下单
4. 分析账户（项目）一路传下去：合同 → SO 行 → PR → PO 行，财务可以按
   项目对比收入和采购成本
""",
    'author': 'MHTT',
    'depends': [
        'sale_purchase',
        'aiis_customer_contract',
        'aiis_procurement_request',
    ],
    'data': [
        'security/ir.model.access.csv',
        'wizard/sale_order_create_pr_wizard_views.xml',
        'views/sale_order_views.xml',
        'views/procurement_request_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
