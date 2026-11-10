/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { MisReportWidget } from "@mis_builder/components/mis_report_widget.esm";

// Odoo 17 field values are in record.data[name]. Keep vendored OCA intact.
patch(MisReportWidget.prototype, {
    _instanceId() {
        const record = this.props.record;
        const value = record.data[this.props.name];
        if (Number.isInteger(value) && value > 0) {
            return value;
        }
        if (record.resModel === "mis.report.instance" && record.resId) {
            return record.resId;
        }
        return super._instanceId();
    },

    get context() {
        return { ...this.props.record.context, ...super.context };
    },
});
