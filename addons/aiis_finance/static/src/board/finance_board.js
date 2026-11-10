/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const PERIODS = [
    ["year", _t("Full year")],
    ["q1", "Q1"], ["q2", "Q2"], ["q3", "Q3"], ["q4", "Q4"],
    ...["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].map(
        (month, i) => [`m${i + 1}`, month]
    ),
];
const TITLES = {
    overview: _t("Finance Overview"),
    cash_flow: _t("Cash Flow"),
    checks: _t("Checks"),
};

/**
 * Finance › Overview, Reports › Cash Flow and Reports › Checks
 * (design doc 6.8.2, 6.8.4, 6.8.5). Figures come from aiis.finance.board.
 */
export class FinanceBoard extends Component {
    static template = "aiis_finance.FinanceBoard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.page = this.props.action.params?.page || "overview";
        this.periods = PERIODS;
        this.title = TITLES[this.page];
        this.state = useState({
            params: { year: new Date().getFullYear(), period: "year", cutoff: "", project_id: "", partner_id: "" },
            data: null,
            projects: [],
            partners: [],
            loading: true,
        });
        onWillStart(async () => {
            const [projects, partners] = await Promise.all([
                this.orm.searchRead("aiis.project", [], ["display_name"], { limit: 500 }),
                this.orm.searchRead("res.partner", [["customer_rank", ">", 0]], ["display_name"], { limit: 500 }),
            ]);
            this.state.projects = projects;
            this.state.partners = partners;
            await this.load();
        });
    }

    get methodName() {
        return { overview: "get_overview", cash_flow: "get_cash_flow", checks: "get_checks" }[this.page];
    }

    async load() {
        this.state.loading = true;
        const params = { ...this.state.params };
        this.state.data = await this.orm.call("aiis.finance.board", this.methodName, [params]);
        if (!this.state.params.cutoff) {
            this.state.params.cutoff = this.state.data.cutoff;
        }
        this.state.loading = false;
    }

    onChange(field, ev) {
        this.state.params[field] = ev.target.value;
        if (field === "year") {
            this.state.params.cutoff = "";
        }
        this.load();
    }

    format(value) {
        if (value === null || value === undefined || value === false) {
            return "";
        }
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
    }

    formatPct(value) {
        if (value === null || value === undefined) {
            return "";
        }
        return `${value.toFixed(1)}%`;
    }

    async drill(kind, quarter) {
        const action = await this.orm.call("aiis.finance.board", "open_drill", [kind, { ...this.state.params }, quarter || false]);
        this.action.doAction(action);
    }

    get cards() {
        const k = this.state.data.kpis;
        return [
            { key: "order_value", label: _t("Sales Order Value"), value: k.order_value,
              hint: _t("Confirmed sales orders, untaxed - not revenue, not cash") },
            { key: "revenue", label: _t("Recognized Revenue"), value: k.revenue,
              hint: _t("Posted to revenue accounts, as in the P&L") },
            { key: "gross_profit", label: _t("Gross Profit"), value: k.gross_profit,
              hint: k.gross_margin === null ? _t("Revenue - cost of revenue")
                  : `${_t("Gross margin")} ${this.formatPct(k.gross_margin)}` },
            { key: "backlog", label: _t("Order Backlog"), value: k.backlog,
              hint: _t("Signed orders not yet recognised, at the cutoff") },
        ];
    }

    get quarterRows() {
        return [
            { key: "order_value", label: _t("Sales Order Value") },
            { key: "revenue", label: _t("Recognized Revenue") },
            { key: "gross_profit", label: _t("Gross Profit") },
            { key: "gross_margin", label: _t("Gross margin"), pct: true },
            { key: "backlog", label: _t("Order Backlog (quarter end)") },
        ];
    }

    get cashTotals() {
        return [
            { key: "net", label: _t("Net cash change"), cls: "fw-bold" },
            { key: "closing", label: _t("Closing cash"), cls: "fw-bold o_aiis_total" },
        ];
    }

    /** SVG polylines: customer receipts vs project supplier payments per quarter */
    get chart() {
        const columns = this.state.data.columns.filter((c) => !c.future);
        const width = 560, height = 180, pad = 30;
        const series = [
            { key: "receipts", label: _t("Customer receipts"), cls: "o_aiis_line_receipts" },
            { key: "project", label: _t("Project supplier payments"), cls: "o_aiis_line_project" },
        ];
        const values = columns.flatMap((c) => series.map((s) => Math.abs(c[s.key])));
        const max = Math.max(1, ...values);
        const step = (width - 2 * pad) / 3;
        const x = (i) => pad + i * step;
        const y = (v) => height - pad - (Math.abs(v) / max) * (height - 2 * pad);
        return {
            width, height,
            labels: ["Q1", "Q2", "Q3", "Q4"].map((label, i) => ({ label, x: x(i), y: height - 8 })),
            series: series.map((s) => ({
                ...s,
                points: columns.map((c, i) => `${x(i)},${y(c[s.key])}`).join(" "),
                dots: columns.map((c, i) => ({ x: x(i), y: y(c[s.key]), title: this.format(Math.abs(c[s.key])) })),
            })),
        };
    }
}

registry.category("actions").add("aiis_finance_board", FinanceBoard);
