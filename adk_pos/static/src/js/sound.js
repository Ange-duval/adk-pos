/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Sonneries ADK POS (Web Audio) + secours HTML <audio>.
 *
 * 10 sonneries distinctes (+ un fichier importé), au choix dans Paramètres ▸ Point de vente ▸ onglet « Sonneries » :
 * une pour « nouveau ticket » (écrans de préparation) et une pour « plat prêt » (salle).
 * Les clés ci-dessous doivent rester identiques à celles du champ « ring_* » de adk.pos.config.
 *
 * Les navigateurs exigent un geste de l'utilisateur : le moteur se « déverrouille » dès le premier
 * clic / toucher / touche, ou via le bouton « Tester la sonnerie ».
 */
let ctx = null;
let unlocked = false;
const wavUrls = {};
let alarm = null;

/** Timbres : liste de partiels [multiple de fréquence, volume, forme d'onde] et type d'enveloppe. */
const STYLES = {
    bell: { partials: [[1, 0.55, "triangle"], [2, 0.18, "sine"]], env: "decay" },
    soft: { partials: [[1, 0.5, "sine"], [2, 0.12, "sine"]], env: "decay" },
    gong: { partials: [[1, 0.6, "sine"], [2.76, 0.25, "sine"], [5.4, 0.1, "sine"]], env: "decay" },
    xylo: { partials: [[1, 0.6, "sine"], [3, 0.2, "sine"], [4.2, 0.08, "sine"]], env: "decay" },
    beep: { partials: [[1, 0.3, "square"]], env: "flat" },
    brass: { partials: [[1, 0.22, "sawtooth"], [2, 0.08, "sawtooth"]], env: "flat" },
};

const n = (dt, freq, dur, style) => ({ dt, freq, dur, style });

export const RINGTONES = {
    cloche: {
        label: "Cloche classique",
        notes: [
            n(0, 880, 0.9, "bell"), n(0.3, 1175, 0.9, "bell"), n(0.6, 1568, 0.9, "bell"),
            n(1.3, 880, 0.9, "bell"), n(1.6, 1175, 0.9, "bell"), n(1.9, 1568, 0.9, "bell"),
        ],
    },
    ding: {
        label: "Ding-dong",
        notes: [n(0, 1047, 0.9, "bell"), n(0.28, 784, 0.9, "bell")],
    },
    carillon: {
        label: "Carillon",
        notes: [
            n(0, 523, 0.7, "soft"), n(0.18, 659, 0.7, "soft"), n(0.36, 784, 0.7, "soft"), n(0.54, 1047, 1.2, "soft"),
            n(1.5, 523, 0.7, "soft"), n(1.68, 659, 0.7, "soft"), n(1.86, 784, 0.7, "soft"), n(2.04, 1047, 1.2, "soft"),
        ],
    },
    gong: {
        label: "Gong",
        notes: [n(0, 262, 2.0, "gong"), n(1.1, 196, 2.2, "gong")],
    },
    bips: {
        label: "Bips rapides",
        notes: [
            n(0, 1200, 0.09, "beep"), n(0.15, 1200, 0.09, "beep"), n(0.3, 1200, 0.09, "beep"), n(0.45, 1200, 0.09, "beep"),
            n(1.1, 1200, 0.09, "beep"), n(1.25, 1200, 0.09, "beep"), n(1.4, 1200, 0.09, "beep"), n(1.55, 1200, 0.09, "beep"),
        ],
    },
    alarme: {
        label: "Alarme",
        notes: [0, 1, 2, 3, 4, 5, 6, 7].map((i) => n(i * 0.25, i % 2 ? 1000 : 780, 0.22, "beep")),
    },
    xylophone: {
        label: "Xylophone",
        notes: [
            n(0, 659, 0.4, "xylo"), n(0.12, 784, 0.4, "xylo"), n(0.24, 988, 0.4, "xylo"),
            n(0.36, 1319, 0.5, "xylo"), n(0.7, 988, 0.4, "xylo"), n(0.82, 1319, 0.7, "xylo"),
        ],
    },
    fanfare: {
        label: "Fanfare",
        notes: [
            n(0, 392, 0.17, "brass"), n(0.2, 392, 0.17, "brass"), n(0.4, 392, 0.17, "brass"), n(0.6, 523, 0.8, "brass"),
        ],
    },
    coucou: {
        label: "Coucou",
        notes: [
            n(0, 784, 0.35, "soft"), n(0.45, 659, 0.55, "soft"),
            n(1.4, 784, 0.35, "soft"), n(1.85, 659, 0.55, "soft"),
        ],
    },
    telephone: {
        label: "Téléphone",
        notes: [
            ...Array.from({ length: 12 }, (_, i) => n(i * 0.06, i % 2 ? 1100 : 1400, 0.055, "beep")),
            ...Array.from({ length: 12 }, (_, i) => n(1.1 + i * 0.06, i % 2 ? 1100 : 1400, 0.055, "beep")),
        ],
    },
};

