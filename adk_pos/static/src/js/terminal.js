/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { AdkSound } from "./sound";
import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";

const POLL_MS = 5000;
const LOCK_AFTER_MS = 5 * 60 * 1000;
const COURSES = [
    { id: "drink", label: "Boisson" },
    { id: "starter", label: "Entrée" },
    { id: "main", label: "Plat" },
    { id: "dessert", label: "Dessert" },
    { id: "other", label: "Autre" },
];
const ORDER_TYPES = [
    { id: "dine_in", label: "Sur place", icon: "fa-cutlery" },
    { id: "takeaway", label: "À emporter", icon: "fa-shopping-bag" },
    { id: "delivery", label: "Livraison", icon: "fa-motorcycle" },
];
const SNAP = 10;

export class AdkPosTerminal extends Component {
    static template = "adk_pos.Terminal";

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.canvasRef = useRef("canvas");
        this.bgInputRef = useRef("bgInput");
        this.courses = COURSES;
        this.orderTypes = ORDER_TYPES;
        this.lastActivity = Date.now();
        this.prevReady = {};
        this.drag = null;

        this.state = useState({
            loading: true,
            busy: false,
            view: "select_config", // select_config | select_employee | open_session | main
            configs: [],
            cfg: null,
            employee: null, // {id, name, role}
            pendingEmployee: null,
            pinInput: "",
            pinError: "",
            session: null,
            tab: "floor", // floor | tickets | history | reservations
            // Plan de salle
            floors: [],
            tables: [],
            floorId: null,
            editMode: false,
            selectedTableId: null,
            dirtyTables: [],
            // Réservations
            reservations: [],
            // Listes
            queue: [],
            history: [],
            historySearch: "",
            // Commande en cours
            order: null,
            // Catalogue
            categories: [],
            products: [],
            categoryId: "all",
            search: "",
            // Fenêtres modales (une à la fois)
            modal: null, // nom de la modale
            m: {}, // données de la modale courante
            // Paiement
            pay: { open: false, lines: [], selected: null, tip: "", result: null },
            printKitchen: false,
            dark: this.loadDark(),
            printers: [],
            showMenu: false,
            approval: null,
            _tmp: { v: "" },
        });
        this.numpadKeys = ["7", "8", "9", "4", "5", "6", "1", "2", "3", "C", "0", "⌫"];
        this.numpadKeysDot = ["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "⌫"];

        onWillStart(async () => {
            await this.loadConfigs();
        });

        onMounted(() => {
            this.pollTimer = setInterval(() => this.poll(), POLL_MS);
            this.lockTimer = setInterval(() => this.checkLock(), 15000);
            this.activityHandler = () => {
                this.lastActivity = Date.now();
            };
            window.addEventListener("pointerdown", this.activityHandler);
            window.addEventListener("keydown", this.activityHandler);
            this.keyHandler = (ev) => this.onGlobalKeydown(ev);
            window.addEventListener("keydown", this.keyHandler);
            this.moveHandler = (ev) => this.onPointerMove(ev);
            this.upHandler = (ev) => this.onPointerUp(ev);
            window.addEventListener("pointermove", this.moveHandler);
            window.addEventListener("pointerup", this.upHandler);
        });
        onWillUnmount(() => {
            clearInterval(this.pollTimer);
            clearInterval(this.lockTimer);
            window.removeEventListener("pointerdown", this.activityHandler);
            window.removeEventListener("keydown", this.activityHandler);
            window.removeEventListener("keydown", this.keyHandler);
            window.removeEventListener("pointermove", this.moveHandler);
            window.removeEventListener("pointerup", this.upHandler);
        });
    }


    // =====================================================================
    // Thème, impression, matériel
    // =====================================================================
    loadDark() {
        try {
            return window.localStorage.getItem("adk_pos_dark") === "1";
        } catch (e) {
            return false;
        }
    }

    toggleDark() {
        this.state.dark = !this.state.dark;
        try {
            window.localStorage.setItem("adk_pos_dark", this.state.dark ? "1" : "0");
            window.localStorage.setItem("adk_pos_dark_set", "1");
        } catch (e) {
            /* stockage indisponible : le thème reste valable pour cette session */
        }
    }

    get themeClass() {
        const cfg = this.state.cfg;
        const classes = [];
        let dark = this.state.dark;
        try {
            if (cfg && !window.localStorage.getItem("adk_pos_dark_set")) {
                dark = cfg.ui_theme === "dark";
            }
        } catch (e) {
            /* ignoré */
        }
        if (dark) {
            classes.push("adk_dark");
        }
        if (cfg && cfg.big_scrollbars) {
            classes.push("adk_big_scroll");
        }
        return classes.join(" ");
    }

    browserUrl(job, id, extra = {}) {
        if (job === "receipt") {
            return `/adk_pos/receipt/${id}`;
        }
        if (job === "basic_receipt") {
            return `/adk_pos/receipt/${id}?basic=1`;
        }
        if (job === "bill") {
            return `/adk_pos/bill/${id}`;
        }
        if (job === "kitchen") {
            return `/adk_pos/kitchen_ticket/${id}/${extra.batch}`;
        }
        if (job === "report") {
            return `/adk_pos/z_report/${id}`;
        }
        return null;
    }

    /** Impression : imprimantes configurées (réseau, USB, série, passerelle) sinon navigateur. */
    async printJob(job, id, extra = {}, printerId = false) {
        const cfg = this.state.cfg;
        if (cfg && cfg.has_printers) {
            let res;
            try {
                res = await this.orm.call("adk.pos.printer", "terminal_print", [cfg.id, job, id, extra, printerId]);
            } catch (e) {
                this.notify("Impression impossible : " + ((e.data && e.data.message) || e.message || e), "danger");
                return;
            }
            for (const err of res.errors) {
                this.notify(`${err.printer} : ${err.error}`, "danger");
            }
            if (res.printed.length) {
                this.notify("Imprimé : " + res.printed.join(", "));
            }
            for (const action of res.actions) {
                if (action.mode === "browser") {
                    const url = this.browserUrl(job, id, extra);
                    if (url) {
                        window.open(url, "_blank");
                    }
                } else {
                    try {
                        await this.sendToDevice(action);
                        this.notify(`Imprimé : ${action.printer}`);
                    } catch (e) {
                        this.notify(`${action.printer} : ${e.message || e}`, "danger");
                    }
                }
            }
            if (res.has_printers) {
                return;
            }
        }
        const url = this.browserUrl(job, id, extra);
        if (url) {
            window.open(url, "_blank");
        }
    }

