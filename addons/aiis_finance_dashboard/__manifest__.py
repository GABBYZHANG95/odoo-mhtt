{
    'name': 'MHTT Finance Dashboard',
    'version': '17.0.3.1.0',
    'category': 'Accounting/Accounting',
    'summary': '公司出入账（实际收付款口径）：Revenue / Cost / Expense（按物品类型）/ Profit',
    'description': """
AIIS 公司出入账（Company Cash Flow，现金收付实现制）
======================================================

在 Finance 里新增一个"公司出入账"功能块，基于**实际发生的收付款**
（account.payment，而不是开票日期）自动生成一份现金流台账：

- **Revenue（收入）** = 实际收到客户的钱（客户退款自动冲减）
- **Cost（成本）** = 采购来直接用于转卖给客户的东西花的钱（比如转售的软件
  许可证）——跟单子直接挂钩
- **Expense（内部运营开支）** = 跟单子无关的公司日常开支，按物品类型分类：
  - Labor Cost（人力成本）
  - Office Supplies（办公用品，含设备/家具，买了就一次性计入，不再折旧）
  - IT & Software Services（IT软件服务）
  - Employee Reimbursement（员工报销，走 Expenses App 提交）
  - Other Administrative Expenses（其他行政费用，兜底分类）
- **Profit（利润）** = Revenue − Cost − Expense

Cost 和 Expense 的区分，直接用的是 Odoo 会计科目自带的类型
（`expense_direct_cost` = Cost，`expense` = Expense），不是额外发明的逻辑；
"按物品类型"细分，靠的是每个 Expense 类型各自对应一个独立的会计科目，报表
按科目/分类分组即可，不需要额外的标签体系。

每一笔付款都会通过 Odoo 自身的核销/对账记录，自动追溯到它实际结清的那张
发票/账单：如果一张供应商账单里同时有多种类型的采购项（比如一半是 IT
设备、一半是办公用品），付款金额会按每类的占比自动拆分。客户收款同理，
会追溯到对应客户发票上的项目（Analytic Account），这样 Revenue 也能按
项目分组。

**Overall Profit** 统计页现在按 (日期 / 项目 / 部门) 这三个维度生成明细，
可以在 Pivot 视图里自由选择按项目还是按部门分组查看 Revenue/Cost/Expense/
Profit，并用搜索栏自带的日期筛选按月/季度/年过滤。Cost/Expense 的部门，
取自发起这笔采购的采购申请（aiis_procurement_request）所属部门；Labor
Cost 的部门直接取自该员工所在部门。Revenue 本身不归属于某个部门（钱不是
哪个部门的），所以按部门分组时 Revenue 会落在"无部门"里，这是预期行为。

说明：Odoo Community 版没有 Enterprise 的完整财务报表模块，这里用标准会计
科目搭了一个免费但实用的版本，满足 10 人以内小公司日常"看现金流、看各类
开支"的需求。
    """,
    'author': 'MHTT',
    'depends': [
        'account',
        'purchase',
        'hr_expense',
        'hr_contract',
        'analytic',
        'aiis_department_menu',
        'aiis_procurement_request',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/account_data.xml',
        'data/product_category_data.xml',
        'data/hr_expense_data.xml',
        'data/payroll_data.xml',
        'views/finance_entry_views.xml',
        'views/finance_summary_views.xml',
        'views/menu.xml',
    ],
    'demo': [
        'data/product_category_demo.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
