/* Virtual Steering Controller — logika halaman ponsel.
 *
 * Alur: sensor ponsel (giroskop/gravitasi) atau sentuhan/keyboard
 *  -> dihaluskan  -> dikirim ke server (WebSocket, 50 Hz)
 *  -> server menerjemahkannya ke gamepad Xbox virtual.
 */
"use strict";

(function () {
  // ================= util =================
  const $ = (s) => document.querySelector(s);
  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

  const toastEl = $("#toast");
  let toastTimer = 0;
  function toast(msg, ms) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), ms || 1900);
  }

  function vibrate(ms) {
    if (navigator.vibrate) {
      try { navigator.vibrate(ms); } catch (e) { /* abaikan */ }
    }
  }

  // ================= pengaturan (tersimpan di ponsel) =================
  const LS_KEY = "vsc.settings.v1";
  const DEFAULTS = {
    mode: "yaw",       // yaw = putar seperti setir, tilt = miring, touch = seret jari
    rangeYaw: 55,      // derajat putaran untuk setir penuh
    rangeTilt: 30,     // derajat kemiringan untuk setir penuh
    dead: 5,           // zona mati (%)
    sens: 100,         // sensitivitas (%)
    smooth: 35,        // penghalusan (%)
    invert: false,     // balik arah
    fs: true,          // layar penuh otomatis
  };
  let cfg = Object.assign({}, DEFAULTS);
  try {
    Object.assign(cfg, JSON.parse(localStorage.getItem(LS_KEY) || "{}"));
  } catch (e) { /* pakai default */ }

  function saveCfg() {
    try { localStorage.setItem(LS_KEY, JSON.stringify(cfg)); } catch (e) { /* penuh/di-private */ }
  }

  const MODE_NAMES = { yaw: "Putar", tilt: "Miring", touch: "Sentuh" };

  // ================= status =================
  const S = {
    alpha: null, beta: null, gamma: null,
    alphaSeen: 0, tiltSeen: 0, sensorTs: 0,
    center: null,
    steer: 0,
    gas: 0, brake: 0, mask: 0,
    kbSteer: 0, touchSteer: 0,
    started: false,
  };

  const conn = { ws: null, ok: false, retry: 0, pad: "", rtt: 0 };

  // ================= elemen =================
  const wheelEl = $("#wheel");
  const steerVal = $("#steerVal");
  const gasBar = $("#gasBar");
  const brakeBar = $("#brakeBar");
  const modeChip = $("#modeChip");
  const btnEls = Array.from(document.querySelectorAll("[data-btn]"));
  const btnBits = btnEls.map((el) => parseInt(el.dataset.btn, 10));
  const gasBtn = document.querySelector('[data-axis="gas"]');
  const brakeBtn = document.querySelector('[data-axis="brake"]');

  // ================= sensor =================
  function onOrientation(e) {
    if (e.alpha != null) { S.alpha = e.alpha; S.alphaSeen = performance.now(); }
    if (e.beta != null || e.gamma != null) S.tiltSeen = performance.now();
    if (e.beta != null) S.beta = e.beta;
    if (e.gamma != null) S.gamma = e.gamma;
    S.sensorTs = performance.now();
  }
  window.addEventListener("deviceorientation", onOrientation, false);

  function screenAngle() {
    let a = 0;
    if (screen.orientation && typeof screen.orientation.angle === "number") {
      a = screen.orientation.angle;
    } else if (typeof window.orientation === "number") {
      a = window.orientation;
    }
    return ((a % 360) + 360) % 360;
  }

  /* Sudut kemiringan (roll) relatif terhadap garis horizontal,
     dikoreksi sesuai orientasi layar. */
  function tiltRoll() {
    const b = S.beta || 0;
    const g = S.gamma || 0;
    const a = screenAngle();
    if (a === 90) return b;
    if (a === 180) return -g;
    if (a === 270) return -b;
    return g;
  }

  function rawNow() {
    if (cfg.mode === "yaw") return S.alpha;
    if (cfg.mode === "tilt") return tiltRoll();
    return null;
  }

  // ================= perhitungan setir =================
  function computeTarget() {
    if (S.kbSteer !== 0) return clamp(S.kbSteer, -1, 1);      // override keyboard (uji di PC)
    if (cfg.mode === "touch") return clamp(S.touchSteer, -1, 1);

    const raw = rawNow();
    if (raw == null) return 0;
    if (S.center == null) S.center = raw;

    // selisih sudut terpendek (yaw dibungkus ±180°)
    const d = cfg.mode === "yaw"
      ? (((raw - S.center) % 360) + 540) % 360 - 180
      : raw - S.center;

    const range = cfg.mode === "yaw" ? cfg.rangeYaw : cfg.rangeTilt;
    let v = clamp((d / range) * (cfg.sens / 100), -1, 1);

    const dz = cfg.dead / 100;
    if (Math.abs(v) < dz) {
      v = 0;
    } else {
      v = Math.sign(v) * ((Math.abs(v) - dz) / (1 - dz));
    }
    if (cfg.invert) v = -v;
    return clamp(v, -1, 1);
  }

  function frame() {
    const target = computeTarget();
    const k = 1 - cfg.smooth / 100;           // faktor per frame (0 = instan)
    S.steer += (target - S.steer) * k;
    if (Math.abs(S.steer - target) < 0.0015) S.steer = target;
    render();
    requestAnimationFrame(frame);
  }

  function render() {
    wheelEl.style.transform = "rotate(" + (-S.steer * 180).toFixed(1) + "deg)";
    steerVal.textContent = Math.round(S.steer * 180) + "°";
    gasBar.style.width = (S.gas * 100).toFixed(0) + "%";
    brakeBar.style.width = (S.brake * 100).toFixed(0) + "%";
    for (let i = 0; i < btnEls.length; i++) {
      btnEls[i].classList.toggle("active", (S.mask & btnBits[i]) !== 0);
    }
    if (gasBtn) gasBtn.classList.toggle("active", S.gas > 0);
    if (brakeBtn) brakeBtn.classList.toggle("active", S.brake > 0);
  }

  // ================= koneksi WebSocket =================
  function sendState() {
    if (!conn.ok || !conn.ws || conn.ws.readyState !== 1) return;
    const msg = {
      t: "i",
      s: Math.round(S.steer * 1000) / 1000,
      g: Math.round(S.gas * 100) / 100,
      b: Math.round(S.brake * 100) / 100,
      p: S.mask,
    };
    try { conn.ws.send(JSON.stringify(msg)); } catch (e) { /* ditutup */ }
  }

  function connect() {
    if (conn.ws && (conn.ws.readyState === 0 || conn.ws.readyState === 1)) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    let ws;
    try {
      ws = new WebSocket(proto + "//" + location.host + "/ws");
    } catch (e) {
      scheduleReconnect();
      return;
    }
    conn.ws = ws;
    ws.onopen = () => {
      conn.ok = true;
      conn.retry = 0;
      updateConn();
    };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.t === "hello") {
        conn.pad = m.pad || "";
        updateConn();
      } else if (m.t === "pong" && typeof m.ts === "number") {
        conn.rtt = Math.max(0, Date.now() - m.ts);
        updateConn();
      }
    };
    ws.onclose = () => {
      conn.ok = false;
      conn.ws = null;
      updateConn();
      scheduleReconnect();
    };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* abaikan */ } };
  }

  function scheduleReconnect() {
    conn.retry = Math.min(conn.retry + 1, 6);
    setTimeout(connect, 600 * conn.retry);
  }

  function updateConn() {
    const dot = $("#dot");
    const txt = $("#connText");
    const rtt = $("#rtt");
    if (conn.ok) {
      dot.className = "dot on";
      let suffix = "";
      if (conn.pad === "mock") suffix = " · mode demo";
      else if (conn.pad) suffix = " · Xbox";
      txt.textContent = "tersambung" + suffix;
      rtt.textContent = conn.rtt ? conn.rtt + " ms" : "";
    } else if (conn.ws && conn.ws.readyState === 0) {
      dot.className = "dot wait";
      txt.textContent = "menyambung…";
      rtt.textContent = "";
    } else {
      dot.className = "dot off";
      txt.textContent = "terputus";
      rtt.textContent = "";
    }
  }

  setInterval(sendState, 20); // 50 Hz — juga berfungsi sebagai penjaga sinyal
  setInterval(() => {
    if (conn.ok && conn.ws) {
      try { conn.ws.send(JSON.stringify({ t: "ping", ts: Date.now() })); } catch (e) { /* abaikan */ }
    }
  }, 2000);

  // ================= mode & kalibrasi =================
  function updateModeChip() {
    modeChip.textContent = "Mode: " + MODE_NAMES[cfg.mode];
  }

  function setMode(m, silent) {
    if (!MODE_NAMES[m]) return;
    cfg.mode = m;
    S.center = null;
    saveCfg();
    syncSettingsUI();
    updateModeChip();
    if (!silent) toast("Mode: " + MODE_NAMES[m]);
  }

  modeChip.addEventListener("click", () => {
    const order = ["yaw", "tilt", "touch"];
    setMode(order[(order.indexOf(cfg.mode) + 1) % order.length]);
  });

  function calibrate() {
    if (cfg.mode === "touch") {
      S.touchSteer = 0;
      toast("Setir di tengah");
      return;
    }
    const seen = cfg.mode === "yaw" ? S.alphaSeen : S.tiltSeen;
    if (seen === 0) {
      toast("Belum ada data sensor — tekan Mulai & izinkan");
      return;
    }
    S.center = rawNow();
    toast("Setir dikalibrasi ke tengah");
    wheelEl.classList.add("calibrated");
    setTimeout(() => wheelEl.classList.remove("calibrated"), 450);
  }

  $("#btnCalib").addEventListener("click", calibrate);

  // ================= pemeriksaan kesehatan sensor =================
  function checkSensors() {
    if (!S.started) return;
    if (cfg.mode === "yaw" && S.alphaSeen === 0) {
      if (S.tiltSeen > 0) {
        setMode("tilt", true);
        toast("Mode Putar tidak tersedia — pindah ke mode Miring", 2600);
      } else {
        setMode("touch", true);
        toast("Sensor tidak tersedia — gunakan mode Sentuh", 2600);
      }
    } else if (cfg.mode === "tilt" && S.tiltSeen === 0) {
      setMode("touch", true);
      toast("Sensor tidak tersedia — gunakan mode Sentuh", 2600);
    }
  }

  // Bila data sensor berhenti total (mis. browser berhenti melaporkan),
  // jangan biarkan setir mati — pindah mode secara otomatis.
  function checkSensorLoss() {
    if (!S.started || document.visibilityState !== "visible") return;
    const now = performance.now();
    const STALE = 10000;
    if (cfg.mode === "yaw" && S.alphaSeen > 0 && now - S.alphaSeen > STALE) {
      if (S.tiltSeen > 0 && now - S.tiltSeen < STALE) {
        setMode("tilt", true);
        toast("Sensor putar hilang — pindah ke mode Miring", 2600);
      } else {
        setMode("touch", true);
        toast("Sensor hilang — gunakan mode Sentuh", 2600);
      }
    } else if (cfg.mode === "tilt" && S.tiltSeen > 0 && now - S.tiltSeen > STALE) {
      setMode("touch", true);
      toast("Sensor hilang — gunakan mode Sentuh", 2600);
    }
  }
  setInterval(checkSensorLoss, 2000);

  // ================= tombol tekan-tahan =================
  btnEls.forEach((el) => {
    const bit = parseInt(el.dataset.btn, 10);
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      try { el.setPointerCapture(e.pointerId); } catch (err) { /* lama */ }
      S.mask |= bit;
      vibrate(8);
    });
    const release = () => { S.mask &= ~bit; };
    el.addEventListener("pointerup", release);
    el.addEventListener("pointercancel", release);
    el.addEventListener("lostpointercapture", release);
  });

  [gasBtn, brakeBtn].forEach((el) => {
    if (!el) return;
    const axis = el.dataset.axis;
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      try { el.setPointerCapture(e.pointerId); } catch (err) { /* lama */ }
      if (axis === "gas") S.gas = 1; else S.brake = 1;
      vibrate(5);
    });
    const release = () => { if (axis === "gas") S.gas = 0; else S.brake = 0; };
    el.addEventListener("pointerup", release);
    el.addEventListener("pointercancel", release);
    el.addEventListener("lostpointercapture", release);
  });

  // ================= seret jari (mode sentuh) =================
  const stage = $("#stage");
  let dragId = null;
  let dragX = 0;

  stage.addEventListener("pointerdown", (e) => {
    if (cfg.mode !== "touch") return;
    if (e.target.closest && e.target.closest("[data-btn],[data-axis]")) return;
    dragId = e.pointerId;
    dragX = e.clientX;
    try { stage.setPointerCapture(e.pointerId); } catch (err) { /* lama */ }
  });
  stage.addEventListener("pointermove", (e) => {
    if (dragId !== e.pointerId) return;
    S.touchSteer = clamp((e.clientX - dragX) / 140, -1, 1);
  });
  function endDrag(e) {
    if (dragId === e.pointerId) {
      dragId = null;
      S.touchSteer = 0;
    }
  }
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);

  // ================= keyboard (uji dari PC) =================
  const KEY_STEER = { ArrowLeft: -1, ArrowRight: 1, KeyA: -1, KeyD: 1 };
  const KEY_MASK = {
    Space: 4096,   // klakson
    KeyB: 8192,    // rem tangan
    KeyE: 16384,   // gigi naik
    KeyQ: 32768,   // gigi turun
    KeyR: 256,     // mundur
    KeyF: 512,     // lampu jauh
    KeyL: 1,       // lampu
    KeyG: 2,       // mode lampu
    KeyC: 32,      // kamera
    KeyP: 16,      // jeda
  };

  window.addEventListener("keydown", (e) => {
    if (e.target && e.target.tagName === "INPUT") return; // slider di panel setelan
    if (e.code in KEY_STEER) {
      S.kbSteer = KEY_STEER[e.code];
      e.preventDefault();
      return;
    }
    switch (e.code) {
      case "ArrowUp": case "KeyW": S.gas = 1; e.preventDefault(); break;
      case "ArrowDown": case "KeyS": S.brake = 1; e.preventDefault(); break;
      default:
        if (e.code in KEY_MASK) { S.mask |= KEY_MASK[e.code]; e.preventDefault(); }
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.code in KEY_STEER) { S.kbSteer = 0; return; }
    switch (e.code) {
      case "ArrowUp": case "KeyW": S.gas = 0; break;
      case "ArrowDown": case "KeyS": S.brake = 0; break;
      default:
        if (e.code in KEY_MASK) S.mask &= ~KEY_MASK[e.code];
    }
  });

  // ================= keamanan: netralkan saat kehilangan fokus =================
  function clearAll() {
    S.mask = 0;
    S.gas = 0;
    S.brake = 0;
    S.kbSteer = 0;
    S.touchSteer = 0;
  }
  window.addEventListener("blur", () => { clearAll(); sendState(); });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearAll(); sendState(); }
  });

  // ================= panel setelan =================
  const ui = {
    range: $("#rangeCtl"), dead: $("#deadCtl"), sens: $("#sensCtl"), smooth: $("#smoothCtl"),
    invert: $("#invertCtl"), fs: $("#fsCtl"),
    rangeVal: $("#rangeVal"), deadVal: $("#deadVal"), sensVal: $("#sensVal"), smoothVal: $("#smoothVal"),
  };

  function syncSettingsUI() {
    const range = cfg.mode === "yaw" ? cfg.rangeYaw : cfg.rangeTilt;
    ui.range.value = range;
    ui.dead.value = cfg.dead;
    ui.sens.value = cfg.sens;
    ui.smooth.value = cfg.smooth;
    ui.invert.checked = !!cfg.invert;
    ui.fs.checked = !!cfg.fs;
    ui.rangeVal.textContent = range + "°";
    ui.deadVal.textContent = cfg.dead + "%";
    ui.sensVal.textContent = cfg.sens + "%";
    ui.smoothVal.textContent = cfg.smooth + "%";
    document.querySelectorAll("#modeSeg button").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === cfg.mode);
    });
  }

  ui.range.addEventListener("input", () => {
    const v = clamp(parseInt(ui.range.value, 10) || 0, 10, 120);
    if (cfg.mode === "yaw") cfg.rangeYaw = v; else cfg.rangeTilt = v;
    ui.rangeVal.textContent = v + "°";
    saveCfg();
  });
  ui.dead.addEventListener("input", () => {
    cfg.dead = parseInt(ui.dead.value, 10) || 0;
    ui.deadVal.textContent = cfg.dead + "%";
    saveCfg();
  });
  ui.sens.addEventListener("input", () => {
    cfg.sens = parseInt(ui.sens.value, 10) || 100;
    ui.sensVal.textContent = cfg.sens + "%";
    saveCfg();
  });
  ui.smooth.addEventListener("input", () => {
    cfg.smooth = parseInt(ui.smooth.value, 10) || 0;
    ui.smoothVal.textContent = cfg.smooth + "%";
    saveCfg();
  });
  ui.invert.addEventListener("change", () => { cfg.invert = ui.invert.checked; saveCfg(); });
  ui.fs.addEventListener("change", () => { cfg.fs = ui.fs.checked; saveCfg(); });

  document.querySelectorAll("#modeSeg button").forEach((b) => {
    b.addEventListener("click", () => setMode(b.dataset.mode));
  });

  $("#btnCalib2").addEventListener("click", calibrate);
  $("#btnResetCfg").addEventListener("click", () => {
    cfg = Object.assign({}, DEFAULTS);
    S.center = null;
    saveCfg();
    syncSettingsUI();
    updateModeChip();
    toast("Setelan direset");
  });

  $("#btnSettings").addEventListener("click", (e) => {
    e.currentTarget.blur(); // supaya tombol Space tidak memicu klik ulang
    $("#settings").classList.remove("hidden");
  });
  $("#btnCloseSettings").addEventListener("click", () => $("#settings").classList.add("hidden"));
  modeChip.addEventListener("click", () => modeChip.blur());
  $("#btnCalib").addEventListener("click", (e) => e.currentTarget.blur());

  // ================= layar mulai =================
  $("#startBtn").addEventListener("click", async () => {
    const btn = $("#startBtn");
    btn.disabled = true;

    // Minta izin sensor SEBELUM await lain (iOS mensyaratkan dari gestur pengguna).
    if (typeof DeviceOrientationEvent !== "undefined" &&
        typeof DeviceOrientationEvent.requestPermission === "function") {
      try {
        const res = await DeviceOrientationEvent.requestPermission();
        if (res !== "granted") {
          $("#startErr").textContent =
            "Izin sensor ditolak. Izinkan lewat ikon 🔒 di bilah alamat, atau pakai mode Sentuh.";
          setMode("touch", true);
        }
      } catch (e) {
        $("#startErr").textContent = "Gagal meminta izin sensor: " + e;
      }
    }

    // Layar penuh + kunci lanskap (gagal = tidak masalah, terutama di iPhone).
    if (cfg.fs) {
      try {
        if (document.documentElement.requestFullscreen) {
          await document.documentElement.requestFullscreen();
        }
      } catch (e) { /* ditolak browser */ }
      try {
        if (screen.orientation && screen.orientation.lock) {
          await screen.orientation.lock("landscape");
        }
      } catch (e) { /* tidak didukung */ }
    }

    $("#overlay").classList.add("gone");
    S.started = true;
    connect();
    setTimeout(checkSensors, 3200);
  });

  // ================= info awal =================
  fetch("/api/status")
    .then((r) => r.json())
    .then((st) => {
      $("#serverInfo").textContent =
        "server v" + st.version + " · " + (st.ssl ? "HTTPS" : "HTTP") +
        (st.pad_ok ? "" : " · mode demo (driver gamepad belum aktif)");
    })
    .catch(() => { $("#serverInfo").textContent = "server tidak terjangkau"; });

  document.addEventListener("contextmenu", (e) => e.preventDefault());

  // ================= mulai =================
  syncSettingsUI();
  updateModeChip();
  updateConn();
  requestAnimationFrame(frame);
})();
