"use strict";
const $ = (id) => document.getElementById(id);
const frequencies = ["62", "250", "1k", "4k", "8k", "16k"];
const descriptions = ["低音", "中低音", "中音", "中高音", "高音", "超高音"];
let snapshot = null,
  gains = [0, 0, 0, 0, 0, 0],
  volume = 0;
let pending = { eq: null, volume: null },
  busyKind = null,
  pumping = false,
  pumpTimer;
let connected = false,
  backendReachable = false,
  toastTimer,
  installPrompt;
let syncLabel = "",
  rememberedVolume = 6,
  draggingBand = null;
const sliders = [];
const format = (n) => (n > 0 ? `+${n}` : String(n).replace("-", "−"));
const equal = (a, b) =>
  Array.isArray(a) &&
  Array.isArray(b) &&
  a.length === b.length &&
  a.every((v, i) => v === b[i]);

function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("toast").hidden = true;
  }, 3800);
}

async function api(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    body === undefined ? 5000 : 15000,
  );
  try {
    const response = await fetch(new URL(`api/${path}`, document.baseURI), {
      method: body === undefined ? "GET" : "POST",
      cache: "no-store",
      signal: controller.signal,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "这次操作未成功，请重试");
    return result;
  } finally {
    clearTimeout(timeout);
  }
}

function updateEnabled() {
  connected = backendReachable && snapshot?.connection === "online";
  const eqReady = connected && Array.isArray(snapshot?.state?.gains);
  ["reset", "save"].forEach((id) => {
    $(id).disabled = !eqReady;
  });
  ["mute", "volume", "volumeDown", "volumeUp"].forEach((id) => {
    $(id).disabled = !connected;
  });
  document.querySelectorAll(".band button, .preset").forEach((el) => {
    el.disabled = !eqReady;
  });
  sliders.forEach(({ slider }) => {
    slider.setAttribute("aria-disabled", String(!eqReady));
    slider.tabIndex = eqReady ? 0 : -1;
  });
}

function renderConnection() {
  updateEnabled();
  const status = backendReachable ? snapshot?.connection : "offline";
  const labels = {
    online: "已连接",
    discovering: "搜索中",
    connecting: "连接中",
    offline: "未连接",
  };
  const connection = $("connection");
  connection.className = `connection ${status || ""}`;
  connection.querySelector("span").textContent = backendReachable
    ? labels[status] || "连接中"
    : "服务未连接";
  $("deviceName").textContent = snapshot?.device?.name || "寻找局域网音箱";
  const warning = !backendReachable
    ? "暂时无法连接服务。请确认已连接家中局域网或 Tailscale，页面会自动重试。"
    : status !== "online"
      ? snapshot?.error ||
        "正在自动寻找音箱。请确认音箱已开机，并与服务处于同一局域网。"
      : !snapshot?.state?.gains
        ? "当前固件未提供六段 EQ，已暂停 EQ 操作。"
        : !snapshot?.state?.eq_active
          ? "当前使用其他音效。调整或选择曲线后，将启用自定义 EQ。"
          : "";
  $("notice").textContent = warning;
  $("notice").hidden = !warning;
  $("footerState").textContent = connected ? "已与音箱同步" : "自动重连中";
}

function renderCurve() {
  gains.forEach((g, i) => {
    const { slider, thumb, fill, output } = sliders[i];
    const percent = ((g + 30) / 60) * 100;
    output.textContent = format(g);
    output.classList.toggle("zero", g === 0);
    slider.setAttribute("aria-valuenow", g);
    slider.setAttribute(
      "aria-valuetext",
      `${format(g)}，${frequencies[i]} 赫兹`,
    );
    thumb.style.bottom = `calc(${10 - (20 * percent) / 100}px + ${percent}%)`;
    fill.style.bottom = `${Math.min(percent, 50)}%`;
    fill.style.height = `${Math.abs(percent - 50)}%`;
  });
  const points = gains.map((g, i) => [12 + i * 115.2, 38 - (g / 30) * 26]);
  let path = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1],
      [x1, y1] = points[i];
    path += ` C${x0 + 48},${y0} ${x1 - 48},${y1} ${x1},${y1}`;
  }
  $("curveLine").setAttribute("d", path);
  $("curveArea").setAttribute("d", `${path} L588,76 L12,76 Z`);
  $("curvePoints").replaceChildren(
    ...points.map(([x, y]) => {
      const circle = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "circle",
      );
      circle.setAttribute("cx", x);
      circle.setAttribute("cy", y);
      circle.setAttribute("r", 3);
      return circle;
    }),
  );
  let matching = null;
  (snapshot?.presets || []).forEach((p) => {
    const active =
      equal(gains, p.gains) &&
      (snapshot?.state?.eq_active || pending.eq || busyKind === "eq");
    document
      .querySelectorAll(`[data-preset="${CSS.escape(p.id)}"]`)
      .forEach((button) => {
        button.classList.toggle("active", Boolean(active));
        button.setAttribute("aria-pressed", String(Boolean(active)));
      });
    if (active && !matching) matching = p;
  });
  $("curveName").textContent =
    matching?.name ||
    (snapshot?.state?.eq_active || pending.eq || busyKind === "eq"
      ? "自定义曲线"
      : "设备中的自定义曲线");
  $("syncState").textContent =
    pending.eq || busyKind === "eq"
      ? "正在同步…"
      : syncLabel || (connected ? "已同步" : "等待音箱");
  $("syncState").className =
    pending.eq || busyKind === "eq" ? "pending" : syncLabel ? "error" : "";
}

