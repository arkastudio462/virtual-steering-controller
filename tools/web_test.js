#!/usr/bin/env node
/**
 * Uji logika halaman ponsel (web/app.js) tanpa browser.
 *
 * Menjalankan app.js dengan DOM tiruan lalu mensimulasikan:
 * layar mulai, sambungan WebSocket, sensor giroskop, tombol tekan-tahan,
 * keyboard, kalibrasi, ganti mode, dan invert arah.
 *
 * Jalankan:  node tools/web_test.js
 */

"use strict";

const fs = require("fs");
const path = require("path");

const BASE = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(BASE, "web", "index.html"), "utf8");
const js = fs.readFileSync(path.join(BASE, "web", "app.js"), "utf8");

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures++;
  console.log(`  [${ok ? "OK  " : "GAGAL"}] ${name}${detail !== undefined ? " — " + detail : ""}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------- DOM tiruan ----------------

const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
const btnBits = [...html.matchAll(/data-btn="(\d+)"/g)].map((m) => m[1]);
const axisAttrs = [...html.matchAll(/data-axis="(\w+)"/g)].map((m) => m[1]);
const modeAttrs = [...html.matchAll(/data-mode="(\w+)"/g)].map((m) => m[1]);

class ClassList {
  constructor() { this.set = new Set(); }
  add(...c) { c.forEach((x) => this.set.add(x)); }
  remove(...c) { c.forEach((x) => this.set.delete(x)); }
  contains(c) { return this.set.has(c); }
  toggle(c, force) {
    const on = force === undefined ? !this.set.has(c) : !!force;
    if (on) this.set.add(c); else this.set.delete(c);
    return on;
  }
}

class El {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.id = attrs.id || "";
    this.dataset = attrs.dataset || {};
    this.classList = new ClassList();
    this.style = {};
    this.textContent = "";
    this.value = "0";
    this.checked = false;
    this.disabled = false;
    this._l = {};
  }
  addEventListener(type, fn) { (this._l[type] = this._l[type] || []).push(fn); }
  removeEventListener(type, fn) {
    this._l[type] = (this._l[type] || []).filter((f) => f !== fn);
  }
  dispatch(type, ev = {}) {
    ev.type = type;
    ev.target = ev.target || this;
    ev.currentTarget = this;
    if (!ev.preventDefault) ev.preventDefault = () => {};
    for (const fn of (this._l[type] || []).slice()) fn(ev);
    return ev;
  }
  setPointerCapture() {}
  releasePointerCapture() {}
  closest() { return null; }
  blur() {}
  focus() {}
}

const registry = new Map();
function elById(id) {
  if (!htmlIds.has(id)) throw new Error(`id "${id}" tidak ada di index.html`);
  if (!registry.has(id)) registry.set(id, new El("div", { id }));
  return registry.get(id);
}

const dataBtnEls = btnBits.map((b) => new El("button", { dataset: { btn: b } }));
const axisEls = {};
axisAttrs.forEach((a) => { axisEls[a] = new El("button", { dataset: { axis: a } }); });
const modeBtns = modeAttrs.map((m) => new El("button", { dataset: { mode: m } }));

const winListeners = {};
const docListeners = {};

const documentStub = {
  hidden: false,
  visibilityState: "visible",
  documentElement: new El("html"),
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
  removeEventListener() {},
  querySelector(sel) {
    if (sel.startsWith("#")) return elById(sel.slice(1));
    if (axisEls[sel]) return axisEls[sel];
    const m = sel.match(/^\[data-axis="(\w+)"\]$/);
    if (m && axisEls[m[1]]) return axisEls[m[1]];
    throw new Error("querySelector tidak dikenal: " + sel);
  },
  querySelectorAll(sel) {
    if (sel === "[data-btn]") return dataBtnEls;
    if (sel === "#modeSeg button") return modeBtns;
    throw new Error("querySelectorAll tidak dikenal: " + sel);
  },
};

const windowStub = {
  addEventListener(type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); },
  removeEventListener() {},
};

function fireWin(type, ev = {}) {
  ev.preventDefault = ev.preventDefault || (() => {});
  for (const fn of (winListeners[type] || []).slice()) fn(ev);
}

const store = new Map();
const localStorageStub = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

class FakeWS {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    FakeWS.instances.push(this);
    setTimeout(() => {
      if (this.readyState !== 0) return;
      this.readyState = 1;
      if (this.onopen) this.onopen();
    }, 5);
  }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    if (this.onclose) this.onclose();
  }
  serverSend(obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); }
}
FakeWS.instances = [];

let rafCb = null;
const timers = { intervals: 0 };

// ---------------- pasang global ----------------

function defineGlobal(name, value) {
  try {
    Object.defineProperty(globalThis, name, { value, writable: true, configurable: true });
  } catch (e) {
    globalThis[name] = value;
  }
}

defineGlobal("window", windowStub);
defineGlobal("document", documentStub);
defineGlobal("location", { protocol: "https:", host: "127.0.0.1:8765" });
defineGlobal("screen", { orientation: { angle: 0, lock: async () => {} } });
defineGlobal("navigator", {});
defineGlobal("localStorage", localStorageStub);
defineGlobal("WebSocket", FakeWS);
defineGlobal("fetch", async () => ({ json: async () => ({ version: "1.0.0", ssl: true, pad_ok: true }) }));
defineGlobal("requestAnimationFrame", (cb) => { rafCb = cb; return 1; });
const realSetInterval = globalThis.setInterval;
globalThis.setInterval = (fn, ms) => { timers.intervals++; return realSetInterval(fn, ms); };

// ---------------- jalankan app.js ----------------

require(path.join(BASE, "web", "app.js"));

function stepFrames(n) {
  for (let i = 0; i < n; i++) {
    const cb = rafCb;
    rafCb = null;
    if (!cb) throw new Error("rAF tidak menunggu (loop frame berhenti?)");
    cb(performance.now());
  }
}

function lastSent() {
  const ws = FakeWS.instances[FakeWS.instances.length - 1];
  if (!ws || !ws.sent.length) return null;
  return ws.sent[ws.sent.length - 1];
}

function orient(alpha, beta, gamma) {
  fireWin("deviceorientation", { alpha, beta, gamma });
}

(async () => {
  console.log("1. Inisialisasi & layar mulai");
  check("mode chip awal", elById("modeChip").textContent === "Mode: Putar",
        elById("modeChip").textContent);
  stepFrames(3);
  check("setir awal 0°", elById("steerVal").textContent === "0°", elById("steerVal").textContent);

  elById("startBtn").dispatch("click");
  await sleep(40);
  const ws = FakeWS.instances[0];
  check("WebSocket dibuat (wss)", !!ws && ws.url === "wss://127.0.0.1:8765/ws", ws && ws.url);
  check("overlay disembunyikan", elById("overlay").classList.contains("gone"));
  ws.serverSend({ t: "hello", pad: "mock", label: "demo", version: "1.0.0" });
  check("status koneksi", elById("connText").textContent.includes("tersambung"),
        elById("connText").textContent);

  console.log("2. Sensor & perhitungan setir (mode Putar)");
  orient(100, 90, 0);
  stepFrames(5);
  check("pusat kalibrasi otomatis = 0°", elById("steerVal").textContent === "0°",
        elById("steerVal").textContent);
  orient(155, 90, 0); // +55° = rentang penuh
  stepFrames(80);
  check("setir penuh kanan = 180°", elById("steerVal").textContent === "180°",
        elById("steerVal").textContent);
  orient(45, 90, 0); // -55° dari pusat
  stepFrames(80);
  check("setir penuh kiri = -180°", elById("steerVal").textContent === "-180°",
        elById("steerVal").textContent);
  orient(100, 90, 0);
  stepFrames(80);
  check("kembali ke tengah = 0°", elById("steerVal").textContent === "0°",
        elById("steerVal").textContent);

  console.log("3. Tombol tekan-tahan & pedal");
  const horn = dataBtnEls.find((e) => e.dataset.btn === "4096");
  horn.dispatch("pointerdown", { pointerId: 1 });
  stepFrames(2);
  await sleep(40);
  check("klakson terkirim (bit 0x1000)", (lastSent().p & 4096) !== 0, JSON.stringify(lastSent()));
  check("kelas active ikut", horn.classList.contains("active"));
  horn.dispatch("pointerup", { pointerId: 1 });
  await sleep(40);
  check("klakson dilepas", (lastSent().p & 4096) === 0, JSON.stringify(lastSent()));

  axisEls.gas.dispatch("pointerdown", { pointerId: 2 });
  stepFrames(2);
  await sleep(40);
  check("gas = 1 terkirim", lastSent().g === 1, JSON.stringify(lastSent()));
  check("bar gas penuh", elById("gasBar").style.width === "100%", elById("gasBar").style.width);
  axisEls.gas.dispatch("pointerup", { pointerId: 2 });
  await sleep(40);
  check("gas = 0 setelah dilepas", lastSent().g === 0);

  axisEls.brake.dispatch("pointerdown", { pointerId: 3 });
  await sleep(40);
  check("rem = 1 terkirim", lastSent().b === 1);
  axisEls.brake.dispatch("pointercancel", { pointerId: 3 });
  await sleep(40);
  check("rem = 0 setelah batal", lastSent().b === 0);

  console.log("4. Keyboard (uji dari PC)");
  fireWin("keydown", { code: "ArrowRight" });
  stepFrames(80);
  check("ArrowRight -> setir penuh kanan", elById("steerVal").textContent === "180°",
        elById("steerVal").textContent);
  fireWin("keyup", { code: "ArrowRight" });
  stepFrames(80);
  check("lepas ArrowRight -> tengah", elById("steerVal").textContent === "0°",
        elById("steerVal").textContent);
  fireWin("keydown", { code: "Space" });
  await sleep(40);
  check("Space = klakson", (lastSent().p & 4096) !== 0);
  fireWin("keyup", { code: "Space" });
  fireWin("keydown", { code: "ArrowUp" });
  await sleep(40);
  check("ArrowUp = gas", lastSent().g === 1);
  fireWin("keyup", { code: "ArrowUp" });

  console.log("5. Kalibrasi manual");
  orient(200, 90, 0); // 100° dari pusat lama -> penuh
  stepFrames(80);
  check("sebelum kalibrasi = 180°", elById("steerVal").textContent === "180°",
        elById("steerVal").textContent);
  elById("btnCalib").dispatch("click");
  stepFrames(80);
  check("setelah kalibrasi = 0°", elById("steerVal").textContent === "0°",
        elById("steerVal").textContent);

  console.log("6. Ganti mode lewat chip & panel");
  elById("modeChip").dispatch("click");
  check("chip -> mode Miring", elById("modeChip").textContent === "Mode: Miring",
        elById("modeChip").textContent);
  stepFrames(5); // pusat mode tilt diatur dari data saat ini
  orient(200, 90, 30); // gamma 30° = rentang tilt penuh
  stepFrames(80);
  check("mode Miring: gamma 30° = 180°", elById("steerVal").textContent === "180°",
        elById("steerVal").textContent);

  console.log("7. Invert arah");
  elById("invertCtl").checked = true;
  elById("invertCtl").dispatch("change");
  stepFrames(80);
  check("invert membuat arah berlawanan", elById("steerVal").textContent === "-180°",
        elById("steerVal").textContent);
  elById("invertCtl").checked = false;
  elById("invertCtl").dispatch("change");
  stepFrames(80);
  check("invert dimatikan -> 180°", elById("steerVal").textContent === "180°",
        elById("steerVal").textContent);

  console.log("8. Mode Sentuh (seret jari)");
  modeBtns.find((b) => b.dataset.mode === "touch").dispatch("click");
  check("mode chip -> Sentuh", elById("modeChip").textContent === "Mode: Sentuh",
        elById("modeChip").textContent);
  stepFrames(30);
  check("tanpa seret = 0°", elById("steerVal").textContent === "0°",
        elById("steerVal").textContent);
  const stage = elById("stage");
  stage.dispatch("pointerdown", { pointerId: 9, clientX: 300 });
  stage.dispatch("pointermove", { pointerId: 9, clientX: 440 }); // +140px = penuh
  stepFrames(80);
  check("seret kanan = 180°", elById("steerVal").textContent === "180°",
        elById("steerVal").textContent);
  stage.dispatch("pointerup", { pointerId: 9 });
  stepFrames(80);
  check("lepas seret = 0°", elById("steerVal").textContent === "0°",
        elById("steerVal").textContent);

  console.log("9. Keamanan & penyimpanan");
  fireWin("blur");
  await sleep(40);
  const afterBlur = lastSent();
  check("kehilangan fokus -> netralkan semua",
        afterBlur.g === 0 && afterBlur.b === 0 && afterBlur.p === 0, JSON.stringify(afterBlur));
  const saved = JSON.parse(localStorageStub.getItem("vsc.settings.v1") || "{}");
  check("setelan tersimpan di localStorage", saved.mode === "touch" && saved.invert === false,
        JSON.stringify(saved));

  console.log("10. Reset setelan");
  elById("btnResetCfg").dispatch("click");
  const saved2 = JSON.parse(localStorageStub.getItem("vsc.settings.v1") || "{}");
  check("reset mengembalikan default", saved2.mode === "yaw" && saved2.rangeYaw === 55,
        JSON.stringify(saved2));
  check("chip kembali ke Putar", elById("modeChip").textContent === "Mode: Putar",
        elById("modeChip").textContent);

  console.log();
  if (failures) {
    console.log(`HASIL: ${failures} pemeriksaan GAGAL`);
    process.exit(1);
  }
  console.log("HASIL: semua pemeriksaan LULUS ✔");
  process.exit(0);
})().catch((err) => {
  console.error("ERROR:", err);
  process.exit(1);
});
