/** @odoo-module **/

import { registry } from "@web/core/registry";
import { AdkSound } from "./sound";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";

const REFRESH_MS = 4000;
const WARN_MIN = 10;
const LATE_MIN = 20;

export class AdkPosKitchen extends Component {
    static template = "adk_pos.Kitchen";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            configs: [],
            configId: null,
            stations: [],
            stationId: false,
            tickets: [],
            sound: true,
            audioBlocked: false,
            flash: false,
            now: Date.now(),
        });
        this.knownKeys = new Set();
        this.firstLoad = true;

        onWillStart(async () => {
            const configs = await this.orm.searchRead("adk.pos.config", [["active", "=", true]], ["name", "ring_order", "ring_volume", "ring_order_url", "ring_alarm_seconds", "ring_repeat"], {
                order: "sequence",
            });
            this.state.configs = configs;
            if (configs.length) {
                await this.selectConfig(configs[0].id);
            }
            this.state.loading = false;
        });
        onMounted(() => {
            this.baseTitle = document.title;
            this.alarmTick = setInterval(() => this.alarmStep(), 1000);
            this.timer = setInterval(() => this.refresh(), REFRESH_MS);
            this.tick = setInterval(() => (this.state.now = Date.now()), 30000);
        });
        onWillUnmount(() => {
            clearInterval(this.alarmTick);
            AdkSound.stopAlarm();
            if (this.baseTitle) {
                document.title = this.baseTitle;
            }
            clearInterval(this.timer);
            clearInterval(this.tick);
        });
    }

    async onConfigChange(ev) {
        await this.selectConfig(parseInt(ev.target.value, 10));
    }

    async selectConfig(configId) {
        this.state.configId = configId;
        this.state.stationId = false;
        this.knownKeys = new Set();
        this.firstLoad = true;
        this.state.stations = await this.orm.call("adk.pos.station", "get_stations_for_user", [configId]);
        if (this.state.stations.length === 1) {
            this.state.stationId = this.state.stations[0].id;
        }
        await this.refresh();
    }

    async selectStation(stationId) {
        this.state.stationId = stationId;
        this.knownKeys = new Set();
        this.firstLoad = true;
        await this.refresh();
    }

    async refresh() {
        if (!this.state.configId) {
            return;
        }
        try {
            const tickets = await this.orm.call("adk.pos.kitchen.item", "get_board", [
                this.state.configId,
                this.state.stationId,
            ]);
            let hasNew = false;
            for (const t of tickets) {
                if (!this.knownKeys.has(t.key)) {
                    this.knownKeys.add(t.key);
                    hasNew = hasNew || !this.firstLoad;
                }
            }
            this.firstLoad = false;
            this.state.tickets = tickets;
            this.updateAlarm();
        } catch {
            // Réseau coupé : on réessaiera au prochain cycle
        }
    }

    get ring() {
        const cfg = this.state.configs.find((c) => c.id === this.state.configId) || {};
        return [cfg.ring_order, cfg.ring_volume, cfg.ring_order_url];
    }

    /** Un ticket attend d'être « Reçu » (ou une annulation d'être vue). */
    get pendingTickets() {
        return this.state.tickets.filter((t) => t.state === "cancelled" || !t.received);
    }

    /**
     * Alarme continue : tant qu'un ticket n'est pas marqué « J'ai vu » (ou pris en préparation),
     * la sonnerie se répète sans s'arrêter, même si le cuisinier / barman / pâtissier est occupé.
     * Un minuteur d'1 seconde surveille l'état : il ne dépend ni du rafraîchissement ni d'un clic.
     */
    updateAlarm() {
        this.alarmStep();
    }

    alarmStep() {
        const pending = this.pendingTickets.length;
        const active = pending > 0 && this.state.sound;
        this.state.flash = active;
        // Titre de l'onglet clignotant : visible même si l'on regarde une autre fenêtre
        document.title = active && Math.floor(Date.now() / 1000) % 2 ? `🔔 ${pending} À RECEVOIR` : this.baseTitle;
        if (!active) {
            this.state.audioBlocked = false;
            this.alarmRan = false;
            AdkSound.stopAlarm();
            return;
        }
        const cfg = this.state.configs.find((c) => c.id === this.state.configId) || {};
        if (!AdkSound.alarmActive()) {
            // Alarme longue (2 min par défaut). Si elle est finie et que le ticket n'est toujours pas vu,
            // elle est relancée tant que l'option « Relancer » du point de vente est active.
            if (!this.alarmRan || cfg.ring_repeat !== false) {
                AdkSound.startAlarm(cfg.ring_order, cfg.ring_volume, cfg.ring_order_url, cfg.ring_alarm_seconds || 120);
                this.alarmRan = true;
            }
        }
        // Le navigateur bloque le son tant qu'on n'a pas touché la page : on le dit clairement
        this.state.audioBlocked = !AdkSound.running();
    }

    unlockAudio() {
        AdkSound.unlock();
        AdkSound.stopAlarm();
        this.alarmRan = false;
        this.alarmStep();
    }

    async receiveTicket(ticket) {
        const ids = ticket.items.filter((i) => i.state !== "cancelled").map((i) => i.id);
        if (ids.length) {
            await this.orm.call("adk.pos.kitchen.item", "mark_received", [ids]);
        }
        await this.refresh();
    }

    async receiveAll() {
        const ids = this.pendingTickets
            .filter((t) => t.state !== "cancelled")
            .flatMap((t) => t.items.filter((i) => i.state !== "cancelled").map((i) => i.id));
        if (ids.length) {
            await this.orm.call("adk.pos.kitchen.item", "mark_received", [ids]);
        }
        await this.refresh();
    }

    testSound() {
        AdkSound.unlock();
        AdkSound.ring("order", ...this.ring);
    }

    toggleSound() {
        this.state.sound = !this.state.sound;
        this.updateAlarm();
    }

    get groups() {
        const byStation = new Map();
        for (const t of this.state.tickets) {
            if (!byStation.has(t.station_id)) {
                const st = this.state.stations.find((x) => x.id === t.station_id) || {};
                byStation.set(t.station_id, {
                    id: t.station_id,
                    name: t.station_name,
                    color: t.station_color,
                    categories: st.categories || [],
                    tickets: [],
                });
            }
            byStation.get(t.station_id).tickets.push(t);
        }
        // Les postes sans ticket sont aussi affichés (section vide) en vue « Tous les postes »
        if (!this.state.stationId) {
            for (const st of this.state.stations) {
                if (!byStation.has(st.id)) {
                    byStation.set(st.id, { id: st.id, name: st.name, color: st.color, categories: st.categories || [], tickets: [] });
                }
            }
        }
        const order = new Map(this.state.stations.map((s, i) => [s.id, i]));
        return [...byStation.values()].sort((a, b) => (order.get(a.id) ?? 99) - (order.get(b.id) ?? 99));
    }

    get currentStation() {
        return this.state.stations.find((s) => s.id === this.state.stationId);
    }

    ticketClass(t) {
        if (t.state !== "cancelled" && !t.received) {
            return "adk_k_pending";
        }
        if (t.state === "ready") {
            return "adk_k_ready";
        }
        if (t.sent_min >= LATE_MIN) {
            return "adk_k_late";
        }
        if (t.sent_min >= WARN_MIN) {
            return "adk_k_warn";
        }
        return "adk_k_new";
    }

    stateLabel(st) {
        return { new: "Nouveau", preparing: "En cours", ready: "Prêt", cancelled: "ANNULÉ" }[st] || st;
    }

    activeIds(ticket) {
        return ticket.items.filter((i) => i.state !== "cancelled").map((i) => i.id);
    }

    async setItem(item, st) {
        await this.orm.call("adk.pos.kitchen.item", "set_state", [[item.id], st]);
        await this.refresh();
    }

    async setTicket(ticket, st) {
        const ids = ticket.items
            .filter((i) => i.state !== "cancelled" && (st !== "served" || i.state === "ready"))
            .map((i) => i.id);
        if (ids.length) {
            await this.orm.call("adk.pos.kitchen.item", "set_state", [ids, st]);
        }
        await this.refresh();
    }

    async bumpTicket(ticket) {
        const ids = this.activeIds(ticket);
        if (ids.length) {
            await this.orm.call("adk.pos.kitchen.item", "set_state", [ids, "served"]);
        }
        const cancelled = ticket.items.filter((i) => i.state === "cancelled").map((i) => i.id);
        if (cancelled.length) {
            await this.orm.call("adk.pos.kitchen.item", "acknowledge_cancel", [cancelled]);
        }
        await this.refresh();
    }

    async ackCancel(ticket) {
        const ids = ticket.items.filter((i) => i.state === "cancelled").map((i) => i.id);
        await this.orm.call("adk.pos.kitchen.item", "acknowledge_cancel", [ids]);
        await this.refresh();
    }
}

registry.category("actions").add("adk_pos_kitchen", AdkPosKitchen);
