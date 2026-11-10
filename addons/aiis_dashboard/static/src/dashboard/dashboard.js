/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const PAGES = [
    ["overview", _t("Overview")],
    ["projects", _t("Projects & Sales")],
    ["expenses", _t("Expenses")],
    ["procurement", _t("Procurement")],
    ["approvals", _t("Approvals")],
    ["maintenance", _t("Maintenance & People")],
];
const PERIODS = [
    ["day", _t("Day")],
    ["month", _t("Month")],
    ["quarter", _t("Quarter")],
    ["year", _t("Year")],
];
// The first two follow the brand colour of this database (dashboard.scss)
const COLORS = ["var(--aiis-chart-1)", "var(--aiis-chart-2)", "#C27A1A", "#7A4FB5", "#8A968F"];
const CHART_HEIGHT = 170;

/**
 * Dashboard (design doc 6.9, #66): operating figures for the CEO and the
 * Chairman. The server sends each page as cards and blocks; this renders
 * them, switches pages and periods, and opens documents the viewer may open.
 */
export class OperationsDashboard extends Component {
    static template = "aiis_dashboard.OperationsDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.pages = PAGES;
        this.periods = PERIODS;
        this.colors = COLORS;
        this.state = useState({
            page: "overview",
            params: { period: "month", date: "" },
            data: null,
            loading: true,
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        const data = await this.orm.call("aiis.operations.dashboard", "get_page", [this.state.page, { ...this.state.params }]);
        this.state.data = data;
        this.state.params.date = data.period.date;
        this.state.loading = false;
    }

    setPage(page) {
        if (page !== this.state.page) {
            this.state.page = page;
            this.load();
        }
    }

    setPeriod(kind) {
        this.state.params.period = kind;
        this.load();
    }

    shift(direction) {
        const period = this.state.data.period;
        const date = direction < 0 ? period.prev_date : period.next_date;
        if (date) {
            this.state.params.date = date;
            this.load();
        }
    }

    thisPeriod() {
        this.state.params.date = "";
        this.load();
    }

    onFilter(key, ev) {
        this.state.params[key] = ev.target.value;
        this.load();
    }

    drill(bucket) {
        if (bucket.future || !bucket.drill) {
            return;
        }
        this.state.params.period = bucket.drill.period;
        this.state.params.date = bucket.drill.date;
        this.load();
    }

    open(cell) {
        if (!cell || !cell.open) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: cell.model,
            res_id: cell.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    blockClass(block) {
        return `o_aiis_dash_block o_aiis_dash_${block.width || "half"}`;
    }

    toneClass(tone) {
        return tone ? `o_aiis_tone_${tone}` : "";
    }

    compact(value, unit) {
        if (!value) {
            return "";
        }
        if (unit === "h") {
            return `${Math.round(value * 10) / 10} h`;
        }
        const abs = Math.abs(value);
        if (abs >= 1000000) {
            return `${(value / 1000000).toFixed(1)}M`;
        }
        if (abs >= 1000) {
            return `${(value / 1000).toFixed(abs >= 10000 ? 0 : 1)}k`;
        }
        return `${Math.round(value)}`;
    }

    /** Bar heights in px: stacked segments, or side by side when grouped */
    chartBuckets(block) {
        const buckets = block.chart.buckets;
        const top = Math.max(
            1,
            ...buckets.map((b) => (block.grouped ? Math.max(0, ...b.values) : b.total))
        );
        return buckets.map((bucket) => ({
            ...bucket,
            caption: this.compact(bucket.total, block.unit),
            segments: bucket.values.map((value, index) => ({
                index,
                color: COLORS[index % COLORS.length],
                height: value > 0 ? Math.max(2, Math.round((value / top) * CHART_HEIGHT)) : 0,
            })),
        }));
    }
}

registry.category("actions").add("aiis_operations_dashboard", OperationsDashboard);