    async sendToDevice(action) {
        const bytes = Uint8Array.from(atob(action.data), (c) => c.charCodeAt(0));
        if (action.mode === "webusb") {
            return this.sendUsb(action, bytes);
        }
        if (action.mode === "webserial") {
            return this.sendSerial(action, bytes);
        }
        if (action.mode === "bridge") {
            const resp = await fetch(action.bridge_url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ printer: action.bridge_printer, data: action.data }),
            });
            if (!resp.ok) {
                throw new Error(`passerelle : erreur ${resp.status}`);
            }
            return;
        }
        throw new Error("mode inconnu");
    }

    async sendUsb(action, bytes) {
        if (!navigator.usb) {
            throw new Error("USB non disponible : utilisez Chrome ou Edge (HTTPS requis).");
        }
        this.usbDevices = this.usbDevices || {};
        let device = this.usbDevices[action.printer_id];
        if (!device) {
            const known = await navigator.usb.getDevices();
            let saved = null;
            try {
                saved = JSON.parse(window.localStorage.getItem("adk_usb_" + action.printer_id) || "null");
            } catch (e) {
                saved = null;
            }
            device = saved && known.find((d) => d.vendorId === saved.v && d.productId === saved.p);
            if (!device) {
                device = await navigator.usb.requestDevice({ filters: [] });
                try {
                    window.localStorage.setItem(
                        "adk_usb_" + action.printer_id,
                        JSON.stringify({ v: device.vendorId, p: device.productId })
                    );
                } catch (e) {
                    /* ignoré */
                }
            }
            this.usbDevices[action.printer_id] = device;
        }
        if (!device.opened) {
            await device.open();
        }
        if (device.configuration === null) {
            await device.selectConfiguration(1);
        }
        let target = null;
        for (const iface of device.configuration.interfaces) {
            for (const alt of iface.alternates) {
                const ep = alt.endpoints.find((e) => e.direction === "out");
                if (ep && (alt.interfaceClass === 7 || !target)) {
                    target = { iface: iface.interfaceNumber, ep: ep.endpointNumber };
                }
            }
        }
        if (!target) {
            throw new Error("aucune sortie USB trouvée sur cet appareil.");
        }
        try {
            await device.claimInterface(target.iface);
        } catch (e) {
            /* déjà réservée par nous */
        }
        for (let i = 0; i < bytes.length; i += 4096) {
            await device.transferOut(target.ep, bytes.slice(i, i + 4096));
        }
    }

    async sendSerial(action, bytes) {
        if (!navigator.serial) {
            throw new Error("Port série / Bluetooth non disponible : utilisez Chrome ou Edge.");
        }
        this.serialPorts = this.serialPorts || {};
        let port = this.serialPorts[action.printer_id];
        if (!port) {
            const granted = await navigator.serial.getPorts();
            port = granted.length === 1 ? granted[0] : await navigator.serial.requestPort();
            this.serialPorts[action.printer_id] = port;
        }
        if (!port.readable) {
            await port.open({ baudRate: action.baud || 9600 });
        }
        const writer = port.writable.getWriter();
        try {
            await writer.write(bytes);
        } finally {
            writer.releaseLock();
        }
    }

    async openPrinters() {
        this.state.printers = await this.orm.call("adk.pos.printer", "terminal_printers", [this.state.cfg.id]);
        this.openModal("printers");
    }

    async testPrinter(printer) {
        await this.printJob("test", 0, {}, printer.id);
    }

    async openCashDrawer() {
        if (!this.state.cfg.has_printers) {
            this.notify("Aucune imprimante configurée pour ouvrir le tiroir.", "warning");
            return;
        }
        await this.printJob("drawer", 0);
    }

    get cartLines() {
        const lines = this.state.order ? this.state.order.lines : [];
        if (this.state.cfg && this.state.cfg.sort_cart_by_category) {
            return [...lines].sort((a, b) => (a.categ || "").localeCompare(b.categ || ""));
        }
        return lines;
    }

    phClass(product) {
        return "adk_ph_" + (product.categ_id ? product.categ_id[0] % 8 : 0);
    }

    openSetupWizard() {
        this.action.doAction("adk_pos.action_adk_pos_setup_wizard");
    }

    async chargeTerminal(index) {
        const pay = this.state.pay;
        const line = pay.lines[index];
        if (!line || !(line.amount > 0)) {
            this.notify("Saisissez d'abord un montant.", "warning");
            return;
        }
        this.state.busy = true;
        line.terminal_status = "wait";
        let res = null;
        try {
            res = await this.orm.call("adk.pos.payment.method", "terminal_charge", [
                line.method_id,
                line.amount,
                this.state.order.name,
                this.state.order.customer_phone || "",
            ]);
        } catch (e) {
            this.notify((e.data && e.data.message) || e.message || "Terminal injoignable.", "danger");
        }
        this.state.busy = false;
        if (res && res.ok) {
            line.reference = res.reference || line.reference;
            line.terminal_status = "ok";
            this.notify("Paiement confirmé par le terminal.");
        } else {
            line.terminal_status = "";
            if (res) {
                this.notify(res.message, "danger");
            }
        }
    }

    // =====================================================================
    // Utilitaires
    // =====================================================================
    get isManager() {
        return this.state.employee && this.state.employee.role === "manager";
    }
    get isCashier() {
        return this.state.employee && ["cashier", "manager"].includes(this.state.employee.role);
    }

    formatMoney(value) {
        const cfg = this.state.cfg;
        const decimals = cfg ? cfg.currency.decimals : 0;
        const num = new Intl.NumberFormat("fr-FR", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        }).format(value || 0);
        if (!cfg) {
            return num;
        }
        return cfg.currency.position === "after"
            ? `${num} ${cfg.currency.symbol}`
            : `${cfg.currency.symbol} ${num}`;
    }

    ageLabel(min) {
        if (min < 1) {
            return "à l'instant";
        }
        if (min < 60) {
            return `${min} min`;
        }
        return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")}`;
    }

    stateLabel(st) {
        return (
            {
                draft: "Brouillon",
                sent: "À valider",
                billing: "En facturation",
                paid: "Payée",
                cancelled: "Annulée",
            }[st] || st
        );
    }

    num(v) {
        const n = parseFloat(String(v).replace(",", "."));
        return isNaN(n) ? 0 : n;
    }

    errorMessage(e) {
        if (e && e.data && e.data.message) {
            return e.data.message;
        }
        if (e && e.message && typeof e.message === "string") {
            return e.message;
        }
        return "Une erreur est survenue.";
    }

    notify(message, type = "success") {
        this.notification.add(message, { type });
    }

    /** Appel serveur avec message d'erreur lisible ; retourne undefined en cas d'erreur. */
    async call(model, method, args = [], kwargs = {}) {
        try {
            return await this.orm.call(model, method, args, kwargs);
        } catch (e) {
            this.notify(this.errorMessage(e), "danger");
            return undefined;
        }
    }

    openModal(name, data = {}) {
        this.state.m = data;
        this.state.modal = name;
    }

    closeModal() {
        this.state.modal = null;
        this.state.m = {};
    }

    /** Pavé numérique générique : modifie la chaîne state.<a>.<b>. */
    numpadKey(path, key) {
        const [a, b] = path.split(".");
        const obj = this.state[a];
        let value = String(obj[b] ?? "");
        if (key === "C") {
            value = "";
        } else if (key === "⌫") {
            value = value.slice(0, -1);
        } else if (key === ".") {
            if (!value.includes(".")) {
                value = (value || "0") + ".";
            }
        } else if (key.startsWith("+")) {
            value = String((parseFloat(value) || 0) + parseFloat(key.slice(1)));
        } else {
            // Valeur pré-remplie (ex. convives) : le premier chiffre tapé la remplace
            value = obj.freshInput === b ? key : value + key;
        }
        if (obj.freshInput === b) {
            obj.freshInput = false;
        }
        obj[b] = value;
    }

    noop() {}

    /**
     * Clavier physique : chiffres, virgule/point, Retour arrière, Entrée, Échap pour le code PIN,
     * l'encaissement et tous les pavés numériques (comme sur l'écran tactile).
     */
    onGlobalKeydown(ev) {
        if (ev.ctrlKey || ev.metaKey || ev.altKey) {
            return;
        }
        const tag = ev.target && ev.target.tagName;
        const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (ev.target && ev.target.isContentEditable);
        let key = null;
        if (/^[0-9]$/.test(ev.key)) {
            key = ev.key;
        } else if (ev.key === "." || ev.key === ",") {
            key = ".";
        } else if (ev.key === "Backspace") {
            key = "⌫";
        } else if (ev.key === "Delete") {
            key = "C";
        }
        const enter = ev.key === "Enter";
        const escape = ev.key === "Escape";
        const s = this.state;
        // Dans un champ texte, on laisse la saisie normale (sauf Entrée pour valider l'écran PIN)
        const handle = (fn, confirm, cancel) => {
            if (typing && !(enter && confirm)) {
                return true;
            }
            if (key !== null && fn) {
                fn(key);
            } else if (enter && confirm) {
                confirm();
            } else if (escape && cancel) {
                cancel();
            } else {
                return false;
            }
            ev.preventDefault();
            return true;
        };
        if (s.view === "select_employee") {
            if (s.pendingEmployee) {
                handle((k) => this.pinKey(k), () => this.confirmPin(), () => this.cancelEmployee());
            }
            return;
        }
        if (s.view !== "main") {
            return;
        }
        if (s.approval) {
            handle((k) => this.approvalKey(k), () => this.confirmApproval(), () => this.cancelApproval());
            return;
        }
        if (s.pay && s.pay.open && !(s.pay.result && s.pay.result.paid)) {
            if (!s.modal) {
                handle((k) => this.payKey(k), () => this.confirmPayment(), () => this.closePayment());
            }
            return;
        }
        const numeric = {
            lineEdit: [(k) => this.lineEditKey(k), () => this.saveLineEdit()],
            globalDiscount: [(k) => this.numpadKey("m.percent", k), () => this.applyGlobalDiscount()],
            guests: [(k) => this.numpadKey("m.guests", k), () => this.saveGuests()],
            cashMove: [(k) => this.numpadKey("m.amount", k), () => this.confirmCashMove()],
        }[s.modal];
        if (numeric) {
            handle(numeric[0], numeric[1], () => this.closeModal());
        }
    }

    setM(key, value) {
        this.state.m[key] = value;
    }

    setCategory(id) {
        this.state.categoryId = id;
    }

    selectFloor(floor) {
        this.state.floorId = floor.id;
        this.state.selectedTableId = null;
    }

    clearSelection() {
        this.state.selectedTableId = null;
    }

    courseLabel(id) {
        const c = COURSES.find((x) => x.id === id);
        return c ? c.label : "";
    }

    async openOrderFromModal(orderId) {
        this.closeModal();
        await this.openOrder(orderId);
    }

    // =====================================================================
    // Boucle de rafraîchissement et verrouillage automatique
    // =====================================================================
    async poll() {
        if (document.hidden || this.state.view !== "main" || this.state.editMode) {
            return;
        }
        if (this.state.tab === "floor") {
            await this.refreshFloor(true);
        }
        await this.refreshQueue();
        if (this.state.tab === "reservations") {
            await this.refreshReservations();
        }
    }

    checkLock() {
        if (
            this.state.view === "main" &&
            Date.now() - this.lastActivity > LOCK_AFTER_MS &&
            !this.state.pay.open
        ) {
            this.lockTerminal();
        }
    }

    lockTerminal() {
        this.state.order = null;
        this.state.modal = null;
        this.state.employee = null;
        this.state.pendingEmployee = null;
        this.state.pinInput = "";
        this.state.view = "select_employee";
        this.notify("Terminal verrouillé par inactivité.", "info");
    }

    // =====================================================================
    // Étape 1 : choix du point de vente
    // =====================================================================
    async loadConfigs() {
        this.state.loading = true;
        const configs = await this.orm.searchRead(
            "adk.pos.config",
            [["active", "=", true]],
            ["name", "pos_type", "use_tables"],
            { order: "sequence" }
        );
        const openSessions = configs.length
            ? await this.orm.searchRead("adk.pos.session", [["state", "=", "opened"]], ["config_id"])
            : [];
        const openByConfig = {};
        for (const s of openSessions) {
            openByConfig[s.config_id[0]] = s;
        }
        const themes = ["adk_theme_violet", "adk_theme_teal", "adk_theme_orange", "adk_theme_pink"];
        const icons = {
            restaurant: "fa-cutlery",
            cafe: "fa-coffee",
            bar: "fa-glass",
            fastfood: "fa-hamburger",
            retail: "fa-shopping-bag",
            boutique: "fa-shopping-bag",
        };
        configs.forEach((c, idx) => {
            c.hasOpenSession = !!openByConfig[c.id];
            c.colorTheme = themes[idx % themes.length];
            c.iconClass = icons[c.pos_type] || "fa-shopping-bag";
        });
        this.state.configs = configs;
        this.state.loading = false;
    }

    async selectConfig(config) {
        const cfg = await this.call("adk.pos.config", "get_terminal_config", [config.id]);
        if (!cfg) {
            return;
        }
        this.state.cfg = cfg;
        this.state.employee = null;
        this.state.pendingEmployee = null;
        this.state.view = "select_employee";
    }

    backToConfigs() {
        this.state.cfg = null;
        this.state.employee = null;
        this.state.pendingEmployee = null;
        this.state.session = null;
        this.state.order = null;
        this.state.modal = null;
        this.state.view = "select_config";
        this.loadConfigs();
    }

    // =====================================================================
    // Étape 2 : identification par code PIN
    // =====================================================================
    chooseEmployee(emp) {
        this.state.pendingEmployee = emp;
        this.state.pinInput = "";
        this.state.pinError = "";
    }

    cancelEmployee() {
        this.state.pendingEmployee = null;
        this.state.pinInput = "";
        this.state.pinError = "";
    }

    pinKey(key) {
        if (key === "⌫") {
            this.state.pinInput = this.state.pinInput.slice(0, -1);
        } else if (key === "C") {
            this.state.pinInput = "";
        } else if (this.state.pinInput.length < 6) {
            this.state.pinInput += key;
        }
        this.state.pinError = "";
    }

    onPinKeydown(ev) {
        if (ev.key === "Enter") {
            this.confirmPin();
        }
    }

    async confirmPin() {
        const emp = this.state.pendingEmployee;
        if (!emp || this.state.pinInput.length < 4) {
            this.state.pinError = "Le code PIN contient 4 à 6 chiffres.";
            return;
        }
        const res = await this.call("res.users", "adk_pos_check_pin", [emp.id, this.state.pinInput]);
        this.state.pinInput = "";
        if (!res) {
            return;
        }
        if (!res.ok) {
            if (res.error === "locked") {
                this.state.pinError = `Code bloqué après trop d'essais. Réessayez dans ${res.minutes} min.`;
            } else {
                this.state.pinError =
                    res.remaining !== undefined
                        ? `Code PIN incorrect (${res.remaining} essai(s) restant(s)).`
                        : "Code PIN incorrect.";
            }
            return;
        }
        this.state.employee = { id: res.id, name: res.name, role: res.role };
        this.state.pendingEmployee = null;
        this.lastActivity = Date.now();
        await this.checkSessionAndProceed();
    }

    // =====================================================================
    // Étape 3 : session de caisse
    // =====================================================================
    async checkSessionAndProceed() {
        const session = await this.call("adk.pos.session", "get_opened_session", [this.state.cfg.id]);
        if (!session) {
            this.state.session = null;
            this.state.m = { balance: "", counts: {} };
            this.state.view = "open_session";
            return;
        }
        this.state.session = session;
        await this.enterMain();
    }

    countTotal(counts) {
        let total = 0;
        for (const [denom, qty] of Object.entries(counts || {})) {
            total += parseFloat(denom) * (parseInt(qty) || 0);
        }
        return total;
    }

    changeCount(denom, delta) {
        const counts = this.state.m.counts;
        counts[denom] = Math.max((parseInt(counts[denom]) || 0) + delta, 0);
        this.state.m.balance = String(this.countTotal(counts));
    }

    countDetails(counts) {
        return Object.entries(counts || {})
            .filter(([, q]) => parseInt(q) > 0)
            .sort((a, b) => parseFloat(b[0]) - parseFloat(a[0]))
            .map(([d, q]) => `${q} × ${d}`)
            .join(" · ");
    }

    async openSession() {
        const id = await this.call("adk.pos.session", "quick_open_session", [
            this.state.cfg.id,
            this.num(this.state.m.balance),
            this.state.employee.id,
            this.countDetails(this.state.m.counts),
        ]);
        if (!id) {
            return;
        }
        this.notify("Session de caisse ouverte !");
        this.state.session = { id };
        await this.enterMain();
    }

    async enterMain() {
        this.state.view = "main";
        this.state.modal = null;
        this.state.order = null;
        this.state.tab = this.state.cfg.use_tables ? "floor" : "tickets";
        await this.loadCatalog();
        if (this.state.cfg.use_tables) {
            await this.refreshFloor();
        }
        await this.refreshQueue();
    }

    // =====================================================================
    // Plan de salle
    // =====================================================================
    async refreshFloor(silent = false) {
        const data = await this.orm.call("adk.pos.table", "get_floor_data", [this.state.cfg.id]);
        // Alerte « prêt » : un plat vient d'être terminé par la cuisine
        for (const t of data.tables) {
            const prev = this.prevReady[t.id] || 0;
            if (silent && t.ready_count > prev) {
                this.notify(`🍽 Table ${t.name} : plat prêt à servir !`, "info");
                AdkSound.ring("ready", this.state.cfg.ring_ready, this.state.cfg.ring_volume, this.state.cfg.ring_ready_url);
            }
            this.prevReady[t.id] = t.ready_count;
        }
        this.state.floors = data.floors;
        this.state.tables = data.tables;
        if (!this.state.floors.find((f) => f.id === this.state.floorId)) {
            this.state.floorId = this.state.floors.length ? this.state.floors[0].id : null;
        }
    }

    get currentFloor() {
        return this.state.floors.find((f) => f.id === this.state.floorId) || null;
    }

    get tablesOfFloor() {
        return this.state.tables.filter((t) => t.floor_id === this.state.floorId);
    }

    tableStyle(t) {
        const f = this.currentFloor;
        if (!f) {
            return "";
        }
        return (
            `left:${(t.x / f.width) * 100}%;top:${(t.y / f.height) * 100}%;` +
            `width:${(t.w / f.width) * 100}%;height:${(t.h / f.height) * 100}%;`
        );
    }

    get canvasStyle() {
        const f = this.currentFloor;
        if (!f) {
            return "";
        }
        const bg = f.bg
            ? `background-image:url('${f.bg}');background-size:100% 100%;background-repeat:no-repeat;`
            : "";
        return `aspect-ratio:${f.width} / ${f.height};background-color:${f.color || "#F5F5F5"};${bg}`;
    }

    tableStateLabel(t) {
        return { free: "Libre", occupied: "Occupée", billing: "Addition", reserved: "Réservée" }[t.state];
    }

    async onTableClick(table) {
        if (this.state.editMode) {
            this.state.selectedTableId = table.id;
            return;
        }
        if (table.order_ids.length > 1) {
            this.openModal("tableOrders", { table });
            return;
        }
        if (table.order_ids.length === 1) {
            await this.openOrder(table.order_ids[0]);
            return;
        }
        if (table.state === "reserved") {
            this.openModal("reservedTable", { table });
            return;
        }
        await this.startOrderOnTable(table);
    }

    startOrderOnTable(table, forceNew = false) {
        // On demande toujours le nombre de convives avant d'ouvrir la commande d'une table
        this.openModal("guests", {
            guests: String(table.seats || 2),
            pendingTable: table,
            freshInput: "guests",
            forceNew,
        });
    }

    async createOrderOnTable(table, forceNew, guests) {
        this.closeModal();
        const id = await this.call("adk.pos.order", "terminal_create_order", [
            this.state.cfg.id,
            this.state.session.id,
            this.state.employee.id,
            table.id,
            "dine_in",
            guests || 1,
            forceNew,
        ]);
        if (id) {
            await this.openOrder(id);
        }
    }

    async freeReservedTable(table) {
        await this.orm.write("adk.pos.table", [table.id], { is_reserved: false, reservation_note: "" });
        this.closeModal();
        this.notify("Table libérée.");
        await this.refreshFloor();
    }

    // ----- Mode édition du plan (managers) -----
    toggleEditMode() {
        if (!this.isManager) {
            this.notify("Réservé aux managers.", "warning");
            return;
        }
        this.state.showMenu = false;
        this.state.modal = null;
        this.state.tab = "floor";
        this.state.editMode = !this.state.editMode;
        this.state.selectedTableId = null;
        this.state.dirtyTables = [];
        if (!this.state.editMode) {
            this.refreshFloor();
        } else if (this.state.floorId && !this.tablesOfFloor.length) {
            this.openGenTables();
        }
    }

    get selectedTable() {
        return this.state.tables.find((t) => t.id === this.state.selectedTableId) || null;
    }

    markDirty(table) {
        if (!this.state.dirtyTables.includes(table.id)) {
            this.state.dirtyTables.push(table.id);
        }
    }

    onTablePointerDown(ev, table, mode) {
        if (!this.state.editMode) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        this.state.selectedTableId = table.id;
        this.drag = {
            id: table.id,
            mode, // move | resize
            startX: ev.clientX,
            startY: ev.clientY,
            ox: table.x,
            oy: table.y,
            ow: table.w,
            oh: table.h,
        };
    }

    onPointerMove(ev) {
        if (!this.drag || !this.canvasRef.el) {
            return;
        }
        const f = this.currentFloor;
        const table = this.state.tables.find((t) => t.id === this.drag.id);
        if (!f || !table) {
            return;
        }
        const rect = this.canvasRef.el.getBoundingClientRect();
        const dx = ((ev.clientX - this.drag.startX) / rect.width) * f.width;
        const dy = ((ev.clientY - this.drag.startY) / rect.height) * f.height;
        const snap = (v) => Math.round(v / SNAP) * SNAP;
        if (this.drag.mode === "move") {
            table.x = Math.min(Math.max(snap(this.drag.ox + dx), 0), f.width - table.w);
            table.y = Math.min(Math.max(snap(this.drag.oy + dy), 0), f.height - table.h);
        } else {
            table.w = Math.min(Math.max(snap(this.drag.ow + dx), 50), f.width - table.x);
            table.h = Math.min(Math.max(snap(this.drag.oh + dy), 50), f.height - table.y);
        }
        this.markDirty(table);
    }

    onPointerUp() {
        this.drag = null;
    }

    changeTableSeats(delta) {
        const t = this.selectedTable;
        if (t) {
            t.seats = Math.max(t.seats + delta, 1);
            this.markDirty(t);
        }
    }

    setTableShape(shape) {
        const t = this.selectedTable;
        if (t) {
            t.shape = shape;
            this.markDirty(t);
        }
    }

    renameSelectedTable() {
        const t = this.selectedTable;
        if (t) {
            this.openModal("renameTable", { name: t.name });
        }
    }

    confirmRenameTable() {
        const t = this.selectedTable;
        if (t && this.state.m.name.trim()) {
            t.name = this.state.m.name.trim();
            this.markDirty(t);
        }
        this.closeModal();
    }

    openGenTables() {
        if (!this.state.floorId) {
            this.notify("Créez d'abord une salle (bouton « Nouvelle salle »).", "warning");
            return;
        }
        this.openModal("genTables", { count: 10, seats: 4, shape: "square", replace: false });
    }

    async confirmGenTables() {
        const m = this.state.m;
        const count = parseInt(m.count, 10);
        if (!count || count < 1) {
            this.notify("Indiquez le nombre de tables.", "warning");
            return;
        }
        const ok = await this.call("adk.pos.table", "terminal_generate_tables", [
            this.state.employee.id, this.state.floorId, count, parseInt(m.seats, 10) || 4,
            m.shape, "T", !!m.replace,
        ]);
        if (ok) {
            this.closeModal();
            this.state.dirtyTables = [];
            await this.refreshFloor();
            this.notify(`${count} table(s) générée(s) et rangée(s).`);
        }
    }

    async arrangeTables() {
        await this.saveLayout(true);
        const ok = await this.call("adk.pos.table", "terminal_arrange_tables", [
            this.state.employee.id, this.state.floorId,
        ]);
        if (ok) {
            await this.refreshFloor();
            this.notify("Tables rangées.");
        }
    }

    pickBackground() {
        if (this.bgInputRef.el) {
            this.bgInputRef.el.click();
        }
    }

    onBackgroundChosen(ev) {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!file) {
            return;
        }
        if (file.size > 8 * 1024 * 1024) {
            this.notify("Image trop lourde (8 Mo maximum).", "warning");
            return;
        }
        const reader = new FileReader();
        reader.onload = async () => {
            const b64 = String(reader.result).split(",")[1];
            const ok = await this.call("adk.pos.table", "terminal_set_background", [
                this.state.employee.id, this.state.floorId, b64,
            ]);
            if (ok) {
                await this.refreshFloor();
                this.notify("Image de fond enregistrée.");
            }
        };
        reader.readAsDataURL(file);
    }

    async removeBackground() {
        const ok = await this.call("adk.pos.table", "terminal_set_background", [
            this.state.employee.id, this.state.floorId, false,
        ]);
        if (ok) {
            await this.refreshFloor();
        }
    }

    openNewFloor() {
        this.openModal("newFloor", { name: "" });
    }

    async confirmNewFloor() {
        const id = await this.call("adk.pos.table", "terminal_add_floor", [
            this.state.employee.id, this.state.cfg.id, this.state.m.name,
        ]);
        if (id) {
            this.closeModal();
            await this.refreshFloor();
            this.state.floorId = id;
        }
    }

    async addTable() {
        if (!this.state.floorId) {
            this.notify("Créez d'abord une salle (Configuration → Points de vente).", "warning");
            return;
        }
        const id = await this.call("adk.pos.table", "terminal_add_table", [
            this.state.employee.id,
            this.state.floorId,
            {},
        ]);
        if (id) {
            await this.saveLayout(true);
            await this.refreshFloor();
            this.state.selectedTableId = id;
        }
    }

    async removeSelectedTable() {
        const t = this.selectedTable;
        if (!t) {
            return;
        }
        const ok = await this.call("adk.pos.table", "terminal_remove_table", [this.state.employee.id, t.id]);
        if (ok) {
            this.state.selectedTableId = null;
            await this.refreshFloor();
        }
    }

    async saveLayout(silent = false) {
        const changes = this.state.tables
            .filter((t) => this.state.dirtyTables.includes(t.id))
            .map((t) => ({
                id: t.id,
                name: t.name,
                seats: t.seats,
                shape: t.shape,
                pos_x: t.x,
                pos_y: t.y,
                width: t.w,
                height: t.h,
            }));
        if (changes.length) {
            const ok = await this.call("adk.pos.table", "terminal_save_layout", [
                this.state.employee.id,
                changes,
            ]);
            if (!ok) {
                return;
            }
        }
        this.state.dirtyTables = [];
        if (!silent) {
            this.state.editMode = false;
            this.state.selectedTableId = null;
            this.notify("Plan de salle enregistré.");
            await this.refreshFloor();
        }
    }

    async cancelLayoutEdit() {
        this.state.editMode = false;
        this.state.dirtyTables = [];
        this.state.selectedTableId = null;
        await this.refreshFloor();
    }

    // =====================================================================
    // Réservations
    // =====================================================================
    async refreshReservations() {
        if (!this.state.cfg.reservation_enabled) {
            return;
        }
        this.state.reservations = await this.orm.call("adk.pos.reservation", "terminal_list", [
            this.state.cfg.id,
        ]);
    }

    async showTab(tab) {
        this.state.tab = tab;
        this.state.showMenu = false;
        if (tab === "reservations") {
            await this.refreshReservations();
        } else if (tab === "history") {
            await this.refreshHistory();
        } else if (tab === "tickets") {
            await this.refreshQueue();
        } else if (tab === "floor") {
            await this.refreshFloor();
        }
    }

    todayStr() {
        const d = new Date();
        const p = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }

    newReservation() {
        const d = new Date(Date.now() + 60 * 60 * 1000);
        const p = (n) => String(n).padStart(2, "0");
        d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
        this.openModal("reservation", {
            customer_name: "",
            phone: "",
            guests: 2,
            date: this.todayStr(),
            time: `${p(d.getHours())}:${p(d.getMinutes())}`,
            duration: this.state.cfg.reservation_duration || 90,
            note: "",
            table_ids: [],
            tables: [],
        });
        this.loadAvailability();
    }

    async loadAvailability() {
        const m = this.state.m;
        if (!m.date || !m.time) {
            return;
        }
        const tables = await this.call("adk.pos.reservation", "terminal_available_tables", [
            this.state.cfg.id,
            m.date,
            m.time,
            m.duration,
        ]);
        if (tables) {
            m.tables = tables;
            m.table_ids = m.table_ids.filter((id) => tables.find((t) => t.id === id && t.available));
        }
    }

    changeGuests(delta) {
        this.state.m.guests = Math.max((parseInt(this.state.m.guests) || 1) + delta, 1);
    }

    toggleReservationTable(table) {
        if (!table.available) {
            return;
        }
        const ids = this.state.m.table_ids;
        const idx = ids.indexOf(table.id);
        if (idx >= 0) {
            ids.splice(idx, 1);
        } else {
            ids.push(table.id);
        }
    }

    async saveReservation() {
        const m = this.state.m;
        const id = await this.call("adk.pos.reservation", "terminal_create", [
            this.state.cfg.id,
            {
                customer_name: m.customer_name,
                phone: m.phone,
                guests: m.guests,
                date: m.date,
                time: m.time,
                duration: m.duration,
                note: m.note,
                table_ids: m.table_ids,
                source: "walkin",
            },
        ]);
        if (id) {
            this.closeModal();
            this.notify("Réservation enregistrée.");
            await this.refreshReservations();
            await this.refreshFloor();
        }
    }

    async seatReservation(res) {
        if (res.table_ids.length === 0) {
            await this.loadFreeTablesForSeating(res);
            return;
        }
        await this.doSeat(res, res.table_ids[0]);
    }

    async loadFreeTablesForSeating(res) {
        await this.refreshFloor();
        const free = this.state.tables.filter((t) => t.state === "free");
        this.openModal("seatChoose", { res, tables: free });
    }

    async doSeat(res, tableId) {
        this.closeModal();
        const id = await this.call("adk.pos.order", "terminal_create_from_reservation", [
            res.id,
            this.state.employee.id,
            this.state.session.id,
            tableId,
        ]);
        if (id) {
            this.notify(`${res.customer} installé(e).`);
            await this.openOrder(id);
        }
    }

    async setReservationState(res, st) {
        const ok = await this.call("adk.pos.reservation", "terminal_set_state", [res.id, st]);
        if (ok) {
            await this.refreshReservations();
            await this.refreshFloor();
        }
    }

    // =====================================================================
    // Listes de commandes (tickets en cours / historique)
    // =====================================================================
    async refreshQueue() {
        if (!this.state.cfg) {
            return;
        }
        this.state.queue = await this.orm.call("adk.pos.order", "terminal_orders", [
            this.state.cfg.id,
            "open",
            "",
        ]);
    }

    async refreshHistory() {
        this.state.history = await this.orm.call("adk.pos.order", "terminal_orders", [
            this.state.cfg.id,
            "paid",
            this.state.historySearch || "",
        ]);
    }

    onHistorySearch() {
        this.refreshHistory();
    }

    get kioskPending() {
        return this.state.queue.filter((o) => o.source === "kiosk" && o.state === "draft").length;
    }

    // =====================================================================
    // Catalogue
    // =====================================================================
    async loadCatalog() {
        const cfgId = this.state.cfg.id;
        const domain = [
            ["sale_ok", "=", true],
            "|",
            ["adk_pos_config_ids", "=", false],
            ["adk_pos_config_ids", "in", [cfgId]],
        ];
        if (this.state.cfg.category_ids && this.state.cfg.category_ids.length) {
            domain.push(["categ_id", "child_of", this.state.cfg.category_ids]);
        }
        const products = await this.orm.searchRead(
            "product.product",
            domain,
            ["name", "lst_price", "categ_id", "adk_pos_has_image", "barcode", "adk_pos_favorite", "default_code"],
            { limit: 2000, order: "name" }
        );
        const categs = new Map();
        for (const p of products) {
            categs.set(p.categ_id[0], p.categ_id[1]);
        }
        this.state.categories = [...categs.entries()]
            .map(([id, name]) => ({ id, name: name.split(" / ").pop() }))
            .sort((a, b) => a.name.localeCompare(b.name));
        this.state.products = products;
        this.state.categoryId = products.some((p) => p.adk_pos_favorite) ? "fav" : "all";
    }

    get hasFavorites() {
        return this.state.products.some((p) => p.adk_pos_favorite);
    }

    get filteredProducts() {
        const q = this.state.search.trim().toLowerCase();
        let list = this.state.products;
        if (q) {
            return list.filter(
                (p) =>
                    p.name.toLowerCase().includes(q) ||
                    (p.barcode && p.barcode.toLowerCase() === q) ||
                    (p.default_code && p.default_code.toLowerCase().includes(q))
            );
        }
        if (this.state.categoryId === "fav") {
            return list.filter((p) => p.adk_pos_favorite);
        }
        if (this.state.categoryId !== "all") {
            list = list.filter((p) => p.categ_id[0] === this.state.categoryId);
        }
        return list;
    }

    async onSearchKeydown(ev) {
        if (ev.key !== "Enter") {
            return;
        }
        const q = this.state.search.trim();
        const exact = this.state.products.find((p) => p.barcode && p.barcode === q);
        const single = this.filteredProducts.length === 1 ? this.filteredProducts[0] : null;
        const product = exact || single;
        if (product) {
            await this.addProduct(product);
            this.state.search = "";
        }
    }

    cartQtyFor(productId) {
        const o = this.state.order;
        if (!o) {
            return 0;
        }
        return o.lines.filter((l) => l.product_id === productId).reduce((a, l) => a + l.qty, 0);
    }

    // =====================================================================
    // Commande en cours
    // =====================================================================
    async openOrder(orderId) {
        const order = await this.call("adk.pos.order", "terminal_get_order", [orderId]);
        if (!order) {
            await this.refreshFloor();
            return;
        }
        this.state.order = order;
        this.state.search = "";
        this.state.pay = { open: false, lines: [], selected: null, tip: String(order.tip_amount || ""), result: null };
        this.state.tab = this.state.tab === "history" ? "history" : this.state.tab;
    }

    async closeOrder() {
        const o = this.state.order;
        if (o && o.state === "draft" && !o.lines.length) {
            await this.call("adk.pos.order", "terminal_discard_if_empty", [o.id]);
        }
        this.state.order = null;
        this.state.pay = { open: false, lines: [], selected: null, tip: "", result: null };
        this.state.modal = null;
        if (this.state.cfg.use_tables) {
            await this.refreshFloor();
        }
        await this.refreshQueue();
        if (this.state.tab === "history") {
            await this.refreshHistory();
        }
    }

    get readonlyOrder() {
        return !!this.state.order && ["paid", "cancelled"].includes(this.state.order.state);
    }

    async startQuickSale() {
        const id = await this.call("adk.pos.order", "terminal_create_order", [
            this.state.cfg.id,
            this.state.session.id,
            this.state.employee.id,
            false,
            "takeaway",
            1,
            true,
        ]);
        if (id) {
            await this.openOrder(id);
        }
    }

    async startOrderWithoutTable(type) {
        const id = await this.call("adk.pos.order", "terminal_create_order", [
            this.state.cfg.id,
            this.state.session.id,
            this.state.employee.id,
            false,
            type,
            1,
            true,
        ]);
        if (id) {
            await this.openOrder(id);
        }
    }

    async addProduct(product) {
        if (this.readonlyOrder) {
            return;
        }
        const order = await this.call("adk.pos.order", "terminal_add_line", [
            this.state.order.id,
            product.id,
            1,
        ]);
        if (order) {
            this.state.order = order;
        }
    }

    async changeQty(line, delta) {
        const newQty = line.qty + delta;
        await this.setLineQty(line, newQty);
    }

    async setLineQty(line, newQty, reason = "") {
        const cfg = this.state.cfg;
        let approver = {};
        if (newQty < line.qty_sent && cfg.approve_void) {
            const ap = await this.askApproval("Retirer un article déjà envoyé en préparation");
            if (!ap) {
                return;
            }
            approver = ap;
            if (!reason) {
                reason = "Retiré après envoi";
            }
        }
        const order = await this.call("adk.pos.order", "terminal_set_line_qty", [
            line.id,
            newQty,
            this.state.employee.id,
            reason,
            approver.approver_id || false,
            approver.approver_pin || false,
        ]);
        if (order) {
            this.state.order = order;
        }
    }

    // ----- Modification d'une ligne -----
    openLineEdit(line) {
        if (this.readonlyOrder) {
            return;
        }
        this.openModal("lineEdit", {
            line,
            qty: String(line.qty),
            discount: String(line.discount || ""),
            note: line.note || "",
            course: line.course,
            field: "qty",
        });
    }

    lineEditKey(key) {
        this.numpadKey("m." + this.state.m.field, key);
    }

    setLineEditField(f) {
        this.state.m.field = f;
    }

    addQuickNote(text) {
        const m = this.state.m;
        const field = m.noteTarget || "note";
        const current = m[field] || "";
        m[field] = current ? `${current}, ${text}` : text;
    }

    async saveLineEdit() {
        const m = this.state.m;
        const line = m.line;
        const cfg = this.state.cfg;
        const newQty = this.num(m.qty);
        const discount = Math.min(Math.max(this.num(m.discount), 0), 100);
        let approver = {};
        if (discount > (line.discount || 0) && discount > cfg.approve_discount_above) {
            const ap = await this.askApproval(`Remise de ${discount} %`);
            if (!ap) {
                return;
            }
            approver = ap;
        }
        let order = await this.call("adk.pos.order", "terminal_update_line", [
            line.id,
            { note: m.note, course: m.course, discount: discount },
            this.state.employee.id,
            approver.approver_id || false,
            approver.approver_pin || false,
        ]);
        if (!order) {
            return;
        }
        this.state.order = order;
        if (newQty !== line.qty) {
            const l2 = order.lines.find((l) => l.id === line.id);
            if (l2) {
                await this.setLineQty(l2, newQty);
            }
        }
        this.closeModal();
    }

    async deleteLine() {
        const line = this.state.m.line;
        this.closeModal();
        await this.setLineQty(line, 0, "Article supprimé");
    }

    // ----- Remise globale -----
    openGlobalDiscount() {
        this.openModal("globalDiscount", { percent: "" });
    }

    async applyGlobalDiscount() {
        const percent = Math.min(this.num(this.state.m.percent), 100);
        let approver = {};
        if (percent > this.state.cfg.approve_discount_above) {
            const ap = await this.askApproval(`Remise globale de ${percent} %`);
            if (!ap) {
                return;
            }
            approver = ap;
        }
        const order = await this.call("adk.pos.order", "terminal_apply_discount", [
            this.state.order.id,
            percent,
            this.state.employee.id,
            approver.approver_id || false,
            approver.approver_pin || false,
        ]);
        if (order) {
            this.state.order = order;
            this.closeModal();
        }
    }

    // ----- Infos de commande : couverts, note, type, client -----
    openGuests() {
        this.openModal("guests", { guests: String(this.state.order.guests || 1) });
    }

    async saveGuests() {
        const m = this.state.m;
        if (m.pendingTable) {
            await this.createOrderOnTable(
                m.pendingTable, m.forceNew, Math.max(parseInt(m.guests) || 1, 1));
            return;
        }
        const order = await this.call("adk.pos.order", "terminal_update", [
            this.state.order.id,
            { guests: Math.max(parseInt(this.state.m.guests) || 1, 1) },
        ]);
        if (order) {
            this.state.order = order;
            this.closeModal();
        }
    }

    openOrderNote() {
        this.openModal("orderNote", { note: this.state.order.note || "", noteTarget: "note" });
    }

    async saveOrderNote() {
        const order = await this.call("adk.pos.order", "terminal_update", [
            this.state.order.id,
            { note: this.state.m.note },
        ]);
        if (order) {
            this.state.order = order;
            this.closeModal();
        }
    }

    openOrderType() {
        const o = this.state.order;
        this.openModal("orderType", {
            type: o.order_type,
            phone: o.customer_phone,
            address: o.delivery_address,
        });
    }

    async saveOrderType() {
        const m = this.state.m;
        const order = await this.call("adk.pos.order", "terminal_update", [
            this.state.order.id,
            { order_type: m.type, customer_phone: m.phone, delivery_address: m.address },
        ]);
        if (order) {
            this.state.order = order;
            this.closeModal();
        }
    }

    get orderLocked() {
        const o = this.state.order;
        return !!o && (o.state === "paid" || o.state === "cancelled");
    }

    async openClientModal() {
        this.openModal("client", { text: "", phone: "", results: [], creating: false });
        await this.searchClients();
    }

    async searchClients() {
        const text = (this.state.m.text || "").trim();
        const domain = text
            ? [
                  "|", "|", "|",
                  ["name", "ilike", text],
                  ["phone", "ilike", text],
                  ["mobile", "ilike", text],
                  ["email", "ilike", text],
              ]
            : [];
        const results = await this.call("res.partner", "search_read", [domain, ["name", "phone", "email"]], {
            limit: 15,
            order: text ? "name" : "write_date desc",
        });
        if (this.state.modal === "client") {
            this.state.m.results = results || [];
        }
    }

    async selectClient(partner) {
        const wasPaid = this.state.order.state === "paid";
        const order = await this.call("adk.pos.order", "terminal_update", [
            this.state.order.id,
            { partner_id: partner.id, customer_phone: partner.phone || this.state.order.customer_phone },
        ]);
        if (order) {
            this.state.order = order;
            this.closeModal();
            this.notify(wasPaid ? `Client enregistré : ${partner.name} — facture mise à jour.` : `Client : ${partner.name}`);
        }
    }

    async createClient() {
        const name = (this.state.m.text || "").trim();
        if (!name) {
            this.notify("Saisissez le nom du client.", "warning");
            return;
        }
        const vals = { name };
        if ((this.state.m.phone || "").trim()) {
            vals.phone = this.state.m.phone.trim();
        }
        const ids = await this.call("res.partner", "create", [[vals]]);
        if (ids) {
            await this.selectClient({ id: ids[0], name, phone: vals.phone || "" });
        }
    }

    async clearClient() {
        const order = await this.call("adk.pos.order", "terminal_update", [
            this.state.order.id,
            { partner_id: false },
        ]);
        if (order) {
            this.state.order = order;
            this.closeModal();
        }
    }

    // ----- Envoi en préparation, addition -----
    async sendOrder() {
        const o = this.state.order;
        if (!o.has_unsent) {
            this.notify("Rien de nouveau à envoyer.", "warning");
            return;
        }
        this.state.busy = true;
        const res = await this.call("adk.pos.order", "terminal_send", [o.id, this.state.employee.id]);
        this.state.busy = false;
        if (!res) {
            return;
        }
        this.notify("Commande envoyée en préparation !");
        if (this.state.printKitchen || (this.state.cfg.has_printers && this.state.cfg.print_kitchen_on_send)) {
            this.printJob("kitchen", o.id, { batch: res.batch });
        }
        this.state.order = res.order;
        await this.closeOrder();
    }

    printKitchenAgain() {
        const o = this.state.order;
        if (o && o.send_count) {
            this.printJob("kitchen", o.id, { batch: o.send_count });
        }
    }

    async requestBill() {
        const o = this.state.order;
        const res = await this.call("adk.pos.order", "terminal_go_billing", [o.id, this.state.employee.id]);
        if (res) {
            this.state.order = res;
            if (this.state.cfg.allow_bill_print) {
                this.printJob("bill", o.id);
            }
        }
    }

    printBill() {
        this.printJob("bill", this.state.order.id);
    }

    printReceipt(orderId) {
        this.printJob("receipt", orderId || this.state.order.id);
    }

    printBasicReceipt(orderId) {
        this.printJob("basic_receipt", orderId || this.state.order.id);
    }

    downloadReceiptPdf(orderId) {
        window.open(`/report/pdf/adk_pos.report_receipt_document_pdf/${orderId || this.state.order.id}`, "_blank");
    }

    // ----- Annulation -----
    openCancelOrder() {
        this.openModal("cancelOrder", { reason: "" });
    }

    async confirmCancelOrder() {
        const o = this.state.order;
        const sent = o.lines.some((l) => l.qty_sent > 0);
        let approver = {};
        if (sent && this.state.cfg.approve_void) {
            const ap = await this.askApproval("Annuler une commande déjà envoyée");
            if (!ap) {
                return;
            }
            approver = ap;
        }
        const ok = await this.call("adk.pos.order", "terminal_cancel_order", [
            o.id,
            this.state.employee.id,
            this.state.m.reason || "",
            approver.approver_id || false,
            approver.approver_pin || false,
        ]);
        if (ok) {
            this.notify("Commande annulée.", "warning");
            this.closeModal();
            this.state.order = null;
            await this.closeOrder();
        }
    }

    // ----- Transfert / fusion de table -----
    async openTransfer() {
        await this.refreshFloor();
        this.openModal("transfer", { floorId: this.state.order.table_id ? null : this.state.floorId });
    }

    get transferTables() {
        const o = this.state.order;
        return this.state.tables.filter((t) => !o.table_id || t.id !== o.table_id[0]);
    }

    async doTransfer(table) {
        const id = await this.call("adk.pos.order", "terminal_transfer_table", [
            this.state.order.id,
            table.id,
            this.state.employee.id,
        ]);
        if (id) {
            this.notify(`Commande déplacée vers ${table.name}.`);
            this.closeModal();
            await this.openOrder(id);
            await this.refreshFloor();
        }
    }

    // ----- Séparation d'addition -----
    openSplit() {
        const picks = {};
        for (const l of this.state.order.lines) {
            picks[l.id] = 0;
        }
        this.openModal("split", { picks });
    }

    changeSplit(line, delta) {
        const picks = this.state.m.picks;
        picks[line.id] = Math.min(Math.max((picks[line.id] || 0) + delta, 0), line.qty);
    }

    get splitCount() {
        return Object.values(this.state.m.picks || {}).reduce((a, b) => a + b, 0);
    }

    async confirmSplit() {
        const splits = Object.entries(this.state.m.picks)
            .filter(([, q]) => q > 0)
            .map(([id, qty]) => ({ line_id: parseInt(id), qty }));
        const newId = await this.call("adk.pos.order", "terminal_split_order", [
            this.state.order.id,
            splits,
            this.state.employee.id,
        ]);
        if (newId) {
            this.notify("Addition séparée : la nouvelle commande est ouverte.");
            this.closeModal();
            await this.openOrder(newId);
        }
    }

    // =====================================================================
    // Validation par un manager (code PIN)
    // =====================================================================
    async askApproval(label) {
        if (this.isManager) {
            return {};
        }
        const managers = await this.call("res.users", "adk_pos_get_managers", [this.state.cfg.id]);
        if (!managers || !managers.length) {
            this.notify("Aucun manager avec un code PIN n'est configuré.", "danger");
            return null;
        }
        return new Promise((resolve) => {
            this.approvalResolve = resolve;
            this.state.approval = {
                label,
                managers,
                managerId: managers[0].id,
                pin: "",
                error: "",
            };
        });
    }

    approvalKey(key) {
        const ap = this.state.approval;
        if (key === "⌫") {
            ap.pin = ap.pin.slice(0, -1);
        } else if (key === "C") {
            ap.pin = "";
        } else if (ap.pin.length < 6) {
            ap.pin += key;
        }
        ap.error = "";
    }

    async confirmApproval() {
        const ap = this.state.approval;
        const res = await this.call("res.users", "adk_pos_approve", [ap.managerId, ap.pin]);
        if (!res || !res.ok) {
            ap.pin = "";
            ap.error =
                res && res.error === "locked"
                    ? `Code bloqué (${res.minutes} min).`
                    : res && res.error === "not_manager"
                    ? "Cet utilisateur n'est pas manager."
                    : "Code PIN incorrect.";
            return;
        }
        const out = { approver_id: ap.managerId, approver_pin: ap.pin };
        this.state.approval = null;
        this.approvalResolve(out);
    }

    cancelApproval() {
        this.state.approval = null;
        if (this.approvalResolve) {
            this.approvalResolve(null);
        }
    }

    // =====================================================================
    // Paiement
    // =====================================================================
    get payTip() {
        return this.state.cfg.tip_enabled ? this.num(this.state.pay.tip) : 0;
    }

    get payBaseDue() {
        const o = this.state.order;
        return o ? o.amount_due - (o.tip_amount || 0) : 0;
    }

    get payDue() {
        return this.payBaseDue + this.payTip;
    }

    get payTotalEntered() {
        return this.state.pay.lines.reduce((a, l) => a + (l.amount || 0), 0);
    }

    get payRemaining() {
        return Math.max(this.payDue - this.payTotalEntered, 0);
    }

    get payChange() {
        const cash = this.state.pay.lines.filter((l) => l.is_cash).reduce((a, l) => a + l.amount, 0);
        return Math.min(Math.max(this.payTotalEntered - this.payDue, 0), cash);
    }

    async openPayment() {
        if (!this.isCashier) {
            this.notify("L'encaissement est réservé aux caissiers.", "warning");
            return;
        }
        const o = this.state.order;
        if (!o.lines.length) {
            return;
        }
        if (o.state !== "billing") {
            const res = await this.call("adk.pos.order", "terminal_go_billing", [o.id, this.state.employee.id]);
            if (!res) {
                return;
            }
            this.state.order = res;
        }
        this.state.pay = {
            open: true,
            lines: [],
            selected: null,
            tip: String(this.state.order.tip_amount || ""),
            result: null,
        };
    }

    closePayment() {
        this.state.pay.open = false;
    }

    addPaymentLine(method) {
        const pay = this.state.pay;
        const remaining = this.payRemaining;
        if (remaining <= 0 && !method.is_cash) {
            return;
        }
        if (method.is_credit && !this.state.order.partner_id) {
            this.notify("Paiement à crédit : choisissez d'abord le client.", "warning");
            this.openClientModal();
            return;
        }
        pay.lines.push({
            uid: Date.now() + Math.random(),
            method_id: method.id,
            name: method.name,
            icon: method.icon,
            is_cash: method.is_cash,
            requires_reference: method.requires_reference,
            terminal: !!method.terminal,
            terminal_status: "",
            amount: remaining,
            buffer: "",
            reference: "",
        });
        pay.selected = pay.lines.length - 1;
    }

    selectPaymentLine(index) {
        this.state.pay.selected = index;
        this.state.pay.lines[index].buffer = "";
    }

    removePaymentLine(index) {
        const pay = this.state.pay;
        pay.lines.splice(index, 1);
        pay.selected = pay.lines.length ? pay.lines.length - 1 : null;
    }

    payKey(key) {
        const pay = this.state.pay;
        const line = pay.lines[pay.selected];
        if (!line) {
            return;
        }
        if (key.startsWith("+")) {
            line.amount = (line.amount || 0) + parseFloat(key.slice(1));
            line.buffer = String(line.amount);
        } else {
            const tmp = { v: line.buffer };
            this.state._tmp = tmp;
            this.numpadKey("_tmp.v", key);
            line.buffer = tmp.v;
            line.amount = this.num(line.buffer);
        }
    }

    setExactAmount() {
        const pay = this.state.pay;
        const line = pay.lines[pay.selected];
        if (line) {
            const others = pay.lines.reduce((a, l, i) => (i === pay.selected ? a : a + l.amount), 0);
            line.amount = Math.max(this.payDue - others, 0);
            line.buffer = "";
        }
    }

    setTipPercent(p) {
        this.state.pay.tip = p ? String(Math.round((this.payBaseDue * p) / 100)) : "";
    }

    async confirmPayment() {
        const pay = this.state.pay;
        const lines = pay.lines.filter((l) => l.amount > 0);
        if (!lines.length) {
            this.notify("Choisissez un mode de paiement.", "warning");
            return;
        }
        for (const l of lines) {
            if (l.terminal && l.terminal_status !== "ok") {
                this.notify(`« ${l.name} » : envoyez d'abord le montant au terminal.`, "warning");
                return;
            }
            if (l.requires_reference && !l.reference.trim()) {
                this.notify(`« ${l.name} » : la référence est obligatoire.`, "warning");
                return;
            }
        }
        this.state.busy = true;
        const res = await this.call("adk.pos.order", "terminal_pay", [
            this.state.order.id,
            this.state.employee.id,
            lines.map((l) => ({ method_id: l.method_id, amount: l.amount, reference: l.reference })),
            this.payTip,
        ]);
        this.state.busy = false;
        if (!res) {
            return;
        }
        this.state.order = res.order;
        pay.result = { paid: res.paid, change: res.change };
        pay.lines = [];
        pay.selected = null;
        if (res.paid) {
            this.notify("Paiement encaissé avec succès !");
            if (this.state.cfg.auto_print_receipt) {
                this.printReceipt(res.order.id);
            }
        } else {
            this.notify("Paiement partiel enregistré.", "info");
            pay.result = null;
        }
    }

    async finishPayment() {
        this.state.pay = { open: false, lines: [], selected: null, tip: "", result: null };
        this.state.order = null;
        await this.closeOrder();
    }

    // =====================================================================
    // Remboursement
    // =====================================================================
    openRefund() {
        const o = this.state.order;
        const picks = {};
        for (const l of o.lines) {
            picks[l.id] = 0;
        }
        const method = this.state.cfg.payment_methods[0];
        this.openModal("refund", { picks, reason: "", methodId: method ? method.id : false });
    }

    changeRefund(line, delta) {
        const picks = this.state.m.picks;
        picks[line.id] = Math.min(Math.max((picks[line.id] || 0) + delta, 0), line.refundable_qty);
    }

    refundAll() {
        for (const l of this.state.order.lines) {
            this.state.m.picks[l.id] = l.refundable_qty;
        }
    }

    get refundTotal() {
        const o = this.state.order;
        if (!o || this.state.modal !== "refund") {
            return 0;
        }
        return o.lines.reduce((a, l) => {
            const q = this.state.m.picks[l.id] || 0;
            return a + (l.qty ? (l.price_total / l.qty) * q : 0);
        }, 0);
    }

    async confirmRefund() {
        const m = this.state.m;
        const lines = Object.entries(m.picks)
            .filter(([, q]) => q > 0)
            .map(([id, qty]) => ({ line_id: parseInt(id), qty }));
        if (!lines.length) {
            this.notify("Sélectionnez au moins un article.", "warning");
            return;
        }
        if (!m.reason.trim()) {
            this.notify("Indiquez le motif du remboursement.", "warning");
            return;
        }
        let approver = {};
        if (this.state.cfg.approve_refund) {
            const ap = await this.askApproval("Remboursement client");
            if (!ap) {
                return;
            }
            approver = ap;
        }
        const refund = await this.call("adk.pos.order", "terminal_refund", [
            this.state.order.id,
            lines,
            this.state.employee.id,
            m.reason,
            m.methodId,
            this.state.session.id,
            approver.approver_id || false,
            approver.approver_pin || false,
        ]);
        if (refund) {
            this.notify("Remboursement enregistré.");
            this.closeModal();
            this.state.order = refund;
            await this.refreshHistory();
        }
    }

    // =====================================================================
    // Menu, mouvements de caisse, fermeture, QR
    // =====================================================================
    toggleMenu() {
        this.state.showMenu = !this.state.showMenu;
    }

    menuAction(name) {
        this.state.showMenu = false;
        this[name]();
    }

    openCashMove() {
        if (!this.isCashier) {
            this.notify("Réservé aux caissiers.", "warning");
            return;
        }
        this.openModal("cashMove", { type: "in", amount: "", reason: "" });
    }

    async confirmCashMove() {
        const m = this.state.m;
        if (!this.num(m.amount) || !m.reason.trim()) {
            this.notify("Merci d'indiquer un montant et un motif.", "warning");
            return;
        }
        const id = await this.call("adk.pos.session", "add_cash_move", [
            this.state.session.id,
            m.type,
            this.num(m.amount),
            m.reason,
            this.state.employee.id,
        ]);
        if (id) {
            this.notify("Mouvement de caisse enregistré.");
            this.closeModal();
        }
    }

    createProduct() {
        if (!this.isManager) {
            this.notify("Réservé aux managers.", "warning");
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "product.template",
            views: [[false, "form"]],
            target: "new",
            context: { default_sale_ok: true },
        });
    }

    goBackend() {
        window.location.href = "/odoo";
    }

    openKitchenScreen() {
        this.action.doAction("adk_pos.action_adk_pos_kitchen");
    }

    openXReport() {
        this.printJob("report", this.state.session.id);
    }

    togglePrintKitchen() {
        this.state.printKitchen = !this.state.printKitchen;
    }

    // ----- Fermeture de caisse -----
    async openCloseSession() {
        if (!this.isCashier) {
            this.notify("Réservé aux caissiers.", "warning");
            return;
        }
        const info = await this.call("adk.pos.session", "get_closing_info", [
            this.state.session.id,
            this.state.employee.id,
        ]);
        if (!info) {
            return;
        }
        this.openModal("closeSession", { info, balance: "", counts: {}, note: "" });
    }

    changeCloseCount(denom, delta) {
        const m = this.state.m;
        m.counts[denom] = Math.max((parseInt(m.counts[denom]) || 0) + delta, 0);
        m.balance = String(this.countTotal(m.counts));
    }

    get closeDifference() {
        const m = this.state.m;
        if (this.state.modal !== "closeSession" || m.info.expected === false) {
            return null;
        }
        return this.num(m.balance) - m.info.expected;
    }

    async confirmCloseSession() {
        const m = this.state.m;
        if (m.info.pending.length) {
            this.notify("Réglez ou annulez d'abord les commandes en cours.", "warning");
            return;
        }
        if (m.balance === "") {
            this.notify("Saisissez le montant compté dans le tiroir-caisse.", "warning");
            return;
        }
        const cfg = this.state.cfg;
        let approver = {};
        const diff = m.info.expected === false ? null : this.num(m.balance) - m.info.expected;
        if (cfg.max_cash_difference && (diff === null || Math.abs(diff) > cfg.max_cash_difference) && !this.isManager) {
            if (diff !== null) {
                const ap = await this.askApproval(`Écart de caisse de ${this.formatMoney(diff)}`);
                if (!ap) {
                    return;
                }
                approver = ap;
            }
        }
        const sessionId = this.state.session.id;
        let res = await this.call("adk.pos.session", "close_session_with_balance", [
            sessionId,
            this.num(m.balance),
            this.countDetails(m.counts),
            this.state.employee.id,
            approver.approver_id || false,
            approver.approver_pin || false,
            m.note,
        ]);
        if (!res && m.info.blind) {
            // En clôture à l'aveugle, l'écart n'est connu que du serveur : demander un manager et réessayer.
            const ap = await this.askApproval("Écart de caisse à valider");
            if (!ap) {
                return;
            }
            res = await this.call("adk.pos.session", "close_session_with_balance", [
                sessionId,
                this.num(m.balance),
                this.countDetails(m.counts),
                this.state.employee.id,
                ap.approver_id,
                ap.approver_pin,
                m.note,
            ]);
        }
        if (!res) {
            return;
        }
        this.closeModal();
        this.printJob("report", sessionId);
        this.notify(
            res.difference
                ? `Caisse fermée avec un écart de ${this.formatMoney(res.difference)}.`
                : "Caisse fermée. Aucun écart.",
            res.difference ? "warning" : "success"
        );
        this.backToConfigs();
    }

    // ----- QR & Kiosque -----
    openKiosk() {
        this.openModal("kiosk", {});
    }

    get kioskUrl() {
        return this.state.cfg ? `${window.location.origin}/adk_pos/kiosk/${this.state.cfg.id}` : "";
    }

    get kioskQrUrl() {
        return `/report/barcode/?barcode_type=QR&value=${encodeURIComponent(this.kioskUrl)}&width=200&height=200`;
    }

    async toggleKioskEnabled() {
        if (!this.isManager) {
            this.notify("Réservé aux managers.", "warning");
            return;
        }
        const value = !this.state.cfg.kiosk_enabled;
        await this.orm.write("adk.pos.config", [this.state.cfg.id], { kiosk_enabled: value });
        this.state.cfg.kiosk_enabled = value;
    }

    printTableQrs() {
        window.open(`/adk_pos/table_qrs/${this.state.cfg.id}`, "_blank");
    }

    async copyKioskLink() {
        try {
            await navigator.clipboard.writeText(this.kioskUrl);
            this.notify("Lien copié !");
        } catch {
            this.notify(this.kioskUrl, "info");
        }
    }
}

registry.category("actions").add("adk_pos_terminal", AdkPosTerminal);
