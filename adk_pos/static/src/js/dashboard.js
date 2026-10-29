/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";

export class AdkPosDashboard extends Component {
    static template = "adk_pos.Dashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            configs: [],
            configId: false,
            period: "today",
            data: null,
        });
        onWillStart(async () => {
            this.state.configs = await this.orm.searchRead("adk.pos.config", [], ["name"], { order: "sequence" });
            await this.load();
        });
        onMounted(() => {
            this.timer = setInterval(() => this.load(), 60000);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load() {
        this.state.data = await this.orm.call("adk.pos.config", "get_dashboard_data", [
            this.state.configId || false,
            this.state.period,
        ]);
        this.state.loading = false;
    }

    async setPeriod(period) {
        this.state.period = period;
        await this.load();
    }

    async onConfigChange(ev) {
        this.state.configId = parseInt(ev.target.value) || false;
        await this.load();
    }

    money(value) {
        const d = this.state.data;
        const num = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 }).format(value || 0);
        if (!d) {
            return num;
        }
        return d.currency_position === "after" ? `${num} ${d.currency_symbol}` : `${d.currency_symbol} ${num}`;
    }

    get donutStyle() {
        const pays = this.state.data.payments;
        const total = pays.reduce((a, p) => a + Math.max(p.total, 0), 0) || 1;
        const colors = ["#6c63ff", "#16a870", "#f59e0b", "#3b82f6", "#e5484d", "#b794f6"];
        let acc = 0;
        const stops = pays.map((p, i) => {
            const from = (acc / total) * 100;
            acc += Math.max(p.total, 0);
            return `${colors[i % colors.length]} ${from}% ${(acc / total) * 100}%`;
        });
        return `background: conic-gradient(${stops.join(", ")})`;
    }

    get maxHour() {
        return Math.max(...this.state.data.sales_by_hour.map((h) => h.total), 1);
    }

    get activeHours() {
        const hours = this.state.data.sales_by_hour;
        const used = hours.filter((h) => h.total > 0).map((h) => h.hour);
        if (!used.length) {
            return hours.filter((h) => h.hour >= 8 && h.hour <= 23);
        }
        const from = Math.max(Math.min(...used) - 1, 0);
        const to = Math.min(Math.max(...used) + 1, 23);
        return hours.filter((h) => h.hour >= from && h.hour <= to);
    }

    barHeight(total) {
        return Math.round((total / this.maxHour) * 100);
    }

    get paymentMax() {
        return Math.max(...this.state.data.payments.map((p) => p.total), 1);
    }

    openTerminal() {
        this.action.doAction("adk_pos.action_adk_pos_terminal");
    }

    openOrders() {
        this.action.doAction("adk_pos.action_adk_pos_order");
    }
}

registry.category("actions").add("adk_pos_dashboard", AdkPosDashboard);