function renderVolume() {
  $("volumeValue").textContent = snapshot?.state ? volume : "—";
  const max = snapshot?.state?.max_volume || 30;
  $("volumeMax").textContent = `/ ${max}`;
  $("volume").max = max;
  $("volume").min = snapshot?.state?.min_volume || 0;
  $("volume").value = volume;
  $("volumeStatus").textContent =
    pending.volume !== null || busyKind === "volume"
      ? "同步中…"
      : connected
        ? "已同步"
        : "等待连接";
  $("mute").textContent = volume === 0 ? "恢复音量" : "静音";
}

function changeBand(index, value) {
  if (!connected || !snapshot?.state?.gains) return;
  const next = Math.min(30, Math.max(-30, Math.round(value / 5) * 5));
  if (gains[index] === next) return;
  gains[index] = next;
  queueEQ();
}

function queueEQ() {
  pending.eq = { gains: [...gains] };
  syncLabel = "";
  renderCurve();
  schedulePump();
}

function changeVolume(value) {
  if (!connected) return;
  const next = Math.min(
    snapshot.state.max_volume || 30,
    Math.max(snapshot.state.min_volume || 0, Math.round(Number(value))),
  );
  if (next === volume) return;
  if (volume > 0) rememberedVolume = volume;
  volume = next;
  pending.volume = { volume };
  renderVolume();
  schedulePump();
}

function schedulePump() {
  clearTimeout(pumpTimer);
  // One transaction in flight; replace pending drag values instead of queueing every tick.
  pumpTimer = setTimeout(pump, 100);
}

async function pump() {
  if (pumping || !connected) return;
  pumping = true;
  try {
    while (connected && (pending.eq || pending.volume !== null)) {
      busyKind = pending.eq ? "eq" : "volume";
      const kind = busyKind,
        payload = pending[kind];
      pending[kind] = null;
      renderCurve();
      renderVolume();
      try {
        const result = await api(kind, payload);
        busyKind = null;
        accept(result);
      } catch (error) {
        // Never replay a control change after an outage or reconnect.
        pending = { eq: null, volume: null };
        busyKind = null;
        syncLabel = "未同步";
        toast(
          error.name === "AbortError"
            ? "响应超时，请确认音箱连接后重试"
            : error.message,
        );
        await poll();
        break;
      }
    }
  } finally {
    busyKind = null;
    pumping = false;
    renderCurve();
    renderVolume();
  }
}

