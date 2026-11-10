{
    'name': 'MHTT Department Menu',
    'version': '17.0.2.2.0',
    'category': 'Human Resources',
    'summary': '按部门（人力资源 / 财务 / 采购 / 销售）重新组织常用功能入口',
    'description': """
AIIS 部门导航菜单
==================

把已安装的 HR、Invoicing、Purchase、Sales 等 App 里最常用的功能，
按公司部门重新组织成四个顶级导航入口：

- 人力资源：员工管理、合同、考勤、请假、报销
- 财务：客户发票、供应商账单、收款、付款
- 采购：询价单、采购订单、供应商管理
- 销售：报价单、销售订单、客户合同审批

说明：这只是新增了按部门分组的快捷导航菜单，直接复用原有 App 的
功能和权限，不修改、不隐藏原有的 App 入口，两边可以同时使用。
    """,
    'author': 'MHTT',
    'depends': [
        'hr',
        'hr_contract',
        'hr_attendance',
        'hr_holidays',
        'hr_expense',
        'account',
        'purchase',
        'sale',
        'contacts',
        'calendar',
        'mail',
    ],
    'data': [
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'aiis_department_menu/static/src/search_model_patch.js',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
