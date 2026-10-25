import { Component, onWillDestroy, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formatCurrency } from "@web/core/currency";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

const BUS_EVENT = "wealth_portfolio/updated";
const RELOAD_DEBOUNCE_MS = 400;
const FLASH_MS = 2500;

/**
 * Live dashboard.
 *
 * Data flow (important for the security story):
 *   server --bus ping {portfolio_id}--> browser --RPC get_dashboard_data--> server
 * The ping carries no financial data. The browser always *pulls* through the
 * ORM, so record rules decide what each user may see.
 */
export class WealthDashboard extends Component {
    static template = "wealth_portfolio.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.bus = useService("bus_service");

        this.state = useState({
            loading: true,
            refreshing: false,
            error: null,
            portfolios: [],
            syncing: null, // id of the portfolio being synced
            flashed: {}, // portfolio id -> "up" | "down" for a short highlight
            updatedAt: null,
        });
        this._reloadTimer = null;
        this._flashTimer = null;

        this._onBusUpdate = () => this.scheduleReload();
        this.bus.subscribe(BUS_EVENT, this._onBusUpdate);
        // The bus is normally started by the web client; this is a no-op safeguard.
        this.bus.start();

        onWillStart(() => this.load());
        onWillDestroy(() => {
            this.bus.unsubscribe(BUS_EVENT, this._onBusUpdate);
            browser.clearTimeout(this._reloadTimer);
            browser.clearTimeout(this._flashTimer);
        });
    }

    // ------------------------------------------------------------------ data
    async load() {
        const firstLoad = this.state.loading;
        if (!firstLoad) {
            this.state.refreshing = true;
        }
        try {
            const portfolios = await this.orm.call("portfolio.portfolio", "get_dashboard_data", []);
            this.markChanges(portfolios);
            this.state.portfolios = portfolios;
            this.state.error = null;
            this.state.updatedAt = new Date();
        } catch (error) {
            // Keep showing the last good data; surface the problem.
            this.state.error = error?.data?.message || error?.message || _t("Could not load portfolios.");
        } finally {
            this.state.loading = false;
            this.state.refreshing = false;
        }
    }

    /** Debounce: a price sync can ping several times in a row. */
    scheduleReload() {
        browser.clearTimeout(this._reloadTimer);
        this._reloadTimer = browser.setTimeout(() => this.load(), RELOAD_DEBOUNCE_MS);
    }

    markChanges(next) {
        const before = new Map(this.state.portfolios.map((p) => [p.id, p.total_value]));
        const flashed = {};
        for (const portfolio of next) {
            if (before.has(portfolio.id) && before.get(portfolio.id) !== portfolio.total_value) {
                flashed[portfolio.id] = portfolio.total_value > before.get(portfolio.id) ? "up" : "down";
            }
        }
        if (Object.keys(flashed).length) {
            this.state.flashed = flashed;
            browser.clearTimeout(this._flashTimer);
            this._flashTimer = browser.setTimeout(() => (this.state.flashed = {}), FLASH_MS);
        }
    }

    // --------------------------------------------------------------- display
    money(amount, portfolio) {
        return formatCurrency(amount, portfolio.currency_id);
    }

    signedMoney(amount, portfolio) {
        const text = this.money(Math.abs(amount), portfolio);
        if (amount > 0) {
            return `+${text}`;
        }
        return amount < 0 ? `−${text}` : text;
    }

    pct(value) {
        if (value === null || value === undefined) {
            return "";
        }
        const sign = value > 0 ? "+" : value < 0 ? "−" : "";
        return `${sign}${Math.abs(value).toFixed(1)}%`;
    }

    trend(portfolio) {
        if (portfolio.pnl > 0) return "up";
        if (portfolio.pnl < 0) return "down";
        return "flat";
    }

    canEdit(portfolio) {
        return portfolio.role === "owner" || portfolio.role === "editor";
    }

    /** One total per currency: amounts in different currencies must never be added. */
    get totalsByCurrency() {
        const totals = new Map();
        for (const p of this.state.portfolios) {
            const entry = totals.get(p.currency_id) || { portfolio: p, value: 0, pnl: 0 };
            entry.value += p.total_value;
            entry.pnl += p.pnl;
            totals.set(p.currency_id, entry);
        }
        return [...totals.values()];
    }

    /** "2026-10-24 05:25:40" (UTC, as the server sends it) -> the user's local date and time. */
    localTime(utcString) {
        const date = new Date(utcString.replace(" ", "T") + "Z");
        return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    }

    get updatedLabel() {
        return this.state.updatedAt ? this.state.updatedAt.toLocaleTimeString() : "";
    }

    // --------------------------------------------------------------- actions
    openPortfolio(portfolio) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "portfolio.portfolio",
            res_id: portfolio.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async syncPrices(portfolio) {
        this.state.syncing = portfolio.id;
        try {
            const notification = await this.orm.call("portfolio.portfolio", "action_sync_prices", [[portfolio.id]]);
            await this.action.doAction(notification);
        } catch (error) {
            this.notification.add(error?.data?.message || _t("Price sync failed."), { type: "danger" });
        } finally {
            this.state.syncing = null;
            await this.load();
        }
    }

    newPortfolio() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "portfolio.portfolio",
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("wealth_portfolio.dashboard", WealthDashboard);