/** Sonnerie par défaut selon l'usage. */
const DEFAULTS = { order: "cloche", ready: "ding" };

function getCtx() {
    if (!ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (AC) {
            ctx = new AC();
        }
    }
    return ctx;
}

function wave(type, phase) {
    const p = phase - Math.floor(phase);
    switch (type) {
        case "square":
            return p < 0.5 ? 1 : -1;
        case "sawtooth":
            return 2 * p - 1;
        case "triangle":
            return 4 * Math.abs(p - 0.5) - 1;
        default:
            return Math.sin(2 * Math.PI * p);
    }
}

function makeWav(key, volume) {
    // Secours : la même sonnerie rendue en WAV 16 bits, généré à la volée
    const rate = 22050;
    const notes = RINGTONES[key].notes;
    const total = Math.floor(rate * (Math.max(...notes.map((x) => x.dt + x.dur)) + 0.1));
    const data = new Int16Array(total);
    for (const { dt, freq, dur, style } of notes) {
        const st = STYLES[style];
        const s0 = Math.floor(dt * rate);
        const len = Math.floor(dur * rate);
        for (let i = 0; i < len && s0 + i < total; i++) {
            const t = i / rate;
            const env = st.env === "decay" ? Math.exp(-5 * t / dur * 1.2) : Math.min(1, t / 0.005, (dur - t) / 0.02);
            let v = 0;
            for (const [mult, vol, type] of st.partials) {
                v += vol * wave(type, freq * mult * t);
            }
            data[s0 + i] += Math.floor(v * env * 9000 * volume);
        }
    }
    const buf = new ArrayBuffer(44 + data.length * 2);
    const view = new DataView(buf);
    const w = (o, str) => [...str].forEach((c, i) => view.setUint8(o + i, c.charCodeAt(0)));
    w(0, "RIFF");
    view.setUint32(4, 36 + data.length * 2, true);
    w(8, "WAVEfmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    w(36, "data");
    view.setUint32(40, data.length * 2, true);
    new Int16Array(buf, 44).set(data);
    return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

export const AdkSound = {
    get unlocked() {
        return unlocked;
    },

    /** Vrai si le navigateur autorise réellement le son (après un premier toucher de la page). */
    running() {
        const c = getCtx();
        if (!c) {
            return true;
        }
        if (c.state === "suspended") {
            try {
                c.resume();
            } catch (e) {
                // ignoré
            }
        }
        return c.state === "running";
    },

    /**
     * Alarme longue : la sonnerie choisie est programmée en boucle sur `seconds` secondes (120 par défaut),
     * sans aucune interruption, jusqu'à stopAlarm() (clic sur « J'ai vu »).
     */
    startAlarm(ringKey, volume = 100, url = "", seconds = 120) {
        this.stopAlarm();
        const key = RINGTONES[ringKey] ? ringKey : DEFAULTS.order;
        const vol = Math.min(Math.max(Number(volume) || 100, 10), 100) / 100;
        const total = Math.min(Math.max(Number(seconds) || 120, 10), 600);
        alarm = { endsAt: Date.now() + total * 1000, oscillators: [], master: null, audio: null };
        if (ringKey === "custom" && url) {
            try {
                const a = new Audio(url);
                a.loop = true;
                a.volume = vol;
                a.play().catch(() => {});
                alarm.audio = a;
                return true;
            } catch (e) {
                // on retombe sur une sonnerie intégrée
            }
        }
        const c = getCtx();
        if (!c) {
            return false;
        }
        try {
            if (c.state === "suspended") {
                c.resume();
            }
            const master = c.createGain();
            master.gain.value = 1;
            master.connect(c.destination);
            alarm.master = master;
            const notes = RINGTONES[key].notes;
            const cycle = Math.max(...notes.map((x) => x.dt + x.dur)) + 1.2;
            const t0 = c.currentTime + 0.05;
            for (let rep = 0; rep * cycle < total; rep++) {
                for (const { dt, freq, dur, style } of notes) {
                    const st = STYLES[style];
                    const at = t0 + rep * cycle + dt;
                    for (const [mult, pv, type] of st.partials) {
                        const o = c.createOscillator();
                        const g = c.createGain();
                        const peak = pv * vol;
                        o.type = type;
                        o.frequency.value = freq * mult;
                        g.gain.setValueAtTime(0.0001, at);
                        g.gain.exponentialRampToValueAtTime(peak, at + 0.012);
                        if (st.env === "flat") {
                            g.gain.setValueAtTime(peak, at + Math.max(dur - 0.03, 0.02));
                        }
                        g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
                        o.connect(g);
                        g.connect(master);
                        o.start(at);
                        o.stop(at + dur + 0.05);
                        alarm.oscillators.push(o);
                    }
                }
            }
            return true;
        } catch (e) {
            return false;
        }
    },

    /** Arrête immédiatement l'alarme longue. */
    stopAlarm() {
        if (!alarm) {
            return;
        }
        try {
            if (alarm.master) {
                alarm.master.gain.value = 0;
                alarm.master.disconnect();
            }
            for (const o of alarm.oscillators) {
                try {
                    o.stop();
                } catch (e) {
                    // déjà arrêté
                }
            }
            if (alarm.audio) {
                alarm.audio.pause();
            }
        } catch (e) {
            // ignoré
        }
        alarm = null;
    },

    alarmActive() {
        return !!alarm && Date.now() < alarm.endsAt;
    },

    /** Durée (ms) d'une sonnerie ; 5 s pour un fichier importé. */
    duration(ringKey) {
        const r = RINGTONES[ringKey];
        return r ? Math.max(...r.notes.map((x) => x.dt + x.dur)) * 1000 : 5000;
    },

    /** Liste des sonneries : [{ key, label }]. */
    list() {
        return Object.entries(RINGTONES).map(([key, r]) => ({ key, label: r.label }));
    },

    /** À appeler depuis un geste utilisateur (clic, touche). */
    unlock() {
        const c = getCtx();
        if (c) {
            try {
                if (c.state === "suspended") {
                    c.resume();
                }
                const o = c.createOscillator();
                const g = c.createGain();
                g.gain.value = 0.0001;
                o.connect(g);
                g.connect(c.destination);
                o.start();
                o.stop(c.currentTime + 0.02);
                unlocked = true;
            } catch (e) {
                // ignoré
            }
        }
        return unlocked;
    },

    /**
     * kind   : "order" (nouveau ticket) | "ready" (plat prêt) : sert de sonnerie par défaut.
     * ringKey: clé d'une des 10 sonneries (réglée dans le point de vente).
     * volume : 10 à 100 (%).
     */
    ring(kind = "order", ringKey = null, volume = 100, url = "") {
        if (ringKey === "custom" && url) {
            // Sonnerie importée depuis l'ordinateur : lecture du fichier ; en cas d'échec, sonnerie par défaut
            try {
                const a = new Audio(url);
                a.volume = Math.min(Math.max(Number(volume) || 100, 10), 100) / 100;
                a.play().catch(() => this.ring(kind, null, volume));
                return;
            } catch (e) {
                // on retombe sur la sonnerie par défaut
            }
        }
        const key = RINGTONES[ringKey] ? ringKey : DEFAULTS[kind] || DEFAULTS.order;
        const vol = Math.min(Math.max(Number(volume) || 100, 10), 100) / 100;
        const c = getCtx();
        let played = false;
        if (c) {
            try {
                if (c.state === "suspended") {
                    c.resume();
                }
                const t0 = c.currentTime + 0.02;
                for (const { dt, freq, dur, style } of RINGTONES[key].notes) {
                    const st = STYLES[style];
                    for (const [mult, pv, type] of st.partials) {
                        const o = c.createOscillator();
                        const g = c.createGain();
                        const peak = pv * vol;
                        o.type = type;
                        o.frequency.value = freq * mult;
                        g.gain.setValueAtTime(0.0001, t0 + dt);
                        g.gain.exponentialRampToValueAtTime(peak, t0 + dt + 0.012);
                        if (st.env === "flat") {
                            g.gain.setValueAtTime(peak, t0 + dt + Math.max(dur - 0.03, 0.02));
                        }
                        g.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + dur);
                        o.connect(g);
                        g.connect(c.destination);
                        o.start(t0 + dt);
                        o.stop(t0 + dt + dur + 0.05);
                    }
                }
                played = c.state === "running";
            } catch (e) {
                played = false;
            }
        }
        if (!played) {
            try {
                const cacheKey = `${key}-${vol}`;
                wavUrls[cacheKey] = wavUrls[cacheKey] || makeWav(key, vol);
                const a = new Audio(wavUrls[cacheKey]);
                a.volume = 1;
                a.play().catch(() => {});
            } catch (e) {
                // ignoré
            }
        }
        if (navigator.vibrate) {
            try {
                navigator.vibrate([200, 100, 200]);
            } catch (e) {
                // ignoré
            }
        }
    },
};

/** Bouton « Écouter » des Paramètres ▸ onglet Sonneries : joue la sonnerie sans quitter le formulaire. */
registry.category("actions").add("adk_pos_play_ring", async function (env, action) {
    const params = action.params || {};
    AdkSound.unlock();
    AdkSound.ring("order", params.ring, params.volume, params.url);
});

// Déverrouillage automatique au premier geste, où que l'on soit dans Odoo
for (const evt of ["pointerdown", "keydown", "touchstart"]) {
    window.addEventListener(evt, () => AdkSound.unlock(), { once: false, passive: true, capture: true });
}
