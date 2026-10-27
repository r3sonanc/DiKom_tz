"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const canvas = $("mapCanvas");
  const ctx = canvas.getContext("2d");
  const colors = ["#2f76de", "#a270c8", "#e8953d", "#239d91", "#d76387"];
  const stateLabels = { idle: "Ожидает приказ", moving: "В движении", paused: "Остановлен", stopped: "Остановлен", manual: "Ручное управление", error: "Ошибка", waiting: "Ожидает путь", parked: "На парковке", offline: "Нет связи", connecting: "Подключается" };
  const errorLabels = { STUCK: "Движение заблокировано", INVALID_COMMAND: "Некорректная команда", INVALID_PATH: "Некорректный маршрут", POINT_NOT_FOUND: "Узел не найден", OFF_GRAPH: "Робот вне маршрута", NOT_AT_NODE: "Робот вне начального узла", COLLISION_WALL: "Остановка перед стеной", COLLISION_ROBOT: "Остановка перед другим роботом" };
  const faultLabels = { stuck: "Застревание", link_lost: "Потеря связи", throttle: "Замедление связи" };
  const state = { snapshot: null, robots: [], nodes: new Map(), selectedId: "", targetId: "", socket: null, connected: false, lastMessage: 0, trails: new Map(), events: [], seenEvents: new Set(), mapKey: "", manualId: null, manualChanging: false };
  const view = { scale: 35, x: 0, y: 0, width: 1, height: 1, fitted: false, dirty: true };
  const keys = new Set();
  let pointer = null;
  let reconnectTimer = null;
  let reconnectDelay = 600;
  let toastTimer = null;
  let manualPending = null;
  let manualPump = null;
  const fleetRows = new Map();

  function setText(id, value) { $(id).textContent = String(value); }
  function number(value, digits = 2) { return value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—"; }
  function selectedRobot() { return state.robots.find((robot) => robot.id === state.selectedId); }
  function robotColor(id) { return colors[Math.max(0, state.robots.findIndex((robot) => robot.id === id)) % colors.length]; }
  function robotName(id) { return String(id).replace(/^robot(\d+)$/i, "Робот $1"); }
  function errorText(error) {
    if (!error) return "";
    const message = typeof error === "string" ? error : error.message;
    return message && /[а-яё]/i.test(message) ? message : errorLabels[error.code] || "Ошибка робота";
  }
  function eventText(message) {
    if (message && typeof message === "object") return errorText(message);
    return String(message).replace(/\{['"]code['"]:\s*['"]([^'"]+)['"],\s*['"]message['"]:\s*(['"])(.*?)\2\}/g, (_, code, quote, detail) => errorText({ code, message: detail })).replace(/\brobot(\d+)\b/gi, "Робот $1");
  }
  function robotStatus(robot) { return robot.connected === false ? "Нет связи" : stateLabels[robot.state] || "Ожидание"; }
  function orderText(robot) {
    if (!robot.order) return "Нет активного приказа";
    if (typeof robot.order === "string") return robot.order;
    const types = { move: "Перемещение", park: "Парковка", manual: "Ручное управление", stop: "Остановка" };
    return `${types[robot.order.type] || "Приказ"}${robot.order.target ? ` → ${robot.order.target}` : ""}${robot.order.id ? ` · ${robot.order.id}` : ""}`;
  }
  function normalizeRobot(robot) {
    const route = (robot.route || []).filter((node, index, nodes) => index === 0 || node !== nodes[index - 1]);
    return { ...robot, id: String(robot.id), connected: robot.connected !== false, x: robot.x ?? robot.pose?.x ?? 0, y: robot.y ?? robot.pose?.y ?? 0, theta: robot.theta ?? robot.pose?.yaw ?? 0, v: robot.v ?? robot.velocity?.linear ?? 0, omega: robot.omega ?? robot.velocity?.angular ?? 0, state: robot.state ?? robot.status ?? "idle", fault: robot.fault || {}, route };
  }
  function toast(message, isError = false) {
    const el = $("toast");
    el.textContent = eventText(message);
    el.classList.toggle("error", isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6500 : 3500);
  }
  function addEvent(message, level = "info", time = new Date().toISOString()) {
    state.events.push({ message: eventText(message), level, time });
    if (state.events.length > 80) state.events.splice(0, state.events.length - 80);
    const fragment = document.createDocumentFragment();
    for (const entry of state.events.slice(-35).reverse()) {
      const row = document.createElement("li");
      row.className = `event ${["warning", "error"].includes(entry.level) ? entry.level : ""}`;
      const timestamp = document.createElement("time");
      const date = new Date(entry.time);
      timestamp.textContent = Number.isNaN(date.getTime()) ? String(entry.time) : date.toLocaleTimeString("ru-RU", { hour12: false });
      const messageEl = document.createElement("span");
      messageEl.textContent = entry.message;
      row.append(timestamp, messageEl);
      fragment.append(row);
    }
    $("eventsList").replaceChildren(fragment);
  }

  async function request(path, payload, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4500);
    try {
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal, ...options });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || result.ok === false) {
        const detail = typeof result.detail === "string" ? result.detail : Array.isArray(result.detail) ? result.detail.map((issue) => issue.msg).filter(Boolean).join("; ") || "Проверьте параметры команды" : "Команда отклонена сервером";
        throw new Error(detail);
      }
      return result;
    } catch (error) {
      throw new Error(error.name === "AbortError" ? "Сервер не ответил вовремя" : error instanceof TypeError ? "Не удалось связаться с сервером" : eventText(error.message || "Ошибка соединения"));
    } finally { clearTimeout(timeout); }
  }
  function command(id, body, options) { return request(`/api/robots/${encodeURIComponent(id)}/commands`, body, options); }
  async function operatorCommand(body, label) {
    const robot = selectedRobot();
    if (!robot || !state.connected) return;
    try {
      if (["stop", "resume"].includes(body.type)) { state.manualChanging = true; await releaseKeys(); }
      await command(robot.id, body);
      if (["resume", "stop"].includes(body.type)) { state.manualId = null; robot.state = body.type === "stop" ? "paused" : "idle"; }
      addEvent(`${robot.id}: ${label}`);
      toast(label);
    } catch (error) { toast(error.message, true); addEvent(`${robot.id}: ${error.message}`, "error"); }
    finally { state.manualChanging = false; updateDetails(); }
  }

  function populateSelect(el, items, current, placeholder) {
    const fragment = document.createDocumentFragment();
    if (placeholder) { const option = document.createElement("option"); option.value = ""; option.textContent = placeholder; fragment.append(option); }
    for (const [value, label] of items) { const option = document.createElement("option"); option.value = value; option.textContent = label; fragment.append(option); }
    el.replaceChildren(fragment);
    el.value = current;
  }
  function selectRobot(id) {
    if (state.selectedId === id) return;
    releaseKeys();
    state.selectedId = id;
    $("robotSelect").value = id;
    updateDetails();
    updateFleet();
    view.dirty = true;
  }
  function updateDetails() {
    const robot = selectedRobot();
    const usable = Boolean(robot && state.connected);
    const remoteManual = robot?.state === "manual";
    const manual = Boolean(robot && (state.manualId === robot.id || remoteManual));
    const manualLocked = Boolean(state.manualId && state.manualId !== robot?.id);
    setText("selectedRobotName", robot ? robotName(robot.id) : "Робот не выбран");
    setText("selectedRobotState", robot ? robotStatus(robot) : "Ожидание телеметрии");
    setText("robotAvatar", robot?.id.replace(/^robot/i, "Р").slice(0, 4) || "Р");
    $("robotAvatar").style.color = robot ? robotColor(robot.id) : "";
    $("robotStateDot").className = `status-dot ${robot ? robot.connected === false || robot.error ? "error" : "online" : ""}`;
    setText("positionValue", robot ? `${number(robot.x)} / ${number(robot.y)}` : "—");
    setText("headingValue", robot ? `${number(((robot.theta * 180 / Math.PI) % 360 + 360) % 360, 1)}°` : "—");
    setText("speedValue", robot ? number(robot.v) : "—");
    setText("angularValue", robot ? number(robot.omega) : "—");
    setText("orderValue", robot ? orderText(robot) : "—");
    setText("routeValue", robot?.route.length ? robot.route.join(" → ") : "Маршрут не назначен");
    $("robotError").hidden = !robot?.error;
    $("robotError").textContent = errorText(robot?.error);
    $("manualEnabled").checked = manual;
    $("manualEnabled").disabled = !usable || state.manualChanging || manualLocked || robot?.connected === false;
    $("moveButton").disabled = !usable || !state.targetId || manual || robot?.connected === false;
    $("parkButton").disabled = !usable || manual || robot?.connected === false;
    $("stopButton").disabled = !usable;
    $("resumeButton").disabled = !usable || robot?.connected === false;
    $("clearErrorButton").disabled = !usable || !robot?.error;
    for (const button of document.querySelectorAll(".key")) button.disabled = !usable || !manual || state.manualId !== robot?.id || robot?.connected === false || state.manualChanging;
    for (const [field, key] of [["faultStuck", "stuck"], ["faultLink", "link_lost"], ["faultThrottle", "throttle"]]) {
      $(field).disabled = !usable;
      $(field).checked = Boolean(robot?.fault[key]);
    }
  }
  function updateFleet() {
    for (const [id, row] of fleetRows) if (!state.robots.some((robot) => robot.id === id)) { row.remove(); fleetRows.delete(id); }
    if (!fleetRows.size) $("fleetBody").replaceChildren();
    for (const robot of state.robots) {
      let row = fleetRows.get(robot.id);
      if (!row) {
        row = document.createElement("tr");
        row.tabIndex = 0;
        row.setAttribute("aria-label", `Выбрать: ${robotName(robot.id)}`);
        row.addEventListener("click", () => selectRobot(robot.id));
        row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRobot(robot.id); } });
        for (let index = 0; index < 6; index++) row.append(document.createElement("td"));
        const identityWrap = document.createElement("span"); identityWrap.className = "fleet-id";
        const mark = document.createElement("i"); mark.className = "fleet-color";
        const name = document.createElement("span"); name.textContent = robotName(robot.id);
        identityWrap.append(mark, name); row.cells[0].append(identityWrap);
        const badge = document.createElement("span"); row.cells[1].append(badge);
        $("fleetBody").append(row); fleetRows.set(robot.id, row);
      }
      row.className = `fleet-row${robot.id === state.selectedId ? " selected" : ""}`;
      row.querySelector(".fleet-color").style.background = robotColor(robot.id);
      const badge = row.cells[1].firstElementChild;
      badge.className = `state-badge ${robot.error || !robot.connected ? "error" : robot.state === "moving" || robot.state === "manual" ? "moving" : robot.state === "idle" || robot.state === "parked" ? "idle" : ""}`;
      badge.textContent = robotStatus(robot);
      row.cells[2].textContent = `${number(robot.x)} / ${number(robot.y)}`;
      row.cells[3].textContent = `${number(robot.v)} м/с`;
      row.cells[4].textContent = orderText(robot); row.cells[4].title = errorText(robot.error);
      const link = row.cells[5]; link.className = `link-cell${robot.connected === false ? " lost" : ""}`;
      link.textContent = robot.connected === false ? `Нет связи · ${number(robot.last_seen, 1)} с` : robot.fault.throttle ? `Замедление · ${number(robot.last_seen, 1)} с` : `В сети · ${number(robot.last_seen, 1)} с`;
    }
    setText("fleetCount", `${state.robots.length} роботов`);
  }
  function acceptSnapshot(snapshot) {
    if (!snapshot.map || !Array.isArray(snapshot.robots)) return;
    const priorIds = state.robots.map((robot) => robot.id).join("|");
    state.snapshot = snapshot;
    state.robots = snapshot.robots.map(normalizeRobot);
    state.nodes = new Map((snapshot.map.nodes || []).map((node) => [String(node.id), node]));
    state.lastMessage = performance.now();
    const mapKey = `${snapshot.world_id || ""}:${snapshot.map.name}:${JSON.stringify(snapshot.map.nodes)}`;
    if (mapKey !== state.mapKey) {
      state.mapKey = mapKey;
      keys.clear(); updateKeys(); manualPending = null; state.manualId = null;
      $("mapSelect").value = snapshot.map.name === "hardcore" ? "hardcore" : "normal";
      $("robotCount").value = state.robots.length;
      state.trails.clear();
      state.targetId = "";
      populateSelect($("targetSelect"), Array.from(state.nodes.values(), (node) => [String(node.id), `${node.id}${node.parking ? " · парковка" : ""}`]), "", "Выберите узел");
      fitMap();
    }
    const ids = state.robots.map((robot) => robot.id).join("|");
    if (!state.robots.some((robot) => robot.id === state.selectedId)) state.selectedId = state.robots[0]?.id || "";
    if (ids !== priorIds) populateSelect($("robotSelect"), state.robots.map((robot) => [robot.id, robotName(robot.id)]), state.selectedId);
    if (state.manualId && !state.robots.some((robot) => robot.id === state.manualId)) { releaseKeys(); state.manualId = null; }
    for (const robot of state.robots) {
      const trail = state.trails.get(robot.id) || [];
      const previous = trail[trail.length - 1];
      if (!previous || Math.hypot(previous.x - robot.x, previous.y - robot.y) > .06) trail.push({ x: robot.x, y: robot.y });
      if (trail.length > 120) trail.splice(0, trail.length - 120);
      state.trails.set(robot.id, trail);
    }
    for (const id of state.trails.keys()) if (!state.robots.some((robot) => robot.id === id)) state.trails.delete(id);
    setText("onlineMetric", `${state.robots.filter((robot) => robot.connected !== false).length}/${state.robots.length}`);
    setText("movingMetric", state.robots.filter((robot) => Math.abs(robot.v) > .01 || Math.abs(robot.omega) > .01).length);
    const metrics = snapshot.metrics || {};
    setText("completedMetric", metrics.completed_tasks ?? metrics.completed_orders ?? metrics.completed ?? 0);
    setText("errorMetric", state.robots.filter((robot) => robot.error || robot.connected === false).length);
    $("darkFactory").checked = Boolean(snapshot.dark_factory);
    $("emptyState").hidden = true;
    const mapLabel = snapshot.map.name === "hardcore" ? "Узкие коридоры" : "Обычный склад";
    setText("mapCaption", `${mapLabel} · ${state.nodes.size} узлов · ${(snapshot.map.edges || []).length} рёбер${snapshot.dispatcher?.paused ? " · парк на паузе" : ""}`);
    updateRobotCount();
    const seconds = Number(snapshot.sim_time ?? snapshot.time ?? 0);
    const timestamp = new Date(snapshot.time);
    setText("simulationTime", Number.isFinite(seconds) ? `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}` : !Number.isNaN(timestamp.getTime()) ? timestamp.toLocaleTimeString("ru-RU", { hour12: false }) : "—");
    for (const entry of snapshot.events || []) {
      const key = `${entry.time}|${entry.level}|${entry.message}`;
      if (state.seenEvents.has(key)) continue;
      state.seenEvents.add(key);
      if (state.seenEvents.size > 300) state.seenEvents.delete(state.seenEvents.values().next().value);
      addEvent(entry.message, entry.level, entry.time);
    }
    updateDetails(); updateFleet(); view.dirty = true;
  }

  function setConnection(online, text) {
    const changed = state.connected !== online;
    state.connected = online;
    $("connectionDot").className = `status-dot ${online ? "online" : "error"}`;
    setText("connectionText", text);
    $("darkFactory").disabled = !online;
    $("resetButton").disabled = !online;
    if (!online) releaseKeys();
    if (changed) updateDetails();
  }
  function connect() {
    clearTimeout(reconnectTimer);
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
    state.socket = socket;
    socket.addEventListener("open", () => { if (state.socket !== socket) return; reconnectDelay = 600; setConnection(true, "Подключено"); });
    socket.addEventListener("message", (event) => {
      if (state.socket !== socket) return;
      try { const snapshot = JSON.parse(event.data); if (!snapshot.type || snapshot.type === "snapshot") { acceptSnapshot(snapshot); setConnection(true, "Подключено"); } }
      catch { console.error("Не удалось прочитать данные симуляции"); }
    });
    socket.addEventListener("error", () => socket.close());
    socket.addEventListener("close", () => {
      if (state.socket !== socket) return;
      setConnection(false, "Переподключение…");
      reconnectTimer = setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(6000, reconnectDelay * 1.7);
    });
  }

  function worldPoint(x, y) { return { x: view.x + x * view.scale, y: view.y - y * view.scale }; }
  function screenPoint(event) { const box = canvas.getBoundingClientRect(); return { x: event.clientX - box.left, y: event.clientY - box.top }; }
  function fitMap() {
    if (!state.snapshot?.map) return;
    const { width = 16, height = 12 } = state.snapshot.map;
    view.scale = Math.min((view.width - 75) / width, (view.height - 90) / height);
    view.scale = Math.max(5, view.scale);
    view.x = (view.width - width * view.scale) / 2;
    view.y = (view.height + height * view.scale) / 2 + 5;
    view.fitted = true; view.dirty = true;
  }
  function zoom(factor, center = { x: view.width / 2, y: view.height / 2 }) {
    const scale = Math.min(160, Math.max(10, view.scale * factor));
    const ratio = scale / view.scale;
    view.x = center.x - (center.x - view.x) * ratio;
    view.y = center.y - (center.y - view.y) * ratio;
    view.scale = scale; view.dirty = true;
  }
  function resize() {
    const box = $("canvasWrap").getBoundingClientRect();
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    view.width = Math.max(1, box.width); view.height = Math.max(1, box.height);
    canvas.width = Math.round(view.width * ratio); canvas.height = Math.round(view.height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    fitMap(); view.dirty = true;
  }
  function line(from, to, color, width, dash = []) {
    const a = worldPoint(from.x, from.y); const b = worldPoint(to.x, to.y);
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.stroke(); ctx.setLineDash([]);
  }
  function draw() {
    if (!view.dirty) return;
    view.dirty = false;
    ctx.clearRect(0, 0, view.width, view.height);
    const map = state.snapshot?.map;
    if (!map) return;
    const bottomLeft = worldPoint(0, 0); const topRight = worldPoint(map.width, map.height);
    ctx.fillStyle = "#f0f4f9"; ctx.fillRect(bottomLeft.x, topRight.y, map.width * view.scale, map.height * view.scale);
    for (let x = 1; x < map.width; x++) line({ x, y: 0 }, { x, y: map.height }, "#e5ebf3", .7);
    for (let y = 1; y < map.height; y++) line({ x: 0, y }, { x: map.width, y }, "#e5ebf3", .7);
    const edges = (map.edges || []).map((edge) => Array.isArray(edge) ? edge : [edge.from, edge.to]);
    for (const [from, to] of edges) {
      const a = state.nodes.get(String(from)); const b = state.nodes.get(String(to));
      if (a && b) { line(a, b, "#fff", Math.max(8, view.scale * .68)); line(a, b, "#becbdd", 1.25, [4, 4]); }
    }
    for (const wall of map.walls || []) {
      const [x1, y1, x2, y2] = Array.isArray(wall) ? wall : [wall.x1, wall.y1, wall.x2, wall.y2];
      line({ x: x1, y: y1 }, { x: x2, y: y2 }, "#bdcad9", 7);
      line({ x: x1, y: y1 }, { x: x2, y: y2 }, "#778aa2", 2);
    }
    for (const robot of state.robots) {
      const trail = state.trails.get(robot.id) || [];
      if (trail.length > 1) {
        ctx.globalAlpha = .15;
        for (let index = 1; index < trail.length; index++) line(trail[index - 1], trail[index], robotColor(robot.id), 2);
        ctx.globalAlpha = 1;
      }
      if (robot.id === state.selectedId && robot.route.length > 1) {
        for (let index = 1; index < robot.route.length; index++) {
          const a = state.nodes.get(String(robot.route[index - 1])); const b = state.nodes.get(String(robot.route[index]));
          if (a && b) line(a, b, "#4b88e88a", 5);
        }
      }
      if (robot.error || robot.fault.stuck || robot.connected === false) {
        const edge = robot.edge;
        const ends = Array.isArray(edge) ? edge : edge ? [edge.from, edge.to] : [];
        if (ends.length === 2) {
          const a = state.nodes.get(String(ends[0])); const b = state.nodes.get(String(ends[1]));
          if (a && b) line(a, b, "#d9626d", 5, [7, 4]);
        }
        const node = state.nodes.get(String(robot.node_id));
        if (node) { const p = worldPoint(node.x, node.y); ctx.beginPath(); ctx.arc(p.x, p.y, 12, 0, Math.PI * 2); ctx.strokeStyle = "#da6f79"; ctx.lineWidth = 2; ctx.stroke(); }
      }
    }
    for (const node of state.nodes.values()) {
      const p = worldPoint(node.x, node.y);
      const target = String(node.id) === state.targetId;
      if (target) { ctx.beginPath(); ctx.arc(p.x, p.y, 13, 0, Math.PI * 2); ctx.fillStyle = "#347be31c"; ctx.fill(); ctx.strokeStyle = "#4d89e9"; ctx.lineWidth = 1.4; ctx.stroke(); }
      if (node.parking) {
        ctx.fillStyle = "#e4eef5"; ctx.fillRect(p.x - 9, p.y - 9, 18, 18);
        ctx.strokeStyle = "#a7bcd0"; ctx.lineWidth = 1; ctx.strokeRect(p.x - 9, p.y - 9, 18, 18);
        ctx.font = "bold 14px Segoe UI, sans-serif"; ctx.fillStyle = "#5c7f9b"; ctx.textAlign = "center"; ctx.fillText("П", p.x, p.y + 5);
      } else { ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill(); ctx.strokeStyle = target ? "#4c84db" : "#9cafc7"; ctx.lineWidth = 1.5; ctx.stroke(); }
      ctx.font = "13px Segoe UI, sans-serif"; ctx.textAlign = "center"; ctx.fillStyle = target ? "#3671c9" : "#73859c"; ctx.fillText(String(node.id), p.x, p.y + (node.parking ? 27 : 24));
    }
    for (const robot of state.robots) drawRobot(robot);
    const meter = Math.max(1, Math.floor(65 / view.scale));
    const barWidth = meter * view.scale;
    ctx.strokeStyle = "#91a2b6"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(20, view.height - 31); ctx.lineTo(20, view.height - 26); ctx.lineTo(20 + barWidth, view.height - 26); ctx.lineTo(20 + barWidth, view.height - 31); ctx.stroke();
    ctx.fillStyle = "#75869c"; ctx.textAlign = "center"; ctx.font = "13px Segoe UI, sans-serif"; ctx.fillText(`${meter} м`, 20 + barWidth / 2, view.height - 10);
  }
  function drawRobot(robot) {
    const p = worldPoint(robot.x, robot.y);
    const scale = view.scale;
    const color = robotColor(robot.id);
    const selected = robot.id === state.selectedId;
    if (selected) { ctx.beginPath(); ctx.arc(p.x, p.y, scale * .72 + 3, 0, Math.PI * 2); ctx.fillStyle = `${color}13`; ctx.fill(); ctx.strokeStyle = `${color}65`; ctx.lineWidth = 1.3; ctx.stroke(); }
    ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(-robot.theta);
    ctx.shadowColor = "#183b6a18"; ctx.shadowBlur = 5; ctx.shadowOffsetY = 2;
    ctx.fillStyle = "#31435a";
    ctx.fillRect(-.24 * scale, -.39 * scale, .48 * scale, .11 * scale);
    ctx.fillRect(-.24 * scale, .28 * scale, .48 * scale, .11 * scale);
    ctx.fillStyle = robot.connected === false ? "#9aa8b8" : color;
    ctx.beginPath(); ctx.roundRect(-.4 * scale, -.3 * scale, .8 * scale, .6 * scale, Math.min(5, scale * .1)); ctx.fill();
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    ctx.strokeStyle = "#ffffff55"; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.moveTo(.31 * scale, 0); ctx.lineTo(.08 * scale, -.12 * scale); ctx.lineTo(.08 * scale, .12 * scale); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#ffffff45"; ctx.fillRect(-.27 * scale, -.14 * scale, .16 * scale, .28 * scale);
    ctx.restore();
    const label = robotName(robot.id); ctx.font = "600 14px Segoe UI, sans-serif";
    const textWidth = ctx.measureText(label).width;
    ctx.fillStyle = "#ffffffed"; ctx.beginPath(); ctx.roundRect(p.x - textWidth / 2 - 7, p.y - scale * .7 - 25, textWidth + 14, 23, 4); ctx.fill();
    ctx.textAlign = "center"; ctx.fillStyle = robot.error || robot.connected === false ? "#c25b65" : color; ctx.fillText(label, p.x, p.y - scale * .7 - 8);
    if (robot.error || robot.connected === false) { ctx.beginPath(); ctx.arc(p.x + scale * .42, p.y - scale * .33, 7, 0, Math.PI * 2); ctx.fillStyle = "#e56a72"; ctx.fill(); ctx.fillStyle = "#fff"; ctx.font = "bold 12px Segoe UI"; ctx.fillText("!", p.x + scale * .42, p.y - scale * .33 + 4); }
  }

  function clickMap(point) {
    let hit = state.robots.find((robot) => { const p = worldPoint(robot.x, robot.y); return Math.hypot(p.x - point.x, p.y - point.y) < Math.max(15, view.scale * .6); });
    if (hit) { selectRobot(hit.id); return; }
    let distance = 18;
    for (const node of state.nodes.values()) {
      const p = worldPoint(node.x, node.y); const current = Math.hypot(p.x - point.x, p.y - point.y);
      if (current < distance) { hit = node; distance = current; }
    }
    if (hit) { state.targetId = String(hit.id); $("targetSelect").value = state.targetId; updateDetails(); view.dirty = true; }
  }
  canvas.addEventListener("wheel", (event) => { event.preventDefault(); zoom(Math.exp(-event.deltaY * .0015), screenPoint(event)); }, { passive: false });
  canvas.addEventListener("pointerdown", (event) => { if (event.button !== 0) return; const p = screenPoint(event); pointer = { id: event.pointerId, startX: p.x, startY: p.y, x: p.x, y: p.y, moved: false }; canvas.setPointerCapture(event.pointerId); });
  canvas.addEventListener("pointermove", (event) => { if (!pointer || pointer.id !== event.pointerId) return; const p = screenPoint(event); if (Math.hypot(p.x - pointer.startX, p.y - pointer.startY) > 4) pointer.moved = true; if (pointer.moved) { view.x += p.x - pointer.x; view.y += p.y - pointer.y; view.dirty = true; } pointer.x = p.x; pointer.y = p.y; });
  canvas.addEventListener("pointerup", (event) => { if (!pointer || pointer.id !== event.pointerId) return; if (!pointer.moved) clickMap(screenPoint(event)); pointer = null; });
  canvas.addEventListener("pointercancel", () => { pointer = null; });
  $("zoomIn").addEventListener("click", () => zoom(1.25));
  $("zoomOut").addEventListener("click", () => zoom(.8));
  $("fitMap").addEventListener("click", fitMap);
  $("robotSelect").addEventListener("change", (event) => selectRobot(event.target.value));
  $("targetSelect").addEventListener("change", (event) => { state.targetId = event.target.value; updateDetails(); view.dirty = true; });
  $("moveButton").addEventListener("click", () => operatorCommand({ type: "move", target: state.targetId }, `Назначено перемещение → ${state.targetId}`));
  $("parkButton").addEventListener("click", () => operatorCommand({ type: "park" }, "Назначена парковка"));
  $("stopButton").addEventListener("click", () => operatorCommand({ type: "stop" }, "Парк остановлен"));
  $("resumeButton").addEventListener("click", () => operatorCommand({ type: "resume" }, "Движение возобновлено"));
  $("clearErrorButton").addEventListener("click", () => operatorCommand({ type: "clear_error" }, "Ошибка сброшена"));
  $("clearEvents").addEventListener("click", () => { state.events.length = 0; $("eventsList").replaceChildren(); addEvent("Журнал интерфейса очищен"); });
  function updateRobotCount() { const max = $("mapSelect").value === "hardcore" ? 5 : 3; $("robotCount").max = max; if (Number($("robotCount").value) > max) $("robotCount").value = max; }
  $("mapSelect").addEventListener("change", updateRobotCount);
  $("resetButton").addEventListener("click", async () => {
    const count = Number($("robotCount").value); const max = Number($("robotCount").max);
    if (!Number.isInteger(count) || count < 1 || count > max) { toast(`Укажите число роботов от 1 до ${max}`, true); return; }
    state.manualChanging = true; await releaseKeys();
    $("resetButton").disabled = true;
    try { await request("/api/reset", { map: $("mapSelect").value, robots: count }); state.trails.clear(); state.manualId = null; toast("Создана новая симуляция"); addEvent("Симуляция перезапущена"); }
    catch (error) { toast(error.message, true); }
    finally { state.manualChanging = false; $("resetButton").disabled = !state.connected; updateDetails(); }
  });
  $("darkFactory").addEventListener("change", async (event) => {
    const enabled = event.target.checked;
    try { await request("/api/dark-factory", { enabled }); addEvent(`Тёмный цех ${enabled ? "включён" : "выключен"}`); }
    catch (error) { event.target.checked = !enabled; toast(error.message, true); }
  });
  for (const [id, field] of [["faultStuck", "stuck"], ["faultLink", "link_lost"], ["faultThrottle", "throttle"]]) {
    $(id).addEventListener("change", async (event) => {
      const robot = selectedRobot(); if (!robot) return;
      const enabled = event.target.checked;
      try { await request(`/api/robots/${encodeURIComponent(robot.id)}/faults`, { [field]: enabled }); addEvent(`${robot.id}: ${faultLabels[field].toLowerCase()} ${enabled ? "— включено" : "— выключено"}`, enabled ? "warning" : "info"); }
      catch (error) { event.target.checked = !enabled; toast(error.message, true); }
    });
  }

  function updateKeys() { for (const button of document.querySelectorAll(".key")) button.classList.toggle("pressed", keys.has(button.dataset.key)); }
  function manualVelocity() { return { linear: (Number(keys.has("w")) - Number(keys.has("s"))) * .5, angular: (Number(keys.has("a")) - Number(keys.has("d"))) * 1.25 }; }
  function sendManual(force = false) {
    const id = state.manualId;
    if (!id || (!state.connected && !force) || (!force && !keys.size)) return manualPump;
    manualPending = { id, velocity: manualVelocity() };
    if (manualPump) return manualPump;
    manualPump = (async () => {
      while (manualPending) {
        const next = manualPending; manualPending = null;
        try { await command(next.id, { type: "manual", ...next.velocity }); }
        catch (error) { manualPending = null; keys.clear(); updateKeys(); toast(error.message, true); }
      }
    })().finally(() => { manualPump = null; });
    return manualPump;
  }
  function releaseKeys() {
    const hadKeys = keys.size > 0;
    keys.clear(); updateKeys();
    if (hadKeys || manualPump) return sendManual(true);
    return Promise.resolve();
  }
  $("manualEnabled").addEventListener("change", async (event) => {
    const robot = selectedRobot(); if (!robot) return;
    const enabled = event.target.checked;
    state.manualChanging = true; updateDetails();
    try {
      await releaseKeys();
      await command(robot.id, enabled ? { type: "manual", linear: 0, angular: 0 } : { type: "resume" });
      state.manualId = enabled ? robot.id : null;
      robot.state = enabled ? "manual" : "idle";
      addEvent(`${robot.id}: ручное управление ${enabled ? "включено, остальной парк на паузе" : "выключено"}`);
    } catch (error) { toast(error.message, true); }
    finally { state.manualChanging = false; updateDetails(); }
  });
  function canDrive() { return Boolean(state.connected && state.manualId && state.manualId === state.selectedId && !state.manualChanging && selectedRobot()?.connected !== false); }
  document.addEventListener("keydown", (event) => {
    const key = { KeyW: "w", KeyA: "a", KeyS: "s", KeyD: "d" }[event.code];
    if (event.code === "Escape") { releaseKeys(); return; }
    const textField = ["SELECT", "TEXTAREA"].includes(event.target.tagName) || (event.target.tagName === "INPUT" && !["checkbox", "button"].includes(event.target.type));
    if (!key || !canDrive() || textField || event.target.isContentEditable) return;
    event.preventDefault();
    if (!keys.has(key)) { keys.add(key); updateKeys(); sendManual(); }
  });
  document.addEventListener("keyup", (event) => {
    const key = { KeyW: "w", KeyA: "a", KeyS: "s", KeyD: "d" }[event.code];
    if (!key || !keys.has(key)) return;
    event.preventDefault(); keys.delete(key); updateKeys(); sendManual(true);
  });
  for (const button of document.querySelectorAll(".key")) {
    button.addEventListener("pointerdown", (event) => { if (!canDrive() || event.button !== 0) return; event.preventDefault(); button.setPointerCapture(event.pointerId); keys.add(button.dataset.key); updateKeys(); sendManual(); });
    const release = () => { if (keys.delete(button.dataset.key)) { updateKeys(); sendManual(true); } };
    button.addEventListener("pointerup", release); button.addEventListener("pointercancel", release); button.addEventListener("lostpointercapture", release);
    button.addEventListener("contextmenu", (event) => event.preventDefault());
  }
  window.addEventListener("blur", releaseKeys);
  document.addEventListener("visibilitychange", () => { if (document.hidden) releaseKeys(); });
  window.addEventListener("pagehide", () => {
    releaseKeys();
    if (state.manualId) fetch(`/api/robots/${encodeURIComponent(state.manualId)}/commands`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "manual", linear: 0, angular: 0 }), keepalive: true }).catch(() => {});
  });
  setInterval(() => { if (keys.size && canDrive()) sendManual(); }, 100);
  setInterval(() => {
    if (state.connected && state.lastMessage && performance.now() - state.lastMessage > 2200) { setConnection(false, "Нет свежих данных"); state.socket?.close(); }
  }, 500);
  new ResizeObserver(resize).observe($("canvasWrap"));
  function frame() { draw(); requestAnimationFrame(frame); }
  setConnection(false, "Подключение…");
  resize(); connect(); requestAnimationFrame(frame);
})();
