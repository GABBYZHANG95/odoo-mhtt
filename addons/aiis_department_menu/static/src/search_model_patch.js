/** @odoo-module **/
// Employees side panel: clicking a department lists only that department's
// own members, not everyone in its sub-departments (Odoo uses `child_of`
// for hierarchical panels). Server-side counters: models/hr_employee.py.
import { patch } from "@web/core/utils/patch";
import { SearchModel } from "@web/search/search_model";

const OWN_MEMBERS_FIELD = {
    "hr.employee": "department_id",
    "hr.employee.public": "department_id",
};

patch(SearchModel.prototype, {
    _getCategoryDomain(excludedCategoryId) {
        const domain = super._getCategoryDomain(...arguments);
        const fieldName = OWN_MEMBERS_FIELD[this.resModel];
        if (!fieldName) {
            return domain;
        }
        return domain.map(([field, operator, value]) =>
            field === fieldName && operator === "child_of" ? [field, "=", value] : [field, operator, value]
        );
    },
});
