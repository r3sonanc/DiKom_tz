// L1: Строгий режим JavaScript: запрещает часть неявных и ошибочных операций.
"use strict";

// L3: Стрелочная функция вызывается сразу; её переменные скрыты от глобальной области.
(() => {
  // L4: const создаёт постоянную привязку; $ — короткая функция поиска HTML-элемента по id.
  const $ = (id) => document.getElementById(id);
  // L5: Находит canvas — HTML-полотно карты.
  const canvas = $("mapCanvas");
  // L6: Получает 2D-контекст: объект команд рисования на canvas.
  const ctx = canvas.getContext("2d");
  // L7: Массив цветов; каждому роботу назначается один цвет по его индексу.
  const colors = ["#2f76de", "#a270c8", "#e8953d", "#239d91", "#d76387"];
  // L8: Объект-словарь переводит коды состояний робота в подписи интерфейса.
  const stateLabels = { idle: "Ожидает приказ", moving: "В движении", paused: "Остановлен", stopped: "Остановлен", manual: "Ручное управление", error: "Ошибка", waiting: "Ожидает путь", parked: "На парковке", offline: "Нет связи", connecting: "Подключается" };
  // L9: Словарь переводит коды ошибок симулятора в понятные сообщения.
  const errorLabels = { STUCK: "Движение заблокировано", INVALID_COMMAND: "Некорректная команда", INVALID_PATH: "Некорректный маршрут", POINT_NOT_FOUND: "Узел не найден", OFF_GRAPH: "Робот вне маршрута", NOT_AT_NODE: "Робот вне начального узла", COLLISION_WALL: "Остановка перед стеной", COLLISION_ROBOT: "Остановка перед другим роботом" };
  // L10: Словарь названий трёх искусственных отказов.
  const faultLabels = { stuck: "Застревание", link_lost: "Потеря связи", throttle: "Замедление связи" };
  // L11: Состояние UI: снимок мира, роботы, выбор, соединение, следы, журнал и ручной режим.
  const state = { snapshot: null, robots: [], nodes: new Map(), selectedId: "", targetId: "", socket: null, connected: false, lastMessage: 0, trails: new Map(), events: [], seenEvents: new Set(), mapKey: "", manualId: null, manualChanging: false };
  // L12: Параметры обзора: масштаб, сдвиг, размер; dirty означает необходимость перерисовки.
  const view = { scale: 35, x: 0, y: 0, width: 1, height: 1, fitted: false, dirty: true };
  // L13: Set хранит нажатые клавиши без повторов.
  const keys = new Set();
  // L14: let разрешает менять значение; pointer хранит жест перетаскивания, null означает его отсутствие.
  let pointer = null;
  // L15: Идентификатор таймера повторного подключения к серверу.
  let reconnectTimer = null;
  // L16: Начальная задержка переподключения — 600 миллисекунд.
  let reconnectDelay = 600;
  // L17: Идентификатор таймера скрытия всплывающего сообщения.
  let toastTimer = null;
  // L18: Единственная ожидающая ручная команда: более новая заменяет старую.
  let manualPending = null;
  // L19: Promise активной отправки ручных команд; предотвращает параллельные очереди.
  let manualPump = null;
  // L20: Map связывает id робота с уже созданной строкой HTML-таблицы.
  const fleetRows = new Map();

  // L22: function объявляет функцию; textContent безопасно записывает значение как текст.
  function setText(id, value) { $(id).textContent = String(value); }
  // L23: Форматирует конечное число с digits знаками после запятой; иначе показывает прочерк.
  function number(value, digits = 2) { return value != null && Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—"; }
  // L24: find возвращает первого робота с выбранным id или undefined.
  function selectedRobot() { return state.robots.find((robot) => robot.id === state.selectedId); }
  // L25: findIndex и остаток % выбирают стабильный цвет из ограниченной палитры.
  function robotColor(id) { return colors[Math.max(0, state.robots.findIndex((robot) => robot.id === id)) % colors.length]; }
  // L26: Регулярное выражение превращает robot1 в «Робот 1»; $1 — захваченный номер.
  function robotName(id) { return String(id).replace(/^robot(\d+)$/i, "Робот $1"); }
  // L27: Принимает строку или объект ошибки и готовит русское сообщение.
  function errorText(error) {
    // L28: Если ошибки нет, немедленно возвращает пустой текст.
    if (!error) return "";
    // L29: typeof проверяет тип: строка используется прямо, у объекта берётся поле message.
    const message = typeof error === "string" ? error : error.message;
    // L30: Русское сообщение сохраняется; иначе используется перевод кода или общий текст ошибки.
    return message && /[а-яё]/i.test(message) ? message : errorLabels[error.code] || "Ошибка робота";
  // L31: Закрывает текущий блок в errorText.
  }
  // L32: Подготавливает сообщение журнала для показа человеку.
  function eventText(message) {
    // L33: Сообщение-объект форматируется как ошибка робота.
    if (message && typeof message === "object") return errorText(message);
    // L34: Регулярные выражения упрощают запись ошибки и заменяют robotN русским названием.
    return String(message).replace(/\{['"]code['"]:\s*['"]([^'"]+)['"],\s*['"]message['"]:\s*(['"])(.*?)\2\}/g, (_, code, quote, detail) => errorText({ code, message: detail })).replace(/\brobot(\d+)\b/gi, "Робот $1");
  // L35: Закрывает текущий блок в eventText.
  }
  // L36: Различает свежесть телеметрии и проверку связи; старые API без link_state остаются совместимыми.
  function robotLinkState(robot) { return robot.connected === false ? "OFFLINE" : robot.link_state || "ONLINE"; }
  // L37: Разрешённым для обычных команд считается только робот в ONLINE.
  function robotReady(robot) { return Boolean(robot && robotLinkState(robot) === "ONLINE"); }
  // L38: Возвращает причину, по которой весь парк ещё нельзя возобновить; пустая строка означает готовность UI.
  function fleetResumeBlocker() {
    // L39: Без WebSocket браузер не может оценить свежую готовность парка.
    if (!state.connected) return "Браузер не получает свежие данные сервера";
    // L40: До первой телеметрии общая команда недоступна.
    if (!state.robots.length) return "Ожидание телеметрии роботов";
    // L41: Определяет, требуется ли подтверждённая остановка перед общей командой.
    const stopped = state.snapshot?.dispatcher?.paused || state.snapshot?.dispatcher?.safety_stop;
    // L42: Проверяет каждого робота, а не только выбранного оператором.
    for (const robot of state.robots) {
      // L43: Начинает без причины блокировки для текущего робота.
      let reason = "";
      // L44: Недоступный робот блокирует возобновление всего парка.
      if (robotLinkState(robot) === "OFFLINE") reason = "Нет свежей телеметрии";
      // L45: Ошибка любого робота требует подтверждённого сброса.
      else if (robot.error) reason = "Перед возобновлением сбросьте ошибку";
      // L46: RECOVERING допускается только после серверной проверки recovery_ready.
      else if (!robotReady(robot) && !robot.recovery_ready) reason = robot.recovery_reason || "Ожидание проверки положения";
      // L47: При паузе нужны неподвижное состояние и обе скорости меньше 0,001.
      else if (stopped && (!["idle", "paused", "manual", "error"].includes(robot.state) || Math.abs(robot.v) >= .001 || Math.abs(robot.omega) >= .001)) reason = "Ожидание подтверждения остановки";
      // L48: Для выхода из ручного режима нужен узел; обычный остановленный переход может оставаться на ребре.
      else if (!robot.node_id && (!robot.edge || state.snapshot?.dispatcher?.manual_robot === robot.id)) reason = "Верните робота к навигационному узлу";
      // L49: Добавляет имя первого блокирующего робота к причине в подсказке кнопки.
      if (reason) return `${robotName(robot.id)}: ${reason}`;
    // L50: Завершает блок проверки готовности парка.
    }
    // L51: Пустая причина разрешает кнопку; сервер всё равно повторно проверит безопасность.
    return "";
  // L52: Завершает блок проверки готовности парка.
  }
  // L53: Поза недостоверна без WebSocket, при position_stale или незавершённом восстановлении.
  function positionUncertain(robot) { return !state.connected || Boolean(robot.position_stale) || !robotReady(robot); }
  // L54: Формирует подпись координат с явным указанием последней или неподтверждённой позиции.
  function positionText(robot) {
    // L55: До первой телеметрии показывает прочерк вместо выдуманного положения (0, 0).
    if (robot.last_seen == null && !robotReady(robot)) return "—";
    // L56: Для RECOVERING добавляет «не подтверждена», для устаревших данных — «последняя».
    const suffix = positionUncertain(robot) ? robotLinkState(robot) === "RECOVERING" ? " · не подтверждена" : " · последняя" : "";
    // L57: Соединяет отформатированные координаты и подпись их достоверности.
    return `${number(robot.x)} / ${number(robot.y)}${suffix}`;
  // L58: Завершает формирование подписи положения.
  }
  // L59: Выбирает причину недостоверности позиции для существующих подсказок полей.
  function positionReason(robot) {
    // L60: Отделяет отсутствие данных сервера от отказа канала конкретного робота.
    if (!state.connected) return "Браузер не получает свежие данные сервера";
    // L61: Использует причину восстановления из API либо поясняет, что позиция только последняя известная.
    return robot.recovery_reason || (positionUncertain(robot) ? "Последняя известная позиция; актуальное положение не подтверждено" : "");
  // L62: Завершает выбор причины недостоверности положения.
  }
  // L63: Формирует видимое состояние робота с учётом связи, ошибок и паузы.
  function robotStatus(robot) {
    // L64: Отсутствие свежей телеметрии имеет приоритет над старым рабочим состоянием.
    if (robotLinkState(robot) === "OFFLINE") return "Нет связи";
    // L65: Возврат сообщений показывает как восстановление, пока проверка не завершена.
    if (robotLinkState(robot) === "RECOVERING") return "Восстановление связи";
    // L66: Свежую ошибку или availability ERROR подписывает «Ошибка».
    if (robot.error || robot.availability === "ERROR") return "Ошибка";
    // L67: Операторскую или защитную паузу подписывает «Остановлен».
    if (robot.availability === "PAUSED") return "Остановлен";
    // L68: Для обычной работы сохраняет прежние подписи движения, ожидания и ручного режима.
    return stateLabels[robot.state] || "Ожидание";
  // L69: Завершает выбор подписи состояния робота.
  }
  // L70: Собирает подпись текущего приказа для карточки и таблицы.
  function orderText(robot) {
    // L71: Если приказа нет, возвращает соответствующую подпись.
    if (!robot.order) return "Нет активного приказа";
    // L72: Уже готовую строку приказа возвращает без преобразований.
    if (typeof robot.order === "string") return robot.order;
    // L73: Локальный словарь названий основных типов приказов.
    const types = { move: "Перемещение", park: "Парковка", manual: "Ручное управление", stop: "Остановка" };
    // L74: Шаблонная строка объединяет тип приказа, необязательную цель и идентификатор.
    return `${types[robot.order.type] || "Приказ"}${robot.order.target ? ` → ${robot.order.target}` : ""}${robot.order.id ? ` · ${robot.order.id}` : ""}`;
  // L75: Закрывает текущий блок в orderText.
  }
  // L76: Приводит поля робота из серверного снимка к форме, принятой в UI.
  function normalizeRobot(robot) {
    // L77: filter удаляет подряд повторяющиеся узлы ожидания из отображаемого маршрута.
    const route = (robot.route || []).filter((node, index, nodes) => index === 0 || node !== nodes[index - 1]);
    // L78: Оператор ... копирует поля; ?. и ?? дают запасные значения для отсутствующей телеметрии.
    return { ...robot, id: String(robot.id), connected: robot.connected !== false, x: robot.x ?? robot.pose?.x ?? 0, y: robot.y ?? robot.pose?.y ?? 0, theta: robot.theta ?? robot.pose?.yaw ?? 0, v: robot.v ?? robot.velocity?.linear ?? 0, omega: robot.omega ?? robot.velocity?.angular ?? 0, state: robot.state ?? robot.status ?? "idle", fault: robot.fault || {}, route };
  // L79: Закрывает текущий блок в normalizeRobot.
  }
  // L80: Показывает временное уведомление; isError по умолчанию false.
  function toast(message, isError = false) {
    // L81: Находит контейнер уведомления в HTML.
    const el = $("toast");
    // L82: Записывает отформатированное сообщение как безопасный текст.
    el.textContent = eventText(message);
    // L83: classList.toggle включает CSS-класс ошибки согласно isError.
    el.classList.toggle("error", isError);
    // L84: hidden=false делает уведомление видимым.
    el.hidden = false;
    // L85: Отменяет таймер предыдущего уведомления.
    clearTimeout(toastTimer);
    // L86: Скрывает ошибку через 6,5 секунды, обычное уведомление — через 3,5 секунды.
    toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6500 : 3500);
  // L87: Закрывает текущий блок в toast.
  }
  // L88: Добавляет событие; по умолчанию уровень info и текущее время ISO.
  function addEvent(message, level = "info", time = new Date().toISOString()) {
    // L89: push добавляет объект события в конец журнала браузера.
    state.events.push({ message: eventText(message), level, time });
    // L90: splice удаляет старые записи сверх лимита 80, ограничивая память.
    if (state.events.length > 80) state.events.splice(0, state.events.length - 80);
    // L91: DocumentFragment собирает новые HTML-элементы до вставки на страницу.
    const fragment = document.createDocumentFragment();
    // L92: slice берёт 35 последних событий, reverse показывает новые сверху.
    for (const entry of state.events.slice(-35).reverse()) {
      // L93: Создаёт один элемент списка для события.
      const row = document.createElement("li");
      // L94: Шаблонная строка назначает базовый класс и окраску warning/error.
      row.className = `event ${["warning", "error"].includes(entry.level) ? entry.level : ""}`;
      // L95: Создаёт HTML-элемент времени события.
      const timestamp = document.createElement("time");
      // L96: Date разбирает время из серверного сообщения.
      const date = new Date(entry.time);
      // L97: Корректную дату форматирует как русское время; при ошибке показывает исходную строку.
      timestamp.textContent = Number.isNaN(date.getTime()) ? String(entry.time) : date.toLocaleTimeString("ru-RU", { hour12: false });
      // L98: Создаёт отдельный элемент для текста события.
      const messageEl = document.createElement("span");
      // L99: textContent защищает текст события от интерпретации как HTML.
      messageEl.textContent = entry.message;
      // L100: Добавляет время и сообщение внутрь строки события.
      row.append(timestamp, messageEl);
      // L101: Добавляет готовую строку во временный фрагмент DOM.
      fragment.append(row);
    // L102: Завершает сборку DOM-строк последних событий.
    }
    // L103: Одной операцией заменяет видимые записи журнала собранным фрагментом.
    $("eventsList").replaceChildren(fragment);
  // L104: Закрывает текущий блок в addEvent.
  }

  // L106: async возвращает Promise; универсальная функция отправляет JSON-команду на сервер.
  async function request(path, payload, options = {}) {
    // L107: AbortController позволяет прервать зависший HTTP-запрос.
    const controller = new AbortController();
    // L108: Через 4500 мс вызывает abort, чтобы интерфейс не ждал бесконечно.
    const timeout = setTimeout(() => controller.abort(), 4500);
    // L109: try начинает блок обработки возможных сетевых ошибок.
    try {
      // L110: await ждёт fetch: POST отправляет JSON-тело, signal подключает отмену, options дополняет параметры.
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal, ...options });
      // L111: Разбирает JSON-ответ; при пустом или неверном JSON использует пустой объект.
      const result = await response.json().catch(() => ({}));
      // L112: Проверяет HTTP-статус и явный отказ приложения через result.ok.
      if (!response.ok || result.ok === false) {
        // L113: Извлекает строку ошибки или собирает сообщения валидации из массива detail.
        const detail = typeof result.detail === "string" ? result.detail : Array.isArray(result.detail) ? result.detail.map((issue) => issue.msg).filter(Boolean).join("; ") || "Проверьте параметры команды" : "Команда отклонена сервером";
        // L114: throw выбрасывает исключение, которое обработает вызывающий код.
        throw new Error(detail);
      // L115: Закрывает проверку неуспешного ответа; успешный ответ обрабатывается ниже.
      }
      // L116: Возвращает разобранный ответ при успешном запросе.
      return result;
    // L117: catch перехватывает ошибку запроса, разбора или ответа сервера.
    } catch (error) {
      // L118: Различает тайм-аут, сетевой сбой и обычную ошибку, создавая понятное сообщение.
      throw new Error(error.name === "AbortError" ? "Сервер не ответил вовремя" : error instanceof TypeError ? "Не удалось связаться с сервером" : eventText(error.message || "Ошибка соединения"));
    // L119: finally отменяет таймер независимо от результата запроса.
    } finally { clearTimeout(timeout); }
  // L120: Закрывает текущий блок в request.
  }
  // L121: Строит URL команды робота; encodeURIComponent безопасно кодирует id внутри адреса.
  function command(id, body, options) { return request(`/api/robots/${encodeURIComponent(id)}/commands`, body, options); }
  // L122: Обрабатывает операторскую команду для выбранного робота.
  async function operatorCommand(body, label) {
    // L123: Получает объект выбранного робота из состояния UI.
    const robot = selectedRobot();
    // L124: Проверяет выбранного робота для поездки, а весь парк — для общей команды resume.
    if (!robot || !state.connected || (["move", "park"].includes(body.type) && !robotReady(robot)) || (body.type === "resume" && fleetResumeBlocker())) return;
    // L125: Начинает защищённую отправку с обработкой отказа сервера.
    try {
      // L126: Перед stop/resume отпускает ручные клавиши и блокирует переключение режима.
      if (["stop", "resume"].includes(body.type)) { state.manualChanging = true; await releaseKeys(); }
      // L127: Ждёт принятия команды сервером; это ещё не означает прибытие робота.
      await command(robot.id, body);
      // L128: После stop/resume сбрасывает локальный ручной режим и сразу обновляет показанное состояние.
      if (["resume", "stop"].includes(body.type)) { state.manualId = null; robot.state = body.type === "stop" ? "paused" : "idle"; }
      // L129: Записывает факт принятой операторской команды в журнал.
      addEvent(`${robot.id}: ${label}`);
      // L130: Показывает уведомление об успешном принятии команды.
      toast(label);
    // L131: При отказе сервера показывает ошибку и сохраняет её в журнале.
    } catch (error) { toast(error.message, true); addEvent(`${robot.id}: ${error.message}`, "error"); }
    // L132: В любом исходе снимает блокировку переключения и обновляет карточку робота.
    finally { state.manualChanging = false; updateDetails(); }
  // L133: Закрывает текущий блок в operatorCommand.
  }

  // L135: Заполняет HTML-select вариантами items и выбирает current.
  function populateSelect(el, items, current, placeholder) {
    // L136: Собирает варианты списка вне живого DOM.
    const fragment = document.createDocumentFragment();
    // L137: При необходимости создаёт первый пустой вариант-подсказку.
    if (placeholder) { const option = document.createElement("option"); option.value = ""; option.textContent = placeholder; fragment.append(option); }
    // L138: Деструктуризация [value,label] получает код и подпись каждой option.
    for (const [value, label] of items) { const option = document.createElement("option"); option.value = value; option.textContent = label; fragment.append(option); }
    // L139: Заменяет весь набор вариантов готовым фрагментом.
    el.replaceChildren(fragment);
    // L140: Выбирает option с текущим значением.
    el.value = current;
  // L141: Закрывает текущий блок в populateSelect.
  }
  // L142: Переключает выбранного операторами робота.
  function selectRobot(id) {
    // L143: Повторный выбор того же робота не требует обновления.
    if (state.selectedId === id) return;
    // L144: Отпускает ручные клавиши перед переключением, чтобы не продолжить старую команду.
    releaseKeys();
    // L145: Сохраняет id нового выбранного робота.
    state.selectedId = id;
    // L146: Синхронизирует выпадающий список с выбором на карте или в таблице.
    $("robotSelect").value = id;
    // L147: Обновляет карточку и доступность кнопок управления.
    updateDetails();
    // L148: Обновляет выделение выбранного робота в таблице.
    updateFleet();
    // L149: Помечает canvas для перерисовки выделения.
    view.dirty = true;
  // L150: Закрывает текущий блок в selectRobot.
  }
  // L151: Обновляет значения карточки и разрешённые оператору действия.
  function updateDetails() {
    // L152: Получает выбранного робота или undefined.
    const robot = selectedRobot();
    // L153: Boolean превращает наличие робота и WebSocket-связи в разрешающий флаг.
    const usable = Boolean(robot && state.connected);
    // L154: Проверяет, сообщает ли сервер о ручном режиме выбранного робота.
    const remoteManual = robot?.state === "manual";
    // L155: Считает ручной режим активным по локальному выбору или серверному состоянию.
    const manual = Boolean(robot && (state.manualId === robot.id || remoteManual));
    // L156: Ручное управление блокируется, если сейчас вручную управляют другим роботом.
    const manualLocked = Boolean(state.manualId && state.manualId !== robot?.id);
    // L157: Показывает имя выбранного робота или приглашение выбрать его.
    setText("selectedRobotName", robot ? robotName(robot.id) : "Робот не выбран");
    // L158: Показывает актуальное состояние выбранного робота.
    setText("selectedRobotState", robot ? robotStatus(robot) : "Ожидание телеметрии");
    // L159: Из robotN делает короткую метку РN для значка карточки.
    setText("robotAvatar", robot?.id.replace(/^robot/i, "Р").slice(0, 4) || "Р");
    // L160: Окрашивает значок карточки в цвет выбранного робота.
    $("robotAvatar").style.color = robot ? robotColor(robot.id) : "";
    // L161: Причина ожидания доступна в подсказке существующего поля состояния.
    $("selectedRobotState").title = robot ? positionReason(robot) : "";
    // L162: Связь OFFLINE отмечает серым, RECOVERING — янтарным, свежую ошибку — красным.
    $("robotStateDot").className = `status-dot ${robot ? robotLinkState(robot) === "OFFLINE" ? "offline" : robotLinkState(robot) === "RECOVERING" ? "recovering" : robot.error ? "error" : "online" : ""}`;
    // L163: Выводит координаты вместе с указанием их достоверности.
    setText("positionValue", robot ? positionText(robot) : "—");
    // L164: Пунктирно подчёркивает последнюю или неподтверждённую позицию.
    $("positionValue").classList.toggle("position-stale", Boolean(robot && positionUncertain(robot)));
    // L165: Раскрывает причину недостоверности координат через title существующего поля.
    $("positionValue").title = robot ? positionReason(robot) : "";
    // L166: Переводит угол из радиан в градусы и нормализует к диапазону от 0 до 360.
    setText("headingValue", robot ? `${number(((robot.theta * 180 / Math.PI) % 360 + 360) % 360, 1)}°` : "—");
    // L167: Выводит линейную скорость из телеметрии.
    setText("speedValue", robot ? number(robot.v) : "—");
    // L168: Выводит угловую скорость из телеметрии.
    setText("angularValue", robot ? number(robot.omega) : "—");
    // L169: Показывает текущий приказ, цель и id в читаемом виде.
    setText("orderValue", robot ? orderText(robot) : "—");
    // L170: join соединяет узлы маршрута стрелками; при отсутствии маршрута даёт подпись.
    setText("routeValue", robot?.route.length ? robot.route.join(" → ") : "Маршрут не назначен");
    // L171: Скрывает блок ошибки, когда у робота нет ошибки.
    $("robotError").hidden = !robot?.error;
    // L172: Записывает понятное сообщение об ошибке робота.
    $("robotError").textContent = errorText(robot?.error);
    // L173: Синхронизирует переключатель ручного режима с текущим состоянием.
    $("manualEnabled").checked = manual;
    // L174: Запрещает переключать ручной режим без ONLINE или во время другого ручного управления.
    $("manualEnabled").disabled = !usable || state.manualChanging || manualLocked || !robotReady(robot);
    // L175: Кнопка поездки требует цели, автоматического режима и подтверждённого ONLINE.
    $("moveButton").disabled = !usable || !state.targetId || manual || !robotReady(robot);
    // L176: Парковка доступна только при подтверждённой связи и вне ручного режима.
    $("parkButton").disabled = !usable || manual || !robotReady(robot);
    // L177: Остановку можно нажать, когда выбран робот и доступен сервер.
    $("stopButton").disabled = !usable;
    // L178: Получает общую причину блокировки возобновления движения.
    const resumeBlocker = fleetResumeBlocker();
    // L179: Блокирует общую кнопку, если хотя бы один робот ещё не готов.
    $("resumeButton").disabled = !usable || Boolean(resumeBlocker);
    // L180: Показывает причину либо поясняет, что resume относится ко всему парку.
    $("resumeButton").title = resumeBlocker || "Возобновить весь парк; сервер повторно проверит безопасность";
    // L181: Сброс ошибки доступен только при наличии ошибки и соединения с сервером.
    $("clearErrorButton").disabled = !usable || !robot?.error;
    // L182: Ручные кнопки недоступны при потере или восстановлении связи и смене режима.
    for (const button of document.querySelectorAll(".key")) button.disabled = !usable || !manual || state.manualId !== robot?.id || !robotReady(robot) || state.manualChanging;
    // L183: Перебирает соответствия HTML-переключателей и серверных полей отказов.
    for (const [field, key] of [["faultStuck", "stuck"], ["faultLink", "link_lost"], ["faultThrottle", "throttle"]]) {
      // L184: Блокирует переключатель отказа при недоступном сервере или отсутствии выбора.
      $(field).disabled = !usable;
      // L185: Показывает текущее значение отказа выбранного робота.
      $(field).checked = Boolean(robot?.fault[key]);
    // L186: Завершает обновление трёх переключателей искусственных отказов.
    }
  // L187: Закрывает текущий блок в updateDetails.
  }
  // L188: Обновляет таблицу парка, повторно используя уже созданные строки.
  function updateFleet() {
    // L189: Удаляет DOM-строки и записи Map для исчезнувших из мира роботов.
    for (const [id, row] of fleetRows) if (!state.robots.some((robot) => robot.id === id)) { row.remove(); fleetRows.delete(id); }
    // L190: При первой загрузке очищает строку-заглушку таблицы.
    if (!fleetRows.size) $("fleetBody").replaceChildren();
    // L191: Перебирает каждого робота из последнего серверного снимка.
    for (const robot of state.robots) {
      // L192: Ищет существующую строку таблицы по id робота.
      let row = fleetRows.get(robot.id);
      // L193: Новую строку создаёт только при первом появлении робота.
      if (!row) {
        // L194: Создаёт HTML-строку таблицы tr.
        row = document.createElement("tr");
        // L195: tabIndex=0 позволяет выбрать строку клавиатурой через Tab.
        row.tabIndex = 0;
        // L196: aria-label даёт программе чтения с экрана понятное действие строки.
        row.setAttribute("aria-label", `Выбрать: ${robotName(robot.id)}`);
        // L197: Нажатие мышью выбирает робота из этой строки.
        row.addEventListener("click", () => selectRobot(robot.id));
        // L198: Enter или пробел выбирают строку с клавиатуры; preventDefault убирает прокрутку пробелом.
        row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRobot(robot.id); } });
        // L199: Создаёт шесть ячеек под поля робота.
        for (let index = 0; index < 6; index++) row.append(document.createElement("td"));
        // L200: Создаёт контейнер цветной метки и имени робота.
        const identityWrap = document.createElement("span"); identityWrap.className = "fleet-id";
        // L201: Создаёт маленький элемент цветовой метки.
        const mark = document.createElement("i"); mark.className = "fleet-color";
        // L202: Создаёт подпись робота как текстовое содержимое.
        const name = document.createElement("span"); name.textContent = robotName(robot.id);
        // L203: Собирает идентификатор робота и вставляет в первую ячейку.
        identityWrap.append(mark, name); row.cells[0].append(identityWrap);
        // L204: Создаёт значок состояния во второй ячейке.
        const badge = document.createElement("span"); row.cells[1].append(badge);
        // L205: Добавляет строку в DOM и сохраняет ссылку для последующих обновлений.
        $("fleetBody").append(row); fleetRows.set(robot.id, row);
      // L206: Завершает создание строки: дальше одинаково обновляются новые и существующие строки.
      }
      // L207: Назначает CSS-класс выделения текущему выбранному роботу.
      row.className = `fleet-row${robot.id === state.selectedId ? " selected" : ""}`;
      // L208: Обновляет цвет маркера робота в таблице.
      row.querySelector(".fleet-color").style.background = robotColor(robot.id);
      // L209: Находит ранее созданный значок состояния во второй ячейке.
      const badge = row.cells[1].firstElementChild;
      // L210: Выбирает разные бейджи OFFLINE, RECOVERING, ошибки, паузы и обычного движения.
      badge.className = `state-badge ${robotLinkState(robot) === "OFFLINE" ? "offline" : robotLinkState(robot) === "RECOVERING" ? "recovering" : robot.error ? "error" : robot.availability === "PAUSED" || robot.state === "paused" ? "paused" : robot.state === "moving" || robot.state === "manual" ? "moving" : robot.state === "idle" || robot.state === "parked" ? "idle" : ""}`;
      // L211: Обновляет текст состояния робота.
      badge.textContent = robotStatus(robot);
      // L212: К состоянию строки добавляет причину ожидания или недостоверности позиции.
      badge.title = positionReason(robot);
      // L213: В таблице также подписывает координаты как последние или неподтверждённые.
      row.cells[2].textContent = positionText(robot);
      // L214: Помечает недостоверные координаты тем же оформлением, что в карточке.
      row.cells[2].className = positionUncertain(robot) ? "position-stale" : "";
      // L215: Поясняет достоверность позиции в подсказке ячейки.
      row.cells[2].title = positionReason(robot);
      // L216: Записывает линейную скорость с единицей м/с в четвёртую ячейку.
      row.cells[3].textContent = `${number(robot.v)} м/с`;
      // L217: Пятая ячейка показывает приказ; title даёт сообщение ошибки при наведении.
      row.cells[4].textContent = orderText(robot); row.cells[4].title = errorText(robot.error);
      // L218: Колонка связи различает потерю канала и незавершённое восстановление.
      const link = row.cells[5]; link.className = `link-cell${robotLinkState(robot) === "OFFLINE" ? " lost" : robotLinkState(robot) === "RECOVERING" ? " recovering" : ""}`;
      // L219: Показывает отдельно потерю данных сервера, OFFLINE, RECOVERING, throttle и обычную свежесть.
      link.textContent = !state.connected ? "Нет данных сервера" : robotLinkState(robot) === "OFFLINE" ? `Нет связи · ${number(robot.last_seen, 1)} с` : robotLinkState(robot) === "RECOVERING" ? `Восстановление · ${number(robot.last_seen, 1)} с` : robot.fault.throttle ? `Замедление · ${number(robot.last_seen, 1)} с` : `В сети · ${number(robot.last_seen, 1)} с`;
      // L220: Дополняет колонку связи причиной ожидания из API.
      link.title = positionReason(robot);
    // L221: Завершает обновление строк всех роботов.
    }
    // L222: Обновляет общее количество роботов в заголовке таблицы.
    setText("fleetCount", `${state.robots.length} роботов`);
  // L223: Закрывает текущий блок в updateFleet.
  }
  // L224: Принимает очередной полный снимок мира, полученный через WebSocket.
  function acceptSnapshot(snapshot) {
    // L225: Отклоняет снимок без карты или массива роботов.
    if (!snapshot.map || !Array.isArray(snapshot.robots)) return;
    // L226: Запоминает прежний набор id, чтобы не пересоздавать список без необходимости.
    const priorIds = state.robots.map((robot) => robot.id).join("|");
    // L227: Сохраняет полный последний снимок сервера.
    state.snapshot = snapshot;
    // L228: map преобразует каждую запись робота к формату интерфейса.
    state.robots = snapshot.robots.map(normalizeRobot);
    // L229: Создаёт Map узлов по строковому id для быстрого доступа при рисовании.
    state.nodes = new Map((snapshot.map.nodes || []).map((node) => [String(node.id), node]));
    // L230: Фиксирует момент получения сообщения по монотонным часам браузера.
    state.lastMessage = performance.now();
    // L231: Строит ключ сцены из world_id, имени карты и структуры узлов.
    const mapKey = `${snapshot.world_id || ""}:${snapshot.map.name}:${JSON.stringify(snapshot.map.nodes)}`;
    // L232: При смене мира или карты сбрасывает связанное с прежней сценой состояние.
    if (mapKey !== state.mapKey) {
      // L233: Сохраняет новый ключ сцены.
      state.mapKey = mapKey;
      // L234: Снимает клавиши и ручной режим, отменяет отложенную ручную команду прежнего мира.
      keys.clear(); updateKeys(); manualPending = null; state.manualId = null;
      // L235: Выбирает название карты в настройках по серверному снимку.
      $("mapSelect").value = snapshot.map.name === "hardcore" ? "hardcore" : "normal";
      // L236: Синхронизирует поле числа роботов с реальным составом сцены.
      $("robotCount").value = state.robots.length;
      // L237: Очищает следы прошлой симуляции.
      state.trails.clear();
      // L238: Сбрасывает выбранную цель из старой карты.
      state.targetId = "";
      // L239: Заполняет список целей узлами карты и помечает парковки.
      populateSelect($("targetSelect"), Array.from(state.nodes.values(), (node) => [String(node.id), `${node.id}${node.parking ? " · парковка" : ""}`]), "", "Выберите узел");
      // L240: Подбирает масштаб и сдвиг для новой карты.
      fitMap();
    // L241: Завершает сброс локального состояния при смене мира или карты.
    }
    // L242: Строит ключ текущего состава роботов.
    const ids = state.robots.map((robot) => robot.id).join("|");
    // L243: Если прежний выбор исчез, выбирает первого доступного робота.
    if (!state.robots.some((robot) => robot.id === state.selectedId)) state.selectedId = state.robots[0]?.id || "";
    // L244: При изменении состава обновляет варианты выбора робота.
    if (ids !== priorIds) populateSelect($("robotSelect"), state.robots.map((robot) => [robot.id, robotName(robot.id)]), state.selectedId);
    // L245: Отменяет ручное управление, если управляемый робот исчез из сцены.
    if (state.manualId && !state.robots.some((robot) => robot.id === state.manualId)) { releaseKeys(); state.manualId = null; }
    // L246: Сбрасывает удерживаемые клавиши при потере связи, чтобы они не возобновили движение сами.
    if (state.manualId && !robotReady(state.robots.find((robot) => robot.id === state.manualId))) releaseKeys();
    // L247: Перебирает роботов для обновления следов их движения.
    for (const robot of state.robots) {
      // L248: Получает существующий след или создаёт пустой массив.
      const trail = state.trails.get(robot.id) || [];
      // L249: Берёт последнюю точку следа.
      const previous = trail[trail.length - 1];
      // L250: Добавляет точку только после смещения более 0,06 м, экономя память и рисование.
      if (!previous || Math.hypot(previous.x - robot.x, previous.y - robot.y) > .06) trail.push({ x: robot.x, y: robot.y });
      // L251: Оставляет не более 120 последних точек следа.
      if (trail.length > 120) trail.splice(0, trail.length - 120);
      // L252: Сохраняет след в Map под id робота.
      state.trails.set(robot.id, trail);
    // L253: Завершает обновление следов текущих роботов.
    }
    // L254: Удаляет следы исчезнувших роботов.
    for (const id of state.trails.keys()) if (!state.robots.some((robot) => robot.id === id)) state.trails.delete(id);
    // L255: Считает роботов со свежей телеметрией и показывает долю «в сети».
    setText("onlineMetric", `${state.robots.filter((robot) => robot.connected !== false).length}/${state.robots.length}`);
    // L256: Не считает устаревшую скорость недоступного робота подтверждением текущего движения.
    setText("movingMetric", state.robots.filter((robot) => robotReady(robot) && (Math.abs(robot.v) > .01 || Math.abs(robot.omega) > .01)).length);
    // L257: Получает серверные метрики или пустой объект при их отсутствии.
    const metrics = snapshot.metrics || {};
    // L258: Показывает число выполненных задач, поддерживая запасные имена поля.
    setText("completedMetric", metrics.completed_tasks ?? metrics.completed_orders ?? metrics.completed ?? 0);
    // L259: Считает роботов с ошибкой или без свежей связи.
    setText("errorMetric", state.robots.filter((robot) => robot.error || robot.connected === false).length);
    // L260: Синхронизирует переключатель «Тёмного цеха» с сервером.
    $("darkFactory").checked = Boolean(snapshot.dark_factory);
    // L261: Скрывает заглушку после первого пригодного снимка карты.
    $("emptyState").hidden = true;
    // L262: Выбирает русское имя карты.
    const mapLabel = snapshot.map.name === "hardcore" ? "Узкие коридоры" : "Обычный склад";
    // L263: В существующей подписи карты отражает как операторскую, так и защитную паузу.
    setText("mapCaption", `${mapLabel} · ${state.nodes.size} узлов · ${(snapshot.map.edges || []).length} рёбер${snapshot.dispatcher?.paused || snapshot.dispatcher?.safety_stop ? " · парк на паузе" : ""}`);
    // L264: Устанавливает допустимое число роботов для текущей карты.
    updateRobotCount();
    // L265: Пытается получить числовое время симуляции из снимка.
    const seconds = Number(snapshot.sim_time ?? snapshot.time ?? 0);
    // L266: Параллельно разбирает серверное время ISO как дату.
    const timestamp = new Date(snapshot.time);
    // L267: Показывает минуты/секунды либо часы из ISO-даты, если числового времени нет.
    setText("simulationTime", Number.isFinite(seconds) ? `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}` : !Number.isNaN(timestamp.getTime()) ? timestamp.toLocaleTimeString("ru-RU", { hour12: false }) : "—");
    // L268: Перебирает серверные события, которые уже могли приходить в предыдущих снимках.
    for (const entry of snapshot.events || []) {
      // L269: Составляет ключ события из времени, уровня и текста для удаления дублей.
      const key = `${entry.time}|${entry.level}|${entry.message}`;
      // L270: continue пропускает уже показанное событие.
      if (state.seenEvents.has(key)) continue;
      // L271: Запоминает ключ нового события.
      state.seenEvents.add(key);
      // L272: Ограничивает множество ключей 300 записями, удаляя самый старый.
      if (state.seenEvents.size > 300) state.seenEvents.delete(state.seenEvents.values().next().value);
      // L273: Добавляет новое серверное событие в журнал интерфейса.
      addEvent(entry.message, entry.level, entry.time);
    // L274: Завершает добавление новых серверных событий.
    }
    // L275: Обновляет карточку, таблицу и помечает карту для следующего кадра.
    updateDetails(); updateFleet(); view.dirty = true;
  // L276: Закрывает текущий блок в acceptSnapshot.
  }

  // L278: Меняет индикатор соединения браузера с HTTP/WebSocket-сервером.
  function setConnection(online, text) {
    // L279: Запоминает факт изменения состояния соединения.
    const changed = state.connected !== online;
    // L280: Обновляет локальный флаг WebSocket-связи браузера.
    state.connected = online;
    // L281: Переключает зелёный/красный класс кружка в заголовке.
    $("connectionDot").className = `status-dot ${online ? "online" : "error"}`;
    // L282: Пишет рядом с кружком «Подключено», «Переподключение…» или другое состояние.
    setText("connectionText", text);
    // L283: Управлять «Тёмным цехом» можно только при доступном сервере.
    $("darkFactory").disabled = !online;
    // L284: Создавать новую сцену можно только при доступном сервере.
    $("resetButton").disabled = !online;
    // L285: При разрыве связи отпускает ручные клавиши.
    if (!online) releaseKeys();
    // L286: Смена WebSocket-связи обновляет карточку, таблицу и достоверность отрисованной позиции.
    if (changed) { updateDetails(); if (state.snapshot) updateFleet(); view.dirty = true; }
  // L287: Закрывает текущий блок в setConnection.
  }
  // L288: Создаёт WebSocket-соединение и обработчики его событий.
  function connect() {
    // L289: Отменяет прежний таймер переподключения, если он был.
    clearTimeout(reconnectTimer);
    // L290: Создаёт /ws по текущему адресу; HTTPS требует защищённого протокола wss.
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
    // L291: Сохраняет сокет как единственное актуальное соединение UI.
    state.socket = socket;
    // L292: При открытии актуального сокета сбрасывает задержку и показывает «Подключено».
    socket.addEventListener("open", () => { if (state.socket !== socket) return; reconnectDelay = 600; setConnection(true, "Подключено"); });
    // L293: Подписывается на сообщения сервера со снимками мира.
    socket.addEventListener("message", (event) => {
      // L294: Игнорирует событие от уже заменённого старого сокета.
      if (state.socket !== socket) return;
      // L295: Разбирает JSON снимка, обновляет UI и подтверждает связь с сервером.
      try { const snapshot = JSON.parse(event.data); if (!snapshot.type || snapshot.type === "snapshot") { acceptSnapshot(snapshot); setConnection(true, "Подключено"); } }
      // L296: При невалидном JSON пишет ошибку в консоль разработчика.
      catch { console.error("Не удалось прочитать данные симуляции"); }
    // L297: Завершает обработчик входящего WebSocket-сообщения.
    });
    // L298: Ошибка сокета приводит к его закрытию и стандартному переподключению.
    socket.addEventListener("error", () => socket.close());
    // L299: Обработчик закрытия соединения запускает восстановление связи.
    socket.addEventListener("close", () => {
      // L300: Закрытие устаревшего сокета не влияет на новое соединение.
      if (state.socket !== socket) return;
      // L301: Показывает потерю соединения и блокирует зависимые действия.
      setConnection(false, "Переподключение…");
      // L302: Назначает вызов connect после текущей задержки.
      reconnectTimer = setTimeout(connect, reconnectDelay);
      // L303: Увеличивает задержку в 1,7 раза, ограничивая её шестью секундами.
      reconnectDelay = Math.min(6000, reconnectDelay * 1.7);
    // L304: Завершает обработчик закрытия WebSocket.
    });
  // L305: Закрывает текущий блок в connect.
  }

  // L307: Преобразует координаты мира в пиксели; экранная ось Y направлена вниз.
  function worldPoint(x, y) { return { x: view.x + x * view.scale, y: view.y - y * view.scale }; }
  // L308: Преобразует координаты указателя относительно окна в координаты внутри canvas.
  function screenPoint(event) { const box = canvas.getBoundingClientRect(); return { x: event.clientX - box.left, y: event.clientY - box.top }; }
  // L309: Подбирает масштаб и расположение так, чтобы вся карта была видна.
  function fitMap() {
    // L310: Без полученной карты подбор обзора не выполняется.
    if (!state.snapshot?.map) return;
    // L311: Деструктуризация читает размеры склада, задавая запасные 16×12 м.
    const { width = 16, height = 12 } = state.snapshot.map;
    // L312: Выбирает меньший масштаб, помещающий ширину и высоту с полями.
    view.scale = Math.min((view.width - 75) / width, (view.height - 90) / height);
    // L313: Ограничивает масштаб снизу пятью пикселями на метр.
    view.scale = Math.max(5, view.scale);
    // L314: Центрирует карту по горизонтали.
    view.x = (view.width - width * view.scale) / 2;
    // L315: Центрирует карту по вертикали с небольшим поправочным сдвигом.
    view.y = (view.height + height * view.scale) / 2 + 5;
    // L316: Помечает обзор настроенным и карту нуждающейся в перерисовке.
    view.fitted = true; view.dirty = true;
  // L317: Закрывает текущий блок в fitMap.
  }
  // L318: Изменяет масштаб вокруг заданного центра; по умолчанию это центр canvas.
  function zoom(factor, center = { x: view.width / 2, y: view.height / 2 }) {
    // L319: Ограничивает масштаб диапазоном от 10 до 160 пикселей на метр.
    const scale = Math.min(160, Math.max(10, view.scale * factor));
    // L320: Рассчитывает отношение нового масштаба к прежнему.
    const ratio = scale / view.scale;
    // L321: Смещает начало X так, чтобы выбранная точка масштабирования не сдвинулась.
    view.x = center.x - (center.x - view.x) * ratio;
    // L322: Аналогично сохраняет положение центра масштабирования по Y.
    view.y = center.y - (center.y - view.y) * ratio;
    // L323: Сохраняет масштаб и запрашивает перерисовку.
    view.scale = scale; view.dirty = true;
  // L324: Закрывает текущий блок в zoom.
  }
  // L325: Подгоняет размер canvas под контейнер и плотность пикселей экрана.
  function resize() {
    // L326: Читает реальные экранные размеры контейнера карты.
    const box = $("canvasWrap").getBoundingClientRect();
    // L327: Ограничивает devicePixelRatio двумя, уменьшая размер буфера на плотных экранах.
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    // L328: Сохраняет размеры области рисования в CSS-пикселях, минимум один пиксель.
    view.width = Math.max(1, box.width); view.height = Math.max(1, box.height);
    // L329: Задаёт размер растрового буфера canvas с учётом плотности пикселей.
    canvas.width = Math.round(view.width * ratio); canvas.height = Math.round(view.height * ratio);
    // L330: Масштабирует контекст, сохраняя удобные CSS-координаты при рисовании.
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    // L331: Снова вписывает карту в новый размер и помечает её изменённой.
    fitMap(); view.dirty = true;
  // L332: Закрывает текущий блок в resize.
  }
  // L333: Рисует отрезок между мировыми точками с выбранным цветом, толщиной и пунктиром.
  function line(from, to, color, width, dash = []) {
    // L334: Преобразует оба конца отрезка в экранные координаты.
    const a = worldPoint(from.x, from.y); const b = worldPoint(to.x, to.y);
    // L335: Создаёт путь, задаёт оформление, рисует отрезок и сбрасывает пунктир.
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.stroke(); ctx.setLineDash([]);
  // L336: Закрывает текущий блок в line.
  }
  // L337: Рисует текущую карту, следы и роботов на canvas.
  function draw() {
    // L338: Пропускает кадр, если данные и обзор не изменились.
    if (!view.dirty) return;
    // L339: Снимает признак dirty: текущие изменения сейчас будут нарисованы.
    view.dirty = false;
    // L340: Очищает прежнее изображение в пределах canvas.
    ctx.clearRect(0, 0, view.width, view.height);
    // L341: Получает карту из последнего снимка; ?. допускает отсутствие снимка.
    const map = state.snapshot?.map;
    // L342: Если карта ещё не пришла, после очистки рисовать нечего.
    if (!map) return;
    // L343: Находит экранные углы прямоугольника склада.
    const bottomLeft = worldPoint(0, 0); const topRight = worldPoint(map.width, map.height);
    // L344: Заливает фон внутренней области склада.
    ctx.fillStyle = "#f0f4f9"; ctx.fillRect(bottomLeft.x, topRight.y, map.width * view.scale, map.height * view.scale);
    // L345: Рисует вертикальные линии метровой сетки.
    for (let x = 1; x < map.width; x++) line({ x, y: 0 }, { x, y: map.height }, "#e5ebf3", .7);
    // L346: Рисует горизонтальные линии метровой сетки.
    for (let y = 1; y < map.height; y++) line({ x: 0, y }, { x: map.width, y }, "#e5ebf3", .7);
    // L347: Приводит рёбра из массива или объекта к паре id узлов.
    const edges = (map.edges || []).map((edge) => Array.isArray(edge) ? edge : [edge.from, edge.to]);
    // L348: Перебирает рёбра графа, распаковывая начало и конец.
    for (const [from, to] of edges) {
      // L349: Находит координаты обоих узлов по их id.
      const a = state.nodes.get(String(from)); const b = state.nodes.get(String(to));
      // L350: Рисует широкую дорожку и пунктирную ось существующего ребра.
      if (a && b) { line(a, b, "#fff", Math.max(8, view.scale * .68)); line(a, b, "#becbdd", 1.25, [4, 4]); }
    // L351: Завершает отрисовку рёбер навигационного графа.
    }
    // L352: Перебирает физические стены склада.
    for (const wall of map.walls || []) {
      // L353: Распаковывает координаты стены из массива либо объекта.
      const [x1, y1, x2, y2] = Array.isArray(wall) ? wall : [wall.x1, wall.y1, wall.x2, wall.y2];
      // L354: Рисует широкую подложку стены.
      line({ x: x1, y: y1 }, { x: x2, y: y2 }, "#bdcad9", 7);
      // L355: Рисует узкую тёмную линию стены поверх подложки.
      line({ x: x1, y: y1 }, { x: x2, y: y2 }, "#778aa2", 2);
    // L356: Завершает отрисовку физических стен.
    }
    // L357: Перебирает роботов для рисования следов, маршрутов и блокировок.
    for (const robot of state.robots) {
      // L358: Получает сохранённые точки следа данного робота.
      const trail = state.trails.get(robot.id) || [];
      // L359: След имеет отрезок только при наличии хотя бы двух точек.
      if (trail.length > 1) {
        // L360: Устанавливает прозрачность для ненавязчивого отображения следа.
        ctx.globalAlpha = .15;
        // L361: Соединяет соседние точки следа линиями цвета робота.
        for (let index = 1; index < trail.length; index++) line(trail[index - 1], trail[index], robotColor(robot.id), 2);
        // L362: Восстанавливает непрозрачность последующих фигур.
        ctx.globalAlpha = 1;
      // L363: Завершает отрисовку непустого следа робота.
      }
      // L364: Показывает маршрут только выбранного робота и только при нескольких узлах.
      if (robot.id === state.selectedId && robot.route.length > 1) {
        // L365: Перебирает соседние пары узлов отображаемого маршрута.
        for (let index = 1; index < robot.route.length; index++) {
          // L366: Находит координаты начала и конца очередного сегмента маршрута.
          const a = state.nodes.get(String(robot.route[index - 1])); const b = state.nodes.get(String(robot.route[index]));
          // L367: Рисует сегмент маршрута полупрозрачным синим цветом.
          if (a && b) line(a, b, "#4b88e88a", 5);
        // L368: Завершает перебор сегментов маршрута.
        }
      // L369: Завершает отрисовку маршрута выбранного робота.
      }
      // L370: У недоступного или восстанавливающегося робота выделяет последний известный заблокированный участок.
      if (robot.error || robot.fault.stuck || !robotReady(robot)) {
        // L371: Получает текущее занятое ребро из телеметрии.
        const edge = robot.edge;
        // L372: Приводит ребро к массиву двух id, учитывая отсутствие ребра.
        const ends = Array.isArray(edge) ? edge : edge ? [edge.from, edge.to] : [];
        // L373: Пунктирную блокировку можно нарисовать при двух известных концах ребра.
        if (ends.length === 2) {
          // L374: Ищет оба узла заблокированного ребра.
          const a = state.nodes.get(String(ends[0])); const b = state.nodes.get(String(ends[1]));
          // L375: Рисует красный пунктир на заблокированном ребре.
          if (a && b) line(a, b, "#d9626d", 5, [7, 4]);
        // L376: Завершает выделение занятого ребра.
        }
        // L377: Находит занятый узел робота, если тот стоит точно на узле.
        const node = state.nodes.get(String(robot.node_id));
        // L378: Обводит заблокированный узел красным кольцом.
        if (node) { const p = worldPoint(node.x, node.y); ctx.beginPath(); ctx.arc(p.x, p.y, 12, 0, Math.PI * 2); ctx.strokeStyle = "#da6f79"; ctx.lineWidth = 2; ctx.stroke(); }
      // L379: Завершает выделение блокировок неисправного или недоступного робота.
      }
    // L380: Завершает перебор роботов для следов, маршрутов и блокировок.
    }
    // L381: Перебирает все навигационные узлы карты.
    for (const node of state.nodes.values()) {
      // L382: Вычисляет экранную позицию узла.
      const p = worldPoint(node.x, node.y);
      // L383: Проверяет, выбран ли данный узел в качестве операторской цели.
      const target = String(node.id) === state.targetId;
      // L384: Выделяет выбранную цель голубым кольцом.
      if (target) { ctx.beginPath(); ctx.arc(p.x, p.y, 13, 0, Math.PI * 2); ctx.fillStyle = "#347be31c"; ctx.fill(); ctx.strokeStyle = "#4d89e9"; ctx.lineWidth = 1.4; ctx.stroke(); }
      // L385: Парковки рисуются отдельно от обычных навигационных узлов.
      if (node.parking) {
        // L386: Рисует светлый квадрат парковки.
        ctx.fillStyle = "#e4eef5"; ctx.fillRect(p.x - 9, p.y - 9, 18, 18);
        // L387: Обводит квадрат парковки.
        ctx.strokeStyle = "#a7bcd0"; ctx.lineWidth = 1; ctx.strokeRect(p.x - 9, p.y - 9, 18, 18);
        // L388: Пишет букву «П» по центру парковки.
        ctx.font = "bold 14px Segoe UI, sans-serif"; ctx.fillStyle = "#5c7f9b"; ctx.textAlign = "center"; ctx.fillText("П", p.x, p.y + 5);
      // L389: Обычный узел рисует белым кругом, меняя обводку выбранной цели.
      } else { ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill(); ctx.strokeStyle = target ? "#4c84db" : "#9cafc7"; ctx.lineWidth = 1.5; ctx.stroke(); }
      // L390: Подписывает id каждого узла, учитывая дополнительную высоту парковочного значка.
      ctx.font = "13px Segoe UI, sans-serif"; ctx.textAlign = "center"; ctx.fillStyle = target ? "#3671c9" : "#73859c"; ctx.fillText(String(node.id), p.x, p.y + (node.parking ? 27 : 24));
    // L391: Завершает отрисовку всех навигационных узлов.
    }
    // L392: Рисует корпус и подпись каждого робота поверх карты.
    for (const robot of state.robots) drawRobot(robot);
    // L393: Выбирает целое число метров для экранной масштабной линейки.
    const meter = Math.max(1, Math.floor(65 / view.scale));
    // L394: Переводит выбранную длину линейки в пиксели.
    const barWidth = meter * view.scale;
    // L395: Рисует горизонтальную масштабную линейку с засечками.
    ctx.strokeStyle = "#91a2b6"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(20, view.height - 31); ctx.lineTo(20, view.height - 26); ctx.lineTo(20 + barWidth, view.height - 26); ctx.lineTo(20 + barWidth, view.height - 31); ctx.stroke();
    // L396: Подписывает физическую длину линейки в метрах.
    ctx.fillStyle = "#75869c"; ctx.textAlign = "center"; ctx.font = "13px Segoe UI, sans-serif"; ctx.fillText(`${meter} м`, 20 + barWidth / 2, view.height - 10);
  // L397: Закрывает текущий блок в draw.
  }
  // L398: Рисует одного робота по координатам и углу из телеметрии.
  function drawRobot(robot) {
    // L399: До первой подтверждённой телеметрии не рисует робота в координатах по умолчанию.
    if (robot.last_seen == null && !robotReady(robot)) return;
    // L400: Переводит положение робота из метров в экранные пиксели.
    const p = worldPoint(robot.x, robot.y);
    // L401: Берёт текущий масштаб пикселей на метр.
    const scale = view.scale;
    // L402: Выбирает цвет по id робота.
    const color = robotColor(robot.id);
    // L403: Проверяет, выбран ли робот оператором.
    const selected = robot.id === state.selectedId;
    // L404: Запоминает, что нарисованная позиция может быть устаревшей или неподтверждённой.
    const uncertain = positionUncertain(robot);
    // L405: Отдельно определяет восстановление для янтарного индикатора.
    const recovering = robotLinkState(robot) === "RECOVERING";
    // L406: Выделяет выбранного робота полупрозрачным кругом.
    if (selected) { ctx.beginPath(); ctx.arc(p.x, p.y, scale * .72 + 3, 0, Math.PI * 2); ctx.fillStyle = `${color}13`; ctx.fill(); ctx.strokeStyle = `${color}65`; ctx.lineWidth = 1.3; ctx.stroke(); }
    // L407: Сохраняет настройки контекста, переносит начало в центр робота и учитывает угол.
    ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(-robot.theta);
    // L408: Неподтверждённый корпус рисуется полупрозрачным; restore ниже возвращает непрозрачность.
    if (uncertain) ctx.globalAlpha = .5;
    // L409: Задаёт лёгкую тень корпуса.
    ctx.shadowColor = "#183b6a18"; ctx.shadowBlur = 5; ctx.shadowOffsetY = 2;
    // L410: Задаёт тёмный цвет для колёс.
    ctx.fillStyle = "#31435a";
    // L411: Рисует одно колесо относительно центра робота.
    ctx.fillRect(-.24 * scale, -.39 * scale, .48 * scale, .11 * scale);
    // L412: Рисует второе колесо на противоположной стороне.
    ctx.fillRect(-.24 * scale, .28 * scale, .48 * scale, .11 * scale);
    // L413: Устаревший корпус серый, подтверждённый сохраняет цвет конкретного робота.
    ctx.fillStyle = uncertain ? "#9aa8b8" : color;
    // L414: Рисует корпус 0,8×0,6 м как скруглённый прямоугольник.
    ctx.beginPath(); ctx.roundRect(-.4 * scale, -.3 * scale, .8 * scale, .6 * scale, Math.min(5, scale * .1)); ctx.fill();
    // L415: Отключает тень для деталей корпуса.
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    // L416: Обводит корпус тонкой полупрозрачной линией.
    ctx.strokeStyle = "#ffffff55"; ctx.lineWidth = 1; ctx.stroke();
    // L417: Рисует белую стрелку направления передней части робота.
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.moveTo(.31 * scale, 0); ctx.lineTo(.08 * scale, -.12 * scale); ctx.lineTo(.08 * scale, .12 * scale); ctx.closePath(); ctx.fill();
    // L418: Рисует светлую внутреннюю деталь корпуса.
    ctx.fillStyle = "#ffffff45"; ctx.fillRect(-.27 * scale, -.14 * scale, .16 * scale, .28 * scale);
    // L419: Восстанавливает контекст, отменяя локальное смещение и поворот.
    ctx.restore();
    // L420: Пунктирная окружность отличает последнюю позу; янтарный цвет означает проверку восстановления.
    if (uncertain) { ctx.beginPath(); ctx.arc(p.x, p.y, scale * .64 + 3, 0, Math.PI * 2); ctx.strokeStyle = recovering ? "#b7832d" : "#8996a8"; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]); ctx.stroke(); ctx.setLineDash([]); }
    // L421: Получает подпись робота и задаёт шрифт.
    const label = robotName(robot.id); ctx.font = "600 14px Segoe UI, sans-serif";
    // L422: Измеряет ширину имени для размера подложки.
    const textWidth = ctx.measureText(label).width;
    // L423: Рисует светлую подложку имени над роботом.
    ctx.fillStyle = "#ffffffed"; ctx.beginPath(); ctx.roundRect(p.x - textWidth / 2 - 7, p.y - scale * .7 - 25, textWidth + 14, 23, 4); ctx.fill();
    // L424: Подпись последней позиции приглушается, а свежая ошибка остаётся красной.
    ctx.textAlign = "center"; ctx.fillStyle = uncertain ? recovering ? "#9c702a" : "#7b899a" : robot.error ? "#c25b65" : color; ctx.fillText(label, p.x, p.y - scale * .7 - 8);
    // L425: Знак вопроса отмечает недостоверную позу, восклицание — свежую ошибку робота.
    if (robot.error || uncertain) { ctx.beginPath(); ctx.arc(p.x + scale * .42, p.y - scale * .33, 7, 0, Math.PI * 2); ctx.fillStyle = uncertain ? recovering ? "#b7832d" : "#8996a8" : "#e56a72"; ctx.fill(); ctx.fillStyle = "#fff"; ctx.font = "bold 12px Segoe UI"; ctx.fillText(uncertain ? "?" : "!", p.x + scale * .42, p.y - scale * .33 + 4); }
  // L426: Закрывает текущий блок в drawRobot.
  }

  // L428: Обрабатывает короткое нажатие на карту, выбирая робота или ближайший узел.
  function clickMap(point) {
    // L429: Ищет робота, чей экранный корпус находится достаточно близко к нажатию.
    let hit = state.robots.find((robot) => { const p = worldPoint(robot.x, robot.y); return Math.hypot(p.x - point.x, p.y - point.y) < Math.max(15, view.scale * .6); });
    // L430: Если найден робот, выбирает его и не ищет узел под ним.
    if (hit) { selectRobot(hit.id); return; }
    // L431: Ограничивает поиск узла радиусом 18 экранных пикселей.
    let distance = 18;
    // L432: Перебирает навигационные узлы для выбора ближайшего к нажатию.
    for (const node of state.nodes.values()) {
      // L433: Вычисляет экранные координаты узла и расстояние до указателя.
      const p = worldPoint(node.x, node.y); const current = Math.hypot(p.x - point.x, p.y - point.y);
      // L434: Сохраняет ближайший узел внутри допустимого радиуса.
      if (current < distance) { hit = node; distance = current; }
    // L435: Завершает поиск ближайшего к нажатию навигационного узла.
    }
    // L436: Выбирает найденный узел целью, обновляет список, кнопки и выделение карты.
    if (hit) { state.targetId = String(hit.id); $("targetSelect").value = state.targetId; updateDetails(); view.dirty = true; }
  // L437: Закрывает текущий блок в clickMap.
  }
  // L438: Колесо меняет масштаб относительно указателя; passive:false разрешает отменить прокрутку страницы.
  canvas.addEventListener("wheel", (event) => { event.preventDefault(); zoom(Math.exp(-event.deltaY * .0015), screenPoint(event)); }, { passive: false });
  // L439: Левая кнопка начинает жест и захватывает указатель, чтобы не терять события вне canvas.
  canvas.addEventListener("pointerdown", (event) => { if (event.button !== 0) return; const p = screenPoint(event); pointer = { id: event.pointerId, startX: p.x, startY: p.y, x: p.x, y: p.y, moved: false }; canvas.setPointerCapture(event.pointerId); });
  // L440: Смещение больше 4 px считается перетаскиванием; двигает обзор вслед за указателем.
  canvas.addEventListener("pointermove", (event) => { if (!pointer || pointer.id !== event.pointerId) return; const p = screenPoint(event); if (Math.hypot(p.x - pointer.startX, p.y - pointer.startY) > 4) pointer.moved = true; if (pointer.moved) { view.x += p.x - pointer.x; view.y += p.y - pointer.y; view.dirty = true; } pointer.x = p.x; pointer.y = p.y; });
  // L441: По отпусканию без перетаскивания выбирает объект карты и завершает жест.
  canvas.addEventListener("pointerup", (event) => { if (!pointer || pointer.id !== event.pointerId) return; if (!pointer.moved) clickMap(screenPoint(event)); pointer = null; });
  // L442: Отменённый браузером жест сбрасывает сохранённый указатель.
  canvas.addEventListener("pointercancel", () => { pointer = null; });
  // L443: Кнопка «плюс» увеличивает масштаб в 1,25 раза.
  $("zoomIn").addEventListener("click", () => zoom(1.25));
  // L444: Кнопка «минус» уменьшает масштаб до 80% прежнего.
  $("zoomOut").addEventListener("click", () => zoom(.8));
  // L445: Кнопка «Вся карта» снова вписывает склад в область просмотра.
  $("fitMap").addEventListener("click", fitMap);
  // L446: Изменение списка роботов вызывает общий обработчик выбора.
  $("robotSelect").addEventListener("change", (event) => selectRobot(event.target.value));
  // L447: Выбор цели в списке обновляет состояние управления и выделение canvas.
  $("targetSelect").addEventListener("change", (event) => { state.targetId = event.target.value; updateDetails(); view.dirty = true; });
  // L448: Кнопка «Отправить» назначает move к выбранному узлу через operatorCommand.
  $("moveButton").addEventListener("click", () => operatorCommand({ type: "move", target: state.targetId }, `Назначено перемещение → ${state.targetId}`));
  // L449: Кнопка парковки отправляет park без явной цели: парковку выбирает диспетчер.
  $("parkButton").addEventListener("click", () => operatorCommand({ type: "park" }, "Назначена парковка"));
  // L450: Кнопка остановки отправляет stop, который на сервере ставит весь парк на паузу.
  $("stopButton").addEventListener("click", () => operatorCommand({ type: "stop" }, "Парк остановлен"));
  // L451: Кнопка возобновления отправляет resume выбранному роботу.
  $("resumeButton").addEventListener("click", () => operatorCommand({ type: "resume" }, "Движение возобновлено"));
  // L452: Кнопка сброса ошибки отправляет clear_error.
  $("clearErrorButton").addEventListener("click", () => operatorCommand({ type: "clear_error" }, "Ошибка сброшена"));
  // L453: Очищает локальный журнал UI, затем добавляет запись об очистке.
  $("clearEvents").addEventListener("click", () => { state.events.length = 0; $("eventsList").replaceChildren(); addEvent("Журнал интерфейса очищен"); });
  // L454: Ограничивает количество роботов числом парковок: три на normal, пять на hardcore.
  function updateRobotCount() { const max = $("mapSelect").value === "hardcore" ? 5 : 3; $("robotCount").max = max; if (Number($("robotCount").value) > max) $("robotCount").value = max; }
  // L455: Переключение карты немедленно обновляет предел числа роботов.
  $("mapSelect").addEventListener("change", updateRobotCount);
  // L456: Кнопка новой симуляции запускает асинхронный сброс мира на сервере.
  $("resetButton").addEventListener("click", async () => {
    // L457: Преобразует введённое количество и допустимый максимум в числа.
    const count = Number($("robotCount").value); const max = Number($("robotCount").max);
    // L458: Отклоняет дробное число или количество вне диапазона карты до отправки запроса.
    if (!Number.isInteger(count) || count < 1 || count > max) { toast(`Укажите число роботов от 1 до ${max}`, true); return; }
    // L459: Блокирует ручное переключение и дожидается освобождения WASD.
    state.manualChanging = true; await releaseKeys();
    // L460: Блокирует повторное нажатие кнопки сброса на время запроса.
    $("resetButton").disabled = true;
    // L461: Отправляет новую карту и число роботов, очищает следы и уведомляет оператора.
    try { await request("/api/reset", { map: $("mapSelect").value, robots: count }); state.trails.clear(); state.manualId = null; toast("Создана новая симуляция"); addEvent("Симуляция перезапущена"); }
    // L462: Показывает сообщение при ошибке пересоздания мира.
    catch (error) { toast(error.message, true); }
    // L463: В любом исходе восстанавливает доступность управления по текущему соединению.
    finally { state.manualChanging = false; $("resetButton").disabled = !state.connected; updateDetails(); }
  // L464: Завершает обработчик кнопки пересоздания симуляции.
  });
  // L465: Переключатель «Тёмного цеха» запускает запрос изменения режима.
  $("darkFactory").addEventListener("change", async (event) => {
    // L466: Читает новое логическое значение checkbox.
    const enabled = event.target.checked;
    // L467: Отправляет enabled серверу и записывает изменение режима в локальный журнал.
    try { await request("/api/dark-factory", { enabled }); addEvent(`Тёмный цех ${enabled ? "включён" : "выключен"}`); }
    // L468: При отказе возвращает прежнее положение переключателя и показывает ошибку.
    catch (error) { event.target.checked = !enabled; toast(error.message, true); }
  // L469: Завершает обработчик переключателя «Тёмного цеха».
  });
  // L470: Связывает id трёх переключателей отказов с полями серверного JSON.
  for (const [id, field] of [["faultStuck", "stuck"], ["faultLink", "link_lost"], ["faultThrottle", "throttle"]]) {
    // L471: Для каждого переключателя регистрирует обработчик change.
    $(id).addEventListener("change", async (event) => {
      // L472: Получает выбранного робота; без него имитировать отказ некого.
      const robot = selectedRobot(); if (!robot) return;
      // L473: Читает, включил или выключил оператор выбранный отказ.
      const enabled = event.target.checked;
      // L474: Отправляет {имя_отказа: значение} выбранному роботу и пишет событие.
      try { await request(`/api/robots/${encodeURIComponent(robot.id)}/faults`, { [field]: enabled }); addEvent(`${robot.id}: ${faultLabels[field].toLowerCase()} ${enabled ? "— включено" : "— выключено"}`, enabled ? "warning" : "info"); }
      // L475: При ошибке запроса восстанавливает checkbox и показывает причину.
      catch (error) { event.target.checked = !enabled; toast(error.message, true); }
    // L476: Завершает обработчик изменения одного искусственного отказа.
    });
  // L477: Завершает регистрацию обработчиков трёх отказов.
  }

  // L479: Подсвечивает экранные WASD-кнопки согласно множеству нажатых клавиш.
  function updateKeys() { for (const button of document.querySelectorAll(".key")) button.classList.toggle("pressed", keys.has(button.dataset.key)); }
  // L480: W−S задаёт ±0,5 м/с; A−D задаёт ±1,25 рад/с; противоположные клавиши взаимно гасятся.
  function manualVelocity() { return { linear: (Number(keys.has("w")) - Number(keys.has("s"))) * .5, angular: (Number(keys.has("a")) - Number(keys.has("d"))) * 1.25 }; }
  // L481: Отправляет последнее ручное управление; force позволяет отправить нулевую остановку.
  function sendManual(force = false) {
    // L482: Получает id робота, которым управляют вручную.
    const id = state.manualId;
    // L483: Обычные ручные команды требуют ONLINE; принудительная нулевая команда остаётся доступна для отпускания клавиш.
    if (!id || (!force && (!state.connected || !keys.size || !robotReady(state.robots.find((robot) => robot.id === id))))) return manualPump;
    // L484: Сохраняет только самую новую ручную скорость вместо очереди старых движений.
    manualPending = { id, velocity: manualVelocity() };
    // L485: При активном отправителе не запускает второй параллельный цикл.
    if (manualPump) return manualPump;
    // L486: Создаёт асинхронный цикл отправки ручных команд.
    manualPump = (async () => {
      // L487: Повторяет отправку, пока есть более свежая ожидающая команда.
      while (manualPending) {
        // L488: Забирает ожидающую команду и освобождает место для нового состояния клавиш.
        const next = manualPending; manualPending = null;
        // L489: Отправляет тип manual и поля скоростей из объекта velocity.
        try { await command(next.id, { type: "manual", ...next.velocity }); }
        // L490: При сетевой ошибке очищает очередь и клавиши, снимает подсветку и показывает сообщение.
        catch (error) { manualPending = null; keys.clear(); updateKeys(); toast(error.message, true); }
      // L491: Завершает цикл отправки самых свежих ручных команд.
      }
    // L492: После завершения цикла очищает ссылку на активный отправитель.
    })().finally(() => { manualPump = null; });
    // L493: Возвращает Promise: вызывающий код может дождаться освобождения управления.
    return manualPump;
  // L494: Закрывает текущий блок в sendManual.
  }
  // L495: Отпускает все ручные клавиши и при необходимости отправляет остановку.
  function releaseKeys() {
    // L496: Запоминает, были ли нажаты какие-либо клавиши.
    const hadKeys = keys.size > 0;
    // L497: Очищает Set клавиш и обновляет экранную подсветку.
    keys.clear(); updateKeys();
    // L498: Если движение или отправка были активны, отправляет принудительную нулевую скорость.
    if (hadKeys || manualPump) return sendManual(true);
    // L499: Иначе возвращает уже завершённый Promise для единообразного await.
    return Promise.resolve();
  // L500: Закрывает текущий блок в releaseKeys.
  }
  // L501: Обработчик checkbox включает или завершает ручной режим робота.
  $("manualEnabled").addEventListener("change", async (event) => {
    // L502: Без выбранного робота переключение не выполняется.
    const robot = selectedRobot(); if (!robot) return;
    // L503: Получает желаемое состояние ручного режима из checkbox.
    const enabled = event.target.checked;
    // L504: Блокирует управление на время переключения и обновляет карточку.
    state.manualChanging = true; updateDetails();
    // L505: Начинает блок обработки возможного отказа сервера.
    try {
      // L506: Перед переключением гарантирует освобождение локальных клавиш.
      await releaseKeys();
      // L507: Для включения отправляет manual с нулевыми скоростями; для выхода — resume.
      await command(robot.id, enabled ? { type: "manual", linear: 0, angular: 0 } : { type: "resume" });
      // L508: Запоминает владельца ручного управления либо очищает его.
      state.manualId = enabled ? robot.id : null;
      // L509: Сразу обновляет локальное состояние робота до следующего серверного снимка.
      robot.state = enabled ? "manual" : "idle";
      // L510: Добавляет понятное сообщение о включении или выключении режима.
      addEvent(`${robot.id}: ручное управление ${enabled ? "включено, остальной парк на паузе" : "выключено"}`);
    // L511: Показывает ошибку переключения режима.
    } catch (error) { toast(error.message, true); }
    // L512: Всегда снимает блокировку переключения и обновляет доступность кнопок.
    finally { state.manualChanging = false; updateDetails(); }
  // L513: Завершает обработчик переключения ручного режима.
  });
  // L514: Клавиатурный привод требует свежего WebSocket, выбранного manual-робота и подтверждённого ONLINE.
  function canDrive() { return Boolean(state.connected && state.manualId && state.manualId === state.selectedId && !state.manualChanging && robotReady(selectedRobot())); }
  // L515: Регистрирует обработчик нажатия физической клавиши.
  document.addEventListener("keydown", (event) => {
    // L516: event.code распознаёт физические WASD независимо от раскладки клавиатуры.
    const key = { KeyW: "w", KeyA: "a", KeyS: "s", KeyD: "d" }[event.code];
    // L517: Escape немедленно отпускает ручные клавиши.
    if (event.code === "Escape") { releaseKeys(); return; }
    // L518: Определяет поля ввода, в которых WASD должны вводить текст, а не управлять роботом.
    const textField = ["SELECT", "TEXTAREA"].includes(event.target.tagName) || (event.target.tagName === "INPUT" && !["checkbox", "button"].includes(event.target.type));
    // L519: Игнорирует неподходящую клавишу, заблокированное управление и редактируемые элементы.
    if (!key || !canDrive() || textField || event.target.isContentEditable) return;
    // L520: Отменяет обычное действие клавиши браузера при управлении роботом.
    event.preventDefault();
    // L521: Первое нажатие добавляет клавишу в Set и отправляет скорость; автоповторы не дублируются.
    if (!keys.has(key)) { keys.add(key); updateKeys(); sendManual(); }
  // L522: Завершает обработчик нажатия физической клавиши.
  });
  // L523: Регистрирует обработчик отпускания физической клавиши.
  document.addEventListener("keyup", (event) => {
    // L524: Преобразует физический код отпускания в логическую клавишу WASD.
    const key = { KeyW: "w", KeyA: "a", KeyS: "s", KeyD: "d" }[event.code];
    // L525: Игнорирует клавишу, которая не участвовала в ручном управлении.
    if (!key || !keys.has(key)) return;
    // L526: Удаляет отпущенную клавишу и принудительно отправляет новую, в том числе нулевую, скорость.
    event.preventDefault(); keys.delete(key); updateKeys(); sendManual(true);
  // L527: Завершает обработчик отпускания физической клавиши.
  });
  // L528: Перебирает экранные кнопки ручного управления.
  for (const button of document.querySelectorAll(".key")) {
    // L529: Нажатие экранной кнопки захватывает указатель, запоминает клавишу и отправляет движение.
    button.addEventListener("pointerdown", (event) => { if (!canDrive() || event.button !== 0) return; event.preventDefault(); button.setPointerCapture(event.pointerId); keys.add(button.dataset.key); updateKeys(); sendManual(); });
    // L530: Общий обработчик отпускания удаляет клавишу и отправляет безопасное обновление скорости.
    const release = () => { if (keys.delete(button.dataset.key)) { updateKeys(); sendManual(true); } };
    // L531: Отпускание, отмена жеста и потеря захвата одинаково прекращают удержание кнопки.
    button.addEventListener("pointerup", release); button.addEventListener("pointercancel", release); button.addEventListener("lostpointercapture", release);
    // L532: Запрещает контекстное меню на WASD-кнопке, чтобы оно не мешало управлению.
    button.addEventListener("contextmenu", (event) => event.preventDefault());
  // L533: Завершает регистрацию событий экранных WASD-кнопок.
  }
  // L534: Потеря фокуса окна отпускает все ручные клавиши.
  window.addEventListener("blur", releaseKeys);
  // L535: Скрытие вкладки или окна также прекращает ручное удержание.
  document.addEventListener("visibilitychange", () => { if (document.hidden) releaseKeys(); });
  // L536: При уходе со страницы пытается отправить остановку до завершения вкладки.
  window.addEventListener("pagehide", () => {
    // L537: Снимает текущее удержание WASD.
    releaseKeys();
    // L538: keepalive просит браузер закончить POST с нулевой скоростью при закрытии страницы; доставка не гарантируется.
    if (state.manualId) fetch(`/api/robots/${encodeURIComponent(state.manualId)}/commands`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "manual", linear: 0, angular: 0 }), keepalive: true }).catch(() => {});
  // L539: Завершает обработчик ухода со страницы.
  });
  // L540: Каждые 100 мс продлевает ручное движение, пока клавиши удерживаются и управление разрешено.
  setInterval(() => { if (keys.size && canDrive()) sendManual(); }, 100);
  // L541: Каждые 500 мс проверяет свежесть сообщений WebSocket.
  setInterval(() => {
    // L542: После 2,2 с без снимков показывает «Нет свежих данных» и закрывает сокет для переподключения.
    if (state.connected && state.lastMessage && performance.now() - state.lastMessage > 2200) { setConnection(false, "Нет свежих данных"); state.socket?.close(); }
  // L543: Завершает обработчик таймера и задаёт период проверки свежести — 500 миллисекунд.
  }, 500);
  // L544: ResizeObserver вызывает resize при изменении размера контейнера карты.
  new ResizeObserver(resize).observe($("canvasWrap"));
  // L545: requestAnimationFrame повторяет кадры в ритме браузера; draw пропускает неизменённую карту.
  function frame() { draw(); requestAnimationFrame(frame); }
  // L546: Начальное состояние заголовка — ожидание подключения.
  setConnection(false, "Подключение…");
  // L547: Настраивает canvas, открывает WebSocket и запускает цикл кадров.
  resize(); connect(); requestAnimationFrame(frame);
// L548: Завершает и немедленно вызывает внешнюю функцию изоляции переменных.
})();