function createSliders() {
  frequencies.forEach((freq, index) => {
    const band = document.createElement("div");
    band.className = "band";
    const output = document.createElement("output");
    output.textContent = "0";
    output.setAttribute("aria-hidden", "true");
    const plus = document.createElement("button");
    plus.className = "step";
    plus.textContent = "＋";
    plus.disabled = true;
    plus.setAttribute("aria-label", `提高 ${freq} Hz 增益`);
    plus.addEventListener("click", () => changeBand(index, gains[index] + 5));
    const slider = document.createElement("div");
    slider.className = "slider";
    slider.setAttribute("role", "slider");
    slider.tabIndex = -1;
    Object.entries({
      "aria-valuemin": -30,
      "aria-valuemax": 30,
      "aria-valuenow": 0,
      "aria-orientation": "vertical",
      "aria-label": `${freq} Hz ${descriptions[index]}增益`,
      "aria-disabled": true,
    }).forEach(([key, value]) => slider.setAttribute(key, value));
    const rail = document.createElement("div");
    rail.className = "slider-rail";
    const fill = document.createElement("div");
    fill.className = "slider-fill";
    rail.append(fill);
    const thumb = document.createElement("div");
    thumb.className = "slider-thumb";
    slider.append(rail, thumb);
    const fromPointer = (event) => {
      const box = slider.getBoundingClientRect();
      changeBand(
        index,
        (1 - (event.clientY - box.top - 10) / (box.height - 20)) * 60 - 30,
      );
    };
    slider.addEventListener("pointerdown", (event) => {
      if (
        !connected ||
        slider.getAttribute("aria-disabled") === "true" ||
        (event.pointerType === "mouse" && event.button !== 0)
      )
        return;
      event.preventDefault();
      slider.setPointerCapture(event.pointerId);
      draggingBand = index;
      slider.classList.add("dragging");
      slider.focus({ preventScroll: true });
      fromPointer(event);
    });
    slider.addEventListener("pointermove", (event) => {
      if (slider.hasPointerCapture(event.pointerId)) fromPointer(event);
    });
    const finish = () => {
      draggingBand = null;
      slider.classList.remove("dragging");
      clearTimeout(pumpTimer);
      pump();
    };
    slider.addEventListener("pointerup", finish);
    slider.addEventListener("pointercancel", finish);
    slider.addEventListener("lostpointercapture", () => {
      draggingBand = null;
      slider.classList.remove("dragging");
    });
    slider.addEventListener("keydown", (event) => {
      const keys = {
        ArrowUp: 5,
        ArrowRight: 5,
        ArrowDown: -5,
        ArrowLeft: -5,
        PageUp: 10,
        PageDown: -10,
      };
      if (event.key in keys || event.key === "Home" || event.key === "End") {
        event.preventDefault();
        changeBand(
          index,
          event.key === "Home"
            ? -30
            : event.key === "End"
              ? 30
              : gains[index] + keys[event.key],
        );
      }
    });
    const minus = document.createElement("button");
    minus.className = "step";
    minus.textContent = "−";
    minus.disabled = true;
    minus.setAttribute("aria-label", `降低 ${freq} Hz 增益`);
    minus.addEventListener("click", () => changeBand(index, gains[index] - 5));
    const label = document.createElement("span");
    label.className = "band-label";
    label.textContent = `${freq} Hz`;
    const desc = document.createElement("span");
    desc.className = "band-desc";
    desc.textContent = descriptions[index];
    band.append(output, plus, slider, minus, label, desc);
    $("eqSliders").append(band);
    sliders.push({ slider, output, fill, thumb });
  });
}

function presetButton(preset) {
  const button = document.createElement("button");
  button.className = "preset";
  button.dataset.preset = preset.id;
  button.setAttribute("aria-pressed", "false");
  button.setAttribute("aria-label", `应用${preset.name}曲线`);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("preset-icon");
  svg.setAttribute("viewBox", "0 0 32 26");
  svg.setAttribute("aria-hidden", "true");
  const line = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "polyline",
  );
  line.setAttribute(
    "points",
    preset.gains
      .map((g, i) => `${2 + i * 5.6},${13 - (g / 30) * 11}`)
      .join(" "),
  );
  svg.append(line);
  const text = document.createElement("span");
  text.className = "preset-text";
  const title = document.createElement("strong");
  title.textContent = preset.name;
  const desc = document.createElement("small");
  desc.textContent = preset.description;
  text.append(title, desc);
  button.append(svg, text);
  button.addEventListener("click", () => {
    if (connected) {
      gains = [...preset.gains];
      queueEQ();
    }
  });
  return button;
}

let presetsKey = "";
function renderPresets() {
  const key = JSON.stringify(snapshot?.presets || []);
  if (key === presetsKey) return;
  presetsKey = key;
  $("presets").replaceChildren();
  $("customPresets").replaceChildren();
  let count = 0;
  (snapshot?.presets || []).forEach((preset) => {
    if (!preset.custom) {
      $("presets").append(presetButton(preset));
      return;
    }
    count++;
    const row = document.createElement("div");
    row.className = "custom-row";
    const del = document.createElement("button");
    del.className = "delete-preset";
    del.textContent = "×";
    del.setAttribute("aria-label", `删除${preset.name}曲线`);
    del.addEventListener("click", async () => {
      if (!confirm(`删除「${preset.name}」？音箱当前的 EQ 不会改变。`)) return;
      try {
        accept(await api("presets/delete", { id: preset.id }));
        toast("曲线已删除");
      } catch (error) {
        toast(error.message);
      }
    });
    row.append(presetButton(preset), del);
    $("customPresets").append(row);
  });
  if (!count) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "把喜欢的声音保存下来。";
    $("customPresets").append(empty);
  }
  $("presetCount").textContent = `${count} 条`;
}

function accept(data) {
  backendReachable = true;
  if (snapshot && data.revision < snapshot.revision) return;
  snapshot = data;
  if (
    data.state?.gains &&
    !pending.eq &&
    busyKind !== "eq" &&
    draggingBand === null
  )
    gains = [...data.state.gains];
  if (
    data.state &&
    pending.volume === null &&
    busyKind !== "volume" &&
    document.activeElement !== $("volume")
  )
    volume = data.state.volume;
  renderPresets();
  renderConnection();
  renderCurve();
  renderVolume();
  $("infoName").textContent = data.state?.name || data.device?.name || "—";
  $("infoHost").textContent = data.device?.host || "—";
  $("infoFirmware").textContent = data.state?.firmware || "—";
  const select = $("deviceSelect");
  const devicesKey = JSON.stringify(data.devices.map((d) => [d.id, d.name]));
  if (select.dataset.devices !== devicesKey) {
    select.replaceChildren(
      ...data.devices.map((device) => {
        const option = document.createElement("option");
        option.value = device.id;
        option.textContent = device.name;
        return option;
      }),
    );
    select.dataset.devices = devicesKey;
  }
  select.hidden = data.devices.length < 2;
  select.value = data.device?.id || "";
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    accept(await api("state"));
  } catch (_) {
    backendReachable = false;
    renderConnection();
    renderCurve();
    renderVolume();
  } finally {
    polling = false;
  }
}

$("volume").addEventListener("input", (event) =>
  changeVolume(event.target.value),
);
$("volume").addEventListener("change", () => {
  clearTimeout(pumpTimer);
  pump();
});
$("volumeDown").addEventListener("click", () => changeVolume(volume - 1));
$("volumeUp").addEventListener("click", () => changeVolume(volume + 1));
$("mute").addEventListener("click", () =>
  changeVolume(volume === 0 ? rememberedVolume : 0),
);
$("reset").addEventListener("click", () => {
  gains = [0, 0, 0, 0, 0, 0];
  queueEQ();
});
$("rescan").addEventListener("click", async () => {
  try {
    await api("discover", {});
    toast("正在重新搜索局域网音箱…");
    setTimeout(poll, 1800);
  } catch (_) {
    toast("无法连接服务，请确认局域网连接");
  }
});
$("deviceSelect").addEventListener("change", async (event) => {
  pending = { eq: null, volume: null };
  try {
    accept(await api("device", { id: event.target.value }));
    setTimeout(poll, 500);
  } catch (error) {
    toast(error.message);
  }
});
$("save").addEventListener("click", () => {
  $("presetName").value = "";
  $("saveDialog").showModal();
  $("presetName").focus();
});
$("saveForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("saveSubmit").disabled = true;
  try {
    accept(
      await api("presets", {
        name: $("presetName").value.trim(),
        gains: [...gains],
      }),
    );
    $("saveDialog").close();
    toast("曲线已保存");
  } catch (error) {
    toast(error.message);
  } finally {
    $("saveSubmit").disabled = false;
  }
});
document
  .querySelectorAll("[data-close]")
  .forEach((button) =>
    button.addEventListener("click", () => $(button.dataset.close).close()),
  );
document.querySelectorAll("dialog").forEach((dialog) =>
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (
        event.clientX < r.left ||
        event.clientX > r.right ||
        event.clientY < r.top ||
        event.clientY > r.bottom
      )
        dialog.close();
    }
  }),
);
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
});
$("installButton").addEventListener("click", async () => {
  if (installPrompt) {
    await installPrompt.prompt();
    installPrompt = null;
    return;
  }
  if (!window.isSecureContext)
    $("secureHint").textContent =
      "完整 PWA 功能需要 HTTPS，请使用配置的 HTTPS 域名打开。";
  if (
    !/iPhone|iPad|iPod/.test(navigator.userAgent) &&
    !(navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
  ) {
    $("installInstructions").textContent =
      "在浏览器菜单中选择「安装 S300」或「将网页安装为应用」。在 iPhone 上，请用 Safari 的「分享」→「添加到主屏幕」。";
  }
  $("installDialog").showModal();
});
if (matchMedia("(display-mode: standalone)").matches || navigator.standalone)
  $("installButton").hidden = true;
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("./sw.js").catch(() => {});
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poll();
});
window.addEventListener("online", poll);
createSliders();
renderCurve();
renderVolume();
poll();
setInterval(() => {
  if (!document.hidden) poll();
}, 2000);
