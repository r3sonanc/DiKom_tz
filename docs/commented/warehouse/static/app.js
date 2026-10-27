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
  // L36: Потеря связи имеет приоритет перед переводом обычного состояния робота.
  function robotStatus(robot) { return robot.connected === false ? "Нет связи" : stateLabels[robot.state] || "Ожидание"; }
  // L37: Собирает подпись текущего приказа для карточки и таблицы.
  function orderText(robot) {
    // L38: Если приказа нет, возвращает соответствующую подпись.
    if (!robot.order) return "Нет активного приказа";
    // L39: Уже готовую строку приказа возвращает без преобразований.
    if (typeof robot.order === "string") return robot.order;
    // L40: Локальный словарь названий основных типов приказов.
    const types = { move: "Перемещение", park: "Парковка", manual: "Ручное управление", stop: "Остановка" };
    // L41: Шаблонная строка объединяет тип приказа, необязательную цель и идентификатор.
    return `${types[robot.order.type] || "Приказ"}${robot.order.target ? ` → ${robot.order.target}` : ""}${robot.order.id ? ` · ${robot.order.id}` : ""}`;
  // L42: Закрывает текущий блок в orderText.
  }
  // L43: Приводит поля робота из серверного снимка к форме, принятой в UI.
  function normalizeRobot(robot) {
    // L44: filter удаляет подряд повторяющиеся узлы ожидания из отображаемого маршрута.
    const route = (robot.route || []).filter((node, index, nodes) => index === 0 || node !== nodes[index - 1]);
    // L45: Оператор ... копирует поля; ?. и ?? дают запасные значения для отсутствующей телеметрии.
    return { ...robot, id: String(robot.id), connected: robot.connected !== false, x: robot.x ?? robot.pose?.x ?? 0, y: robot.y ?? robot.pose?.y ?? 0, theta: robot.theta ?? robot.pose?.yaw ?? 0, v: robot.v ?? robot.velocity?.linear ?? 0, omega: robot.omega ?? robot.velocity?.angular ?? 0, state: robot.state ?? robot.status ?? "idle", fault: robot.fault || {}, route };
  // L46: Закрывает текущий блок в normalizeRobot.
  }
  // L47: Показывает временное уведомление; isError по умолчанию false.
  function toast(message, isError = false) {
    // L48: Находит контейнер уведомления в HTML.
    const el = $("toast");
    // L49: Записывает отформатированное сообщение как безопасный текст.
    el.textContent = eventText(message);
    // L50: classList.toggle включает CSS-класс ошибки согласно isError.
    el.classList.toggle("error", isError);
    // L51: hidden=false делает уведомление видимым.
    el.hidden = false;
    // L52: Отменяет таймер предыдущего уведомления.
    clearTimeout(toastTimer);
    // L53: Скрывает ошибку через 6,5 секунды, обычное уведомление — через 3,5 секунды.
    toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6500 : 3500);
  // L54: Закрывает текущий блок в toast.
  }
  // L55: Добавляет событие; по умолчанию уровень info и текущее время ISO.
  function addEvent(message, level = "info", time = new Date().toISOString()) {
    // L56: push добавляет объект события в конец журнала браузера.
    state.events.push({ message: eventText(message), level, time });
    // L57: splice удаляет старые записи сверх лимита 80, ограничивая память.
    if (state.events.length > 80) state.events.splice(0, state.events.length - 80);
    // L58: DocumentFragment собирает новые HTML-элементы до вставки на страницу.
    const fragment = document.createDocumentFragment();
    // L59: slice берёт 35 последних событий, reverse показывает новые сверху.
    for (const entry of state.events.slice(-35).reverse()) {
      // L60: Создаёт один элемент списка для события.
      const row = document.createElement("li");
      // L61: Шаблонная строка назначает базовый класс и окраску warning/error.
      row.className = `event ${["warning", "error"].includes(entry.level) ? entry.level : ""}`;
      // L62: Создаёт HTML-элемент времени события.
      const timestamp = document.createElement("time");
      // L63: Date разбирает время из серверного сообщения.
      const date = new Date(entry.time);
      // L64: Корректную дату форматирует как русское время; при ошибке показывает исходную строку.
      timestamp.textContent = Number.isNaN(date.getTime()) ? String(entry.time) : date.toLocaleTimeString("ru-RU", { hour12: false });
      // L65: Создаёт отдельный элемент для текста события.
      const messageEl = document.createElement("span");
      // L66: textContent защищает текст события от интерпретации как HTML.
      messageEl.textContent = entry.message;
      // L67: Добавляет время и сообщение внутрь строки события.
      row.append(timestamp, messageEl);
      // L68: Добавляет готовую строку во временный фрагмент DOM.
      fragment.append(row);
    // L69: Завершает сборку DOM-строк последних событий.
    }
    // L70: Одной операцией заменяет видимые записи журнала собранным фрагментом.
    $("eventsList").replaceChildren(fragment);
  // L71: Закрывает текущий блок в addEvent.
  }

  // L73: async возвращает Promise; универсальная функция отправляет JSON-команду на сервер.
  async function request(path, payload, options = {}) {
    // L74: AbortController позволяет прервать зависший HTTP-запрос.
    const controller = new AbortController();
    // L75: Через 4500 мс вызывает abort, чтобы интерфейс не ждал бесконечно.
    const timeout = setTimeout(() => controller.abort(), 4500);
    // L76: try начинает блок обработки возможных сетевых ошибок.
    try {
      // L77: await ждёт fetch: POST отправляет JSON-тело, signal подключает отмену, options дополняет параметры.
      const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal, ...options });
      // L78: Разбирает JSON-ответ; при пустом или неверном JSON использует пустой объект.
      const result = await response.json().catch(() => ({}));
      // L79: Проверяет HTTP-статус и явный отказ приложения через result.ok.
      if (!response.ok || result.ok === false) {
        // L80: Извлекает строку ошибки или собирает сообщения валидации из массива detail.
        const detail = typeof result.detail === "string" ? result.detail : Array.isArray(result.detail) ? result.detail.map((issue) => issue.msg).filter(Boolean).join("; ") || "Проверьте параметры команды" : "Команда отклонена сервером";
        // L81: throw выбрасывает исключение, которое обработает вызывающий код.
        throw new Error(detail);
      // L82: Закрывает проверку неуспешного ответа; успешный ответ обрабатывается ниже.
      }
      // L83: Возвращает разобранный ответ при успешном запросе.
      return result;
    // L84: catch перехватывает ошибку запроса, разбора или ответа сервера.
    } catch (error) {
      // L85: Различает тайм-аут, сетевой сбой и обычную ошибку, создавая понятное сообщение.
      throw new Error(error.name === "AbortError" ? "Сервер не ответил вовремя" : error instanceof TypeError ? "Не удалось связаться с сервером" : eventText(error.message || "Ошибка соединения"));
    // L86: finally отменяет таймер независимо от результата запроса.
    } finally { clearTimeout(timeout); }
  // L87: Закрывает текущий блок в request.
  }
  // L88: Строит URL команды робота; encodeURIComponent безопасно кодирует id внутри адреса.
  function command(id, body, options) { return request(`/api/robots/${encodeURIComponent(id)}/commands`, body, options); }
  // L89: Обрабатывает операторскую команду для выбранного робота.
  async function operatorCommand(body, label) {
    // L90: Получает объект выбранного робота из состояния UI.
    const robot = selectedRobot();
    // L91: Без выбранного робота или связи с сервером команда не отправляется.
    if (!robot || !state.connected) return;
    // L92: Начинает защищённую отправку с обработкой отказа сервера.
    try {
      // L93: Перед stop/resume отпускает ручные клавиши и блокирует переключение режима.
      if (["stop", "resume"].includes(body.type)) { state.manualChanging = true; await releaseKeys(); }
      // L94: Ждёт принятия команды сервером; это ещё не означает прибытие робота.
      await command(robot.id, body);
      // L95: После stop/resume сбрасывает локальный ручной режим и сразу обновляет показанное состояние.
      if (["resume", "stop"].includes(body.type)) { state.manualId = null; robot.state = body.type === "stop" ? "paused" : "idle"; }
      // L96: Записывает факт принятой операторской команды в журнал.
      addEvent(`${robot.id}: ${label}`);
      // L97: Показывает уведомление об успешном принятии команды.
      toast(label);
    // L98: При отказе сервера показывает ошибку и сохраняет её в журнале.
    } catch (error) { toast(error.message, true); addEvent(`${robot.id}: ${error.message}`, "error"); }
    // L99: В любом исходе снимает блокировку переключения и обновляет карточку робота.
    finally { state.manualChanging = false; updateDetails(); }
  // L100: Закрывает текущий блок в operatorCommand.
  }

  // L102: Заполняет HTML-select вариантами items и выбирает current.
  function populateSelect(el, items, current, placeholder) {
    // L103: Собирает варианты списка вне живого DOM.
    const fragment = document.createDocumentFragment();
    // L104: При необходимости создаёт первый пустой вариант-подсказку.
    if (placeholder) { const option = document.createElement("option"); option.value = ""; option.textContent = placeholder; fragment.append(option); }
    // L105: Деструктуризация [value,label] получает код и подпись каждой option.
    for (const [value, label] of items) { const option = document.createElement("option"); option.value = value; option.textContent = label; fragment.append(option); }
    // L106: Заменяет весь набор вариантов готовым фрагментом.
    el.replaceChildren(fragment);
    // L107: Выбирает option с текущим значением.
    el.value = current;
  // L108: Закрывает текущий блок в populateSelect.
  }
  // L109: Переключает выбранного операторами робота.
  function selectRobot(id) {
    // L110: Повторный выбор того же робота не требует обновления.
    if (state.selectedId === id) return;
    // L111: Отпускает ручные клавиши перед переключением, чтобы не продолжить старую команду.
    releaseKeys();
    // L112: Сохраняет id нового выбранного робота.
    state.selectedId = id;
    // L113: Синхронизирует выпадающий список с выбором на карте или в таблице.
    $("robotSelect").value = id;
    // L114: Обновляет карточку и доступность кнопок управления.
    updateDetails();
    // L115: Обновляет выделение выбранного робота в таблице.
    updateFleet();
    // L116: Помечает canvas для перерисовки выделения.
    view.dirty = true;
  // L117: Закрывает текущий блок в selectRobot.
  }
  // L118: Обновляет значения карточки и разрешённые оператору действия.
  function updateDetails() {
    // L119: Получает выбранного робота или undefined.
    const robot = selectedRobot();
    // L120: Boolean превращает наличие робота и WebSocket-связи в разрешающий флаг.
    const usable = Boolean(robot && state.connected);
    // L121: Проверяет, сообщает ли сервер о ручном режиме выбранного робота.
    const remoteManual = robot?.state === "manual";
    // L122: Считает ручной режим активным по локальному выбору или серверному состоянию.
    const manual = Boolean(robot && (state.manualId === robot.id || remoteManual));
    // L123: Ручное управление блокируется, если сейчас вручную управляют другим роботом.
    const manualLocked = Boolean(state.manualId && state.manualId !== robot?.id);
    // L124: Показывает имя выбранного робота или приглашение выбрать его.
    setText("selectedRobotName", robot ? robotName(robot.id) : "Робот не выбран");
    // L125: Показывает актуальное состояние выбранного робота.
    setText("selectedRobotState", robot ? robotStatus(robot) : "Ожидание телеметрии");
    // L126: Из robotN делает короткую метку РN для значка карточки.
    setText("robotAvatar", robot?.id.replace(/^robot/i, "Р").slice(0, 4) || "Р");
    // L127: Окрашивает значок карточки в цвет выбранного робота.
    $("robotAvatar").style.color = robot ? robotColor(robot.id) : "";
    // L128: Красный индикатор карточки означает ошибку или потерю телеметрии робота.
    $("robotStateDot").className = `status-dot ${robot ? robot.connected === false || robot.error ? "error" : "online" : ""}`;
    // L129: Форматирует координаты робота в метрах.
    setText("positionValue", robot ? `${number(robot.x)} / ${number(robot.y)}` : "—");
    // L130: Переводит угол из радиан в градусы и нормализует к диапазону от 0 до 360.
    setText("headingValue", robot ? `${number(((robot.theta * 180 / Math.PI) % 360 + 360) % 360, 1)}°` : "—");
    // L131: Выводит линейную скорость из телеметрии.
    setText("speedValue", robot ? number(robot.v) : "—");
    // L132: Выводит угловую скорость из телеметрии.
    setText("angularValue", robot ? number(robot.omega) : "—");
    // L133: Показывает текущий приказ, цель и id в читаемом виде.
    setText("orderValue", robot ? orderText(robot) : "—");
    // L134: join соединяет узлы маршрута стрелками; при отсутствии маршрута даёт подпись.
    setText("routeValue", robot?.route.length ? robot.route.join(" → ") : "Маршрут не назначен");
    // L135: Скрывает блок ошибки, когда у робота нет ошибки.
    $("robotError").hidden = !robot?.error;
    // L136: Записывает понятное сообщение об ошибке робота.
    $("robotError").textContent = errorText(robot?.error);
    // L137: Синхронизирует переключатель ручного режима с текущим состоянием.
    $("manualEnabled").checked = manual;
    // L138: Блокирует ручной режим при отсутствии связи, переключении или управлении другим роботом.
    $("manualEnabled").disabled = !usable || state.manualChanging || manualLocked || robot?.connected === false;
    // L139: Разрешает поездку только при выборе цели, связи и выключенном ручном режиме.
    $("moveButton").disabled = !usable || !state.targetId || manual || robot?.connected === false;
    // L140: Разрешает парковку только при связи с выбранным роботом и автоматическом режиме.
    $("parkButton").disabled = !usable || manual || robot?.connected === false;
    // L141: Остановку можно нажать, когда выбран робот и доступен сервер.
    $("stopButton").disabled = !usable;
    // L142: Возобновление требует связи с выбранным роботом.
    $("resumeButton").disabled = !usable || robot?.connected === false;
    // L143: Сброс ошибки доступен только при наличии ошибки и соединения с сервером.
    $("clearErrorButton").disabled = !usable || !robot?.error;
    // L144: Все экранные WASD-кнопки разрешаются только владельцу активного ручного режима.
    for (const button of document.querySelectorAll(".key")) button.disabled = !usable || !manual || state.manualId !== robot?.id || robot?.connected === false || state.manualChanging;
    // L145: Перебирает соответствия HTML-переключателей и серверных полей отказов.
    for (const [field, key] of [["faultStuck", "stuck"], ["faultLink", "link_lost"], ["faultThrottle", "throttle"]]) {
      // L146: Блокирует переключатель отказа при недоступном сервере или отсутствии выбора.
      $(field).disabled = !usable;
      // L147: Показывает текущее значение отказа выбранного робота.
      $(field).checked = Boolean(robot?.fault[key]);
    // L148: Завершает обновление трёх переключателей искусственных отказов.
    }
  // L149: Закрывает текущий блок в updateDetails.
  }
  // L150: Обновляет таблицу парка, повторно используя уже созданные строки.
  function updateFleet() {
    // L151: Удаляет DOM-строки и записи Map для исчезнувших из мира роботов.
    for (const [id, row] of fleetRows) if (!state.robots.some((robot) => robot.id === id)) { row.remove(); fleetRows.delete(id); }
    // L152: При первой загрузке очищает строку-заглушку таблицы.
    if (!fleetRows.size) $("fleetBody").replaceChildren();
    // L153: Перебирает каждого робота из последнего серверного снимка.
    for (const robot of state.robots) {
      // L154: Ищет существующую строку таблицы по id робота.
      let row = fleetRows.get(robot.id);
      // L155: Новую строку создаёт только при первом появлении робота.
      if (!row) {
        // L156: Создаёт HTML-строку таблицы tr.
        row = document.createElement("tr");
        // L157: tabIndex=0 позволяет выбрать строку клавиатурой через Tab.
        row.tabIndex = 0;
        // L158: aria-label даёт программе чтения с экрана понятное действие строки.
        row.setAttribute("aria-label", `Выбрать: ${robotName(robot.id)}`);
        // L159: Нажатие мышью выбирает робота из этой строки.
        row.addEventListener("click", () => selectRobot(robot.id));
        // L160: Enter или пробел выбирают строку с клавиатуры; preventDefault убирает прокрутку пробелом.
        row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectRobot(robot.id); } });
        // L161: Создаёт шесть ячеек под поля робота.
        for (let index = 0; index < 6; index++) row.append(document.createElement("td"));
        // L162: Создаёт контейнер цветной метки и имени робота.
        const identityWrap = document.createElement("span"); identityWrap.className = "fleet-id";
        // L163: Создаёт маленький элемент цветовой метки.
        const mark = document.createElement("i"); mark.className = "fleet-color";
        // L164: Создаёт подпись робота как текстовое содержимое.
        const name = document.createElement("span"); name.textContent = robotName(robot.id);
        // L165: Собирает идентификатор робота и вставляет в первую ячейку.
        identityWrap.append(mark, name); row.cells[0].append(identityWrap);
        // L166: Создаёт значок состояния во второй ячейке.
        const badge = document.createElement("span"); row.cells[1].append(badge);
        // L167: Добавляет строку в DOM и сохраняет ссылку для последующих обновлений.
        $("fleetBody").append(row); fleetRows.set(robot.id, row);
      // L168: Завершает создание строки: дальше одинаково обновляются новые и существующие строки.
      }
      // L169: Назначает CSS-класс выделения текущему выбранному роботу.
      row.className = `fleet-row${robot.id === state.selectedId ? " selected" : ""}`;
      // L170: Обновляет цвет маркера робота в таблице.
      row.querySelector(".fleet-color").style.background = robotColor(robot.id);
      // L171: Находит ранее созданный значок состояния во второй ячейке.
      const badge = row.cells[1].firstElementChild;
      // L172: Выбирает оформление ошибки, движения или ожидания по телеметрии.
      badge.className = `state-badge ${robot.error || !robot.connected ? "error" : robot.state === "moving" || robot.state === "manual" ? "moving" : robot.state === "idle" || robot.state === "parked" ? "idle" : ""}`;
      // L173: Обновляет текст состояния робота.
      badge.textContent = robotStatus(robot);
      // L174: Записывает координаты в третью ячейку.
      row.cells[2].textContent = `${number(robot.x)} / ${number(robot.y)}`;
      // L175: Записывает линейную скорость с единицей м/с в четвёртую ячейку.
      row.cells[3].textContent = `${number(robot.v)} м/с`;
      // L176: Пятая ячейка показывает приказ; title даёт сообщение ошибки при наведении.
      row.cells[4].textContent = orderText(robot); row.cells[4].title = errorText(robot.error);
      // L177: Находит ячейку связи и включает класс lost при потере телеметрии.
      const link = row.cells[5]; link.className = `link-cell${robot.connected === false ? " lost" : ""}`;
      // L178: Показывает состояние связи робота и возраст последнего принятого сообщения.
      link.textContent = robot.connected === false ? `Нет связи · ${number(robot.last_seen, 1)} с` : robot.fault.throttle ? `Замедление · ${number(robot.last_seen, 1)} с` : `В сети · ${number(robot.last_seen, 1)} с`;
    // L179: Завершает обновление строк всех роботов.
    }
    // L180: Обновляет общее количество роботов в заголовке таблицы.
    setText("fleetCount", `${state.robots.length} роботов`);
  // L181: Закрывает текущий блок в updateFleet.
  }
  // L182: Принимает очередной полный снимок мира, полученный через WebSocket.
  function acceptSnapshot(snapshot) {
    // L183: Отклоняет снимок без карты или массива роботов.
    if (!snapshot.map || !Array.isArray(snapshot.robots)) return;
    // L184: Запоминает прежний набор id, чтобы не пересоздавать список без необходимости.
    const priorIds = state.robots.map((robot) => robot.id).join("|");
    // L185: Сохраняет полный последний снимок сервера.
    state.snapshot = snapshot;
    // L186: map преобразует каждую запись робота к формату интерфейса.
    state.robots = snapshot.robots.map(normalizeRobot);
    // L187: Создаёт Map узлов по строковому id для быстрого доступа при рисовании.
    state.nodes = new Map((snapshot.map.nodes || []).map((node) => [String(node.id), node]));
    // L188: Фиксирует момент получения сообщения по монотонным часам браузера.
    state.lastMessage = performance.now();
    // L189: Строит ключ сцены из world_id, имени карты и структуры узлов.
    const mapKey = `${snapshot.world_id || ""}:${snapshot.map.name}:${JSON.stringify(snapshot.map.nodes)}`;
    // L190: При смене мира или карты сбрасывает связанное с прежней сценой состояние.
    if (mapKey !== state.mapKey) {
      // L191: Сохраняет новый ключ сцены.
      state.mapKey = mapKey;
      // L192: Снимает клавиши и ручной режим, отменяет отложенную ручную команду прежнего мира.
      keys.clear(); updateKeys(); manualPending = null; state.manualId = null;
      // L193: Выбирает название карты в настройках по серверному снимку.
      $("mapSelect").value = snapshot.map.name === "hardcore" ? "hardcore" : "normal";
      // L194: Синхронизирует поле числа роботов с реальным составом сцены.
      $("robotCount").value = state.robots.length;
      // L195: Очищает следы прошлой симуляции.
      state.trails.clear();
      // L196: Сбрасывает выбранную цель из старой карты.
      state.targetId = "";
      // L197: Заполняет список целей узлами карты и помечает парковки.
      populateSelect($("targetSelect"), Array.from(state.nodes.values(), (node) => [String(node.id), `${node.id}${node.parking ? " · парковка" : ""}`]), "", "Выберите узел");
      // L198: Подбирает масштаб и сдвиг для новой карты.
      fitMap();
    // L199: Завершает сброс локального состояния при смене мира или карты.
    }
    // L200: Строит ключ текущего состава роботов.
    const ids = state.robots.map((robot) => robot.id).join("|");
    // L201: Если прежний выбор исчез, выбирает первого доступного робота.
    if (!state.robots.some((robot) => robot.id === state.selectedId)) state.selectedId = state.robots[0]?.id || "";
    // L202: При изменении состава обновляет варианты выбора робота.
    if (ids !== priorIds) populateSelect($("robotSelect"), state.robots.map((robot) => [robot.id, robotName(robot.id)]), state.selectedId);
    // L203: Отменяет ручное управление, если управляемый робот исчез из сцены.
    if (state.manualId && !state.robots.some((robot) => robot.id === state.manualId)) { releaseKeys(); state.manualId = null; }
    // L204: Перебирает роботов для обновления следов их движения.
    for (const robot of state.robots) {
      // L205: Получает существующий след или создаёт пустой массив.
      const trail = state.trails.get(robot.id) || [];
      // L206: Берёт последнюю точку следа.
      const previous = trail[trail.length - 1];
      // L207: Добавляет точку только после смещения более 0,06 м, экономя память и рисование.
      if (!previous || Math.hypot(previous.x - robot.x, previous.y - robot.y) > .06) trail.push({ x: robot.x, y: robot.y });
      // L208: Оставляет не более 120 последних точек следа.
      if (trail.length > 120) trail.splice(0, trail.length - 120);
      // L209: Сохраняет след в Map под id робота.
      state.trails.set(robot.id, trail);
    // L210: Завершает обновление следов текущих роботов.
    }
    // L211: Удаляет следы исчезнувших роботов.
    for (const id of state.trails.keys()) if (!state.robots.some((robot) => robot.id === id)) state.trails.delete(id);
    // L212: Считает роботов со свежей телеметрией и показывает долю «в сети».
    setText("onlineMetric", `${state.robots.filter((robot) => robot.connected !== false).length}/${state.robots.length}`);
    // L213: Считает движение по ненулевой линейной или угловой скорости.
    setText("movingMetric", state.robots.filter((robot) => Math.abs(robot.v) > .01 || Math.abs(robot.omega) > .01).length);
    // L214: Получает серверные метрики или пустой объект при их отсутствии.
    const metrics = snapshot.metrics || {};
    // L215: Показывает число выполненных задач, поддерживая запасные имена поля.
    setText("completedMetric", metrics.completed_tasks ?? metrics.completed_orders ?? metrics.completed ?? 0);
    // L216: Считает роботов с ошибкой или без свежей связи.
    setText("errorMetric", state.robots.filter((robot) => robot.error || robot.connected === false).length);
    // L217: Синхронизирует переключатель «Тёмного цеха» с сервером.
    $("darkFactory").checked = Boolean(snapshot.dark_factory);
    // L218: Скрывает заглушку после первого пригодного снимка карты.
    $("emptyState").hidden = true;
    // L219: Выбирает русское имя карты.
    const mapLabel = snapshot.map.name === "hardcore" ? "Узкие коридоры" : "Обычный склад";
    // L220: Показывает карту, число узлов и рёбер и наличие общей паузы парка.
    setText("mapCaption", `${mapLabel} · ${state.nodes.size} узлов · ${(snapshot.map.edges || []).length} рёбер${snapshot.dispatcher?.paused ? " · парк на паузе" : ""}`);
    // L221: Устанавливает допустимое число роботов для текущей карты.
    updateRobotCount();
    // L222: Пытается получить числовое время симуляции из снимка.
    const seconds = Number(snapshot.sim_time ?? snapshot.time ?? 0);
    // L223: Параллельно разбирает серверное время ISO как дату.
    const timestamp = new Date(snapshot.time);
    // L224: Показывает минуты/секунды либо часы из ISO-даты, если числового времени нет.
    setText("simulationTime", Number.isFinite(seconds) ? `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}` : !Number.isNaN(timestamp.getTime()) ? timestamp.toLocaleTimeString("ru-RU", { hour12: false }) : "—");
    // L225: Перебирает серверные события, которые уже могли приходить в предыдущих снимках.
    for (const entry of snapshot.events || []) {
      // L226: Составляет ключ события из времени, уровня и текста для удаления дублей.
      const key = `${entry.time}|${entry.level}|${entry.message}`;
      // L227: continue пропускает уже показанное событие.
      if (state.seenEvents.has(key)) continue;
      // L228: Запоминает ключ нового события.
      state.seenEvents.add(key);
      // L229: Ограничивает множество ключей 300 записями, удаляя самый старый.
      if (state.seenEvents.size > 300) state.seenEvents.delete(state.seenEvents.values().next().value);
      // L230: Добавляет новое серверное событие в журнал интерфейса.
      addEvent(entry.message, entry.level, entry.time);
    // L231: Завершает добавление новых серверных событий.
    }
    // L232: Обновляет карточку, таблицу и помечает карту для следующего кадра.
    updateDetails(); updateFleet(); view.dirty = true;
  // L233: Закрывает текущий блок в acceptSnapshot.
  }

  // L235: Меняет индикатор соединения браузера с HTTP/WebSocket-сервером.
  function setConnection(online, text) {
    // L236: Запоминает факт изменения состояния соединения.
    const changed = state.connected !== online;
    // L237: Обновляет локальный флаг WebSocket-связи браузера.
    state.connected = online;
    // L238: Переключает зелёный/красный класс кружка в заголовке.
    $("connectionDot").className = `status-dot ${online ? "online" : "error"}`;
    // L239: Пишет рядом с кружком «Подключено», «Переподключение…» или другое состояние.
    setText("connectionText", text);
    // L240: Управлять «Тёмным цехом» можно только при доступном сервере.
    $("darkFactory").disabled = !online;
    // L241: Создавать новую сцену можно только при доступном сервере.
    $("resetButton").disabled = !online;
    // L242: При разрыве связи отпускает ручные клавиши.
    if (!online) releaseKeys();
    // L243: Если соединение изменилось, сразу обновляет доступность кнопок карточки.
    if (changed) updateDetails();
  // L244: Закрывает текущий блок в setConnection.
  }
  // L245: Создаёт WebSocket-соединение и обработчики его событий.
  function connect() {
    // L246: Отменяет прежний таймер переподключения, если он был.
    clearTimeout(reconnectTimer);
    // L247: Создаёт /ws по текущему адресу; HTTPS требует защищённого протокола wss.
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
    // L248: Сохраняет сокет как единственное актуальное соединение UI.
    state.socket = socket;
    // L249: При открытии актуального сокета сбрасывает задержку и показывает «Подключено».
    socket.addEventListener("open", () => { if (state.socket !== socket) return; reconnectDelay = 600; setConnection(true, "Подключено"); });
    // L250: Подписывается на сообщения сервера со снимками мира.
    socket.addEventListener("message", (event) => {
      // L251: Игнорирует событие от уже заменённого старого сокета.
      if (state.socket !== socket) return;
      // L252: Разбирает JSON снимка, обновляет UI и подтверждает связь с сервером.
      try { const snapshot = JSON.parse(event.data); if (!snapshot.type || snapshot.type === "snapshot") { acceptSnapshot(snapshot); setConnection(true, "Подключено"); } }
      // L253: При невалидном JSON пишет ошибку в консоль разработчика.
      catch { console.error("Не удалось прочитать данные симуляции"); }
    // L254: Завершает обработчик входящего WebSocket-сообщения.
    });
    // L255: Ошибка сокета приводит к его закрытию и стандартному переподключению.
    socket.addEventListener("error", () => socket.close());
    // L256: Обработчик закрытия соединения запускает восстановление связи.
    socket.addEventListener("close", () => {
      // L257: Закрытие устаревшего сокета не влияет на новое соединение.
      if (state.socket !== socket) return;
      // L258: Показывает потерю соединения и блокирует зависимые действия.
      setConnection(false, "Переподключение…");
      // L259: Назначает вызов connect после текущей задержки.
      reconnectTimer = setTimeout(connect, reconnectDelay);
      // L260: Увеличивает задержку в 1,7 раза, ограничивая её шестью секундами.
      reconnectDelay = Math.min(6000, reconnectDelay * 1.7);
    // L261: Завершает обработчик закрытия WebSocket.
    });
  // L262: Закрывает текущий блок в connect.
  }

  // L264: Преобразует координаты мира в пиксели; экранная ось Y направлена вниз.
  function worldPoint(x, y) { return { x: view.x + x * view.scale, y: view.y - y * view.scale }; }
  // L265: Преобразует координаты указателя относительно окна в координаты внутри canvas.
  function screenPoint(event) { const box = canvas.getBoundingClientRect(); return { x: event.clientX - box.left, y: event.clientY - box.top }; }
  // L266: Подбирает масштаб и расположение так, чтобы вся карта была видна.
  function fitMap() {
    // L267: Без полученной карты подбор обзора не выполняется.
    if (!state.snapshot?.map) return;
    // L268: Деструктуризация читает размеры склада, задавая запасные 16×12 м.
    const { width = 16, height = 12 } = state.snapshot.map;
    // L269: Выбирает меньший масштаб, помещающий ширину и высоту с полями.
    view.scale = Math.min((view.width - 75) / width, (view.height - 90) / height);
    // L270: Ограничивает масштаб снизу пятью пикселями на метр.
    view.scale = Math.max(5, view.scale);
    // L271: Центрирует карту по горизонтали.
    view.x = (view.width - width * view.scale) / 2;
    // L272: Центрирует карту по вертикали с небольшим поправочным сдвигом.
    view.y = (view.height + height * view.scale) / 2 + 5;
    // L273: Помечает обзор настроенным и карту нуждающейся в перерисовке.
    view.fitted = true; view.dirty = true;
  // L274: Закрывает текущий блок в fitMap.
  }
  // L275: Изменяет масштаб вокруг заданного центра; по умолчанию это центр canvas.
  function zoom(factor, center = { x: view.width / 2, y: view.height / 2 }) {
    // L276: Ограничивает масштаб диапазоном от 10 до 160 пикселей на метр.
    const scale = Math.min(160, Math.max(10, view.scale * factor));
    // L277: Рассчитывает отношение нового масштаба к прежнему.
    const ratio = scale / view.scale;
    // L278: Смещает начало X так, чтобы выбранная точка масштабирования не сдвинулась.
    view.x = center.x - (center.x - view.x) * ratio;
    // L279: Аналогично сохраняет положение центра масштабирования по Y.
    view.y = center.y - (center.y - view.y) * ratio;
    // L280: Сохраняет масштаб и запрашивает перерисовку.
    view.scale = scale; view.dirty = true;
  // L281: Закрывает текущий блок в zoom.
  }
  // L282: Подгоняет размер canvas под контейнер и плотность пикселей экрана.
  function resize() {
    // L283: Читает реальные экранные размеры контейнера карты.
    const box = $("canvasWrap").getBoundingClientRect();
    // L284: Ограничивает devicePixelRatio двумя, уменьшая размер буфера на плотных экранах.
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    // L285: Сохраняет размеры области рисования в CSS-пикселях, минимум один пиксель.
    view.width = Math.max(1, box.width); view.height = Math.max(1, box.height);
    // L286: Задаёт размер растрового буфера canvas с учётом плотности пикселей.
    canvas.width = Math.round(view.width * ratio); canvas.height = Math.round(view.height * ratio);
    // L287: Масштабирует контекст, сохраняя удобные CSS-координаты при рисовании.
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    // L288: Снова вписывает карту в новый размер и помечает её изменённой.
    fitMap(); view.dirty = true;
  // L289: Закрывает текущий блок в resize.
  }
  // L290: Рисует отрезок между мировыми точками с выбранным цветом, толщиной и пунктиром.
  function line(from, to, color, width, dash = []) {
    // L291: Преобразует оба конца отрезка в экранные координаты.
    const a = worldPoint(from.x, from.y); const b = worldPoint(to.x, to.y);
    // L292: Создаёт путь, задаёт оформление, рисует отрезок и сбрасывает пунктир.
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.stroke(); ctx.setLineDash([]);
  // L293: Закрывает текущий блок в line.
  }
  // L294: Рисует текущую карту, следы и роботов на canvas.
  function draw() {
    // L295: Пропускает кадр, если данные и обзор не изменились.
    if (!view.dirty) return;
    // L296: Снимает признак dirty: текущие изменения сейчас будут нарисованы.
    view.dirty = false;
    // L297: Очищает прежнее изображение в пределах canvas.
    ctx.clearRect(0, 0, view.width, view.height);
    // L298: Получает карту из последнего снимка; ?. допускает отсутствие снимка.
    const map = state.snapshot?.map;
    // L299: Если карта ещё не пришла, после очистки рисовать нечего.
    if (!map) return;
    // L300: Находит экранные углы прямоугольника склада.
    const bottomLeft = worldPoint(0, 0); const topRight = worldPoint(map.width, map.height);
    // L301: Заливает фон внутренней области склада.
    ctx.fillStyle = "#f0f4f9"; ctx.fillRect(bottomLeft.x, topRight.y, map.width * view.scale, map.height * view.scale);
    // L302: Рисует вертикальные линии метровой сетки.
    for (let x = 1; x < map.width; x++) line({ x, y: 0 }, { x, y: map.height }, "#e5ebf3", .7);
    // L303: Рисует горизонтальные линии метровой сетки.
    for (let y = 1; y < map.height; y++) line({ x: 0, y }, { x: map.width, y }, "#e5ebf3", .7);
    // L304: Приводит рёбра из массива или объекта к паре id узлов.
    const edges = (map.edges || []).map((edge) => Array.isArray(edge) ? edge : [edge.from, edge.to]);
    // L305: Перебирает рёбра графа, распаковывая начало и конец.
    for (const [from, to] of edges) {
      // L306: Находит координаты обоих узлов по их id.
      const a = state.nodes.get(String(from)); const b = state.nodes.get(String(to));
      // L307: Рисует широкую дорожку и пунктирную ось существующего ребра.
      if (a && b) { line(a, b, "#fff", Math.max(8, view.scale * .68)); line(a, b, "#becbdd", 1.25, [4, 4]); }
    // L308: Завершает отрисовку рёбер навигационного графа.
    }
    // L309: Перебирает физические стены склада.
    for (const wall of map.walls || []) {
      // L310: Распаковывает координаты стены из массива либо объекта.
      const [x1, y1, x2, y2] = Array.isArray(wall) ? wall : [wall.x1, wall.y1, wall.x2, wall.y2];
      // L311: Рисует широкую подложку стены.
      line({ x: x1, y: y1 }, { x: x2, y: y2 }, "#bdcad9", 7);
      // L312: Рисует узкую тёмную линию стены поверх подложки.
      line({ x: x1, y: y1 }, { x: x2, y: y2 }, "#778aa2", 2);
    // L313: Завершает отрисовку физических стен.
    }
    // L314: Перебирает роботов для рисования следов, маршрутов и блокировок.
    for (const robot of state.robots) {
      // L315: Получает сохранённые точки следа данного робота.
      const trail = state.trails.get(robot.id) || [];
      // L316: След имеет отрезок только при наличии хотя бы двух точек.
      if (trail.length > 1) {
        // L317: Устанавливает прозрачность для ненавязчивого отображения следа.
        ctx.globalAlpha = .15;
        // L318: Соединяет соседние точки следа линиями цвета робота.
        for (let index = 1; index < trail.length; index++) line(trail[index - 1], trail[index], robotColor(robot.id), 2);
        // L319: Восстанавливает непрозрачность последующих фигур.
        ctx.globalAlpha = 1;
      // L320: Завершает отрисовку непустого следа робота.
      }
      // L321: Показывает маршрут только выбранного робота и только при нескольких узлах.
      if (robot.id === state.selectedId && robot.route.length > 1) {
        // L322: Перебирает соседние пары узлов отображаемого маршрута.
        for (let index = 1; index < robot.route.length; index++) {
          // L323: Находит координаты начала и конца очередного сегмента маршрута.
          const a = state.nodes.get(String(robot.route[index - 1])); const b = state.nodes.get(String(robot.route[index]));
          // L324: Рисует сегмент маршрута полупрозрачным синим цветом.
          if (a && b) line(a, b, "#4b88e88a", 5);
        // L325: Завершает перебор сегментов маршрута.
        }
      // L326: Завершает отрисовку маршрута выбранного робота.
      }
      // L327: Ошибка, застревание или потеря связи отмечаются как блокировка участка.
      if (robot.error || robot.fault.stuck || robot.connected === false) {
        // L328: Получает текущее занятое ребро из телеметрии.
        const edge = robot.edge;
        // L329: Приводит ребро к массиву двух id, учитывая отсутствие ребра.
        const ends = Array.isArray(edge) ? edge : edge ? [edge.from, edge.to] : [];
        // L330: Пунктирную блокировку можно нарисовать при двух известных концах ребра.
        if (ends.length === 2) {
          // L331: Ищет оба узла заблокированного ребра.
          const a = state.nodes.get(String(ends[0])); const b = state.nodes.get(String(ends[1]));
          // L332: Рисует красный пунктир на заблокированном ребре.
          if (a && b) line(a, b, "#d9626d", 5, [7, 4]);
        // L333: Завершает выделение занятого ребра.
        }
        // L334: Находит занятый узел робота, если тот стоит точно на узле.
        const node = state.nodes.get(String(robot.node_id));
        // L335: Обводит заблокированный узел красным кольцом.
        if (node) { const p = worldPoint(node.x, node.y); ctx.beginPath(); ctx.arc(p.x, p.y, 12, 0, Math.PI * 2); ctx.strokeStyle = "#da6f79"; ctx.lineWidth = 2; ctx.stroke(); }
      // L336: Завершает выделение блокировок неисправного или недоступного робота.
      }
    // L337: Завершает перебор роботов для следов, маршрутов и блокировок.
    }
    // L338: Перебирает все навигационные узлы карты.
    for (const node of state.nodes.values()) {
      // L339: Вычисляет экранную позицию узла.
      const p = worldPoint(node.x, node.y);
      // L340: Проверяет, выбран ли данный узел в качестве операторской цели.
      const target = String(node.id) === state.targetId;
      // L341: Выделяет выбранную цель голубым кольцом.
      if (target) { ctx.beginPath(); ctx.arc(p.x, p.y, 13, 0, Math.PI * 2); ctx.fillStyle = "#347be31c"; ctx.fill(); ctx.strokeStyle = "#4d89e9"; ctx.lineWidth = 1.4; ctx.stroke(); }
      // L342: Парковки рисуются отдельно от обычных навигационных узлов.
      if (node.parking) {
        // L343: Рисует светлый квадрат парковки.
        ctx.fillStyle = "#e4eef5"; ctx.fillRect(p.x - 9, p.y - 9, 18, 18);
        // L344: Обводит квадрат парковки.
        ctx.strokeStyle = "#a7bcd0"; ctx.lineWidth = 1; ctx.strokeRect(p.x - 9, p.y - 9, 18, 18);
        // L345: Пишет букву «П» по центру парковки.
        ctx.font = "bold 14px Segoe UI, sans-serif"; ctx.fillStyle = "#5c7f9b"; ctx.textAlign = "center"; ctx.fillText("П", p.x, p.y + 5);
      // L346: Обычный узел рисует белым кругом, меняя обводку выбранной цели.
      } else { ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill(); ctx.strokeStyle = target ? "#4c84db" : "#9cafc7"; ctx.lineWidth = 1.5; ctx.stroke(); }
      // L347: Подписывает id каждого узла, учитывая дополнительную высоту парковочного значка.
      ctx.font = "13px Segoe UI, sans-serif"; ctx.textAlign = "center"; ctx.fillStyle = target ? "#3671c9" : "#73859c"; ctx.fillText(String(node.id), p.x, p.y + (node.parking ? 27 : 24));
    // L348: Завершает отрисовку всех навигационных узлов.
    }
    // L349: Рисует корпус и подпись каждого робота поверх карты.
    for (const robot of state.robots) drawRobot(robot);
    // L350: Выбирает целое число метров для экранной масштабной линейки.
    const meter = Math.max(1, Math.floor(65 / view.scale));
    // L351: Переводит выбранную длину линейки в пиксели.
    const barWidth = meter * view.scale;
    // L352: Рисует горизонтальную масштабную линейку с засечками.
    ctx.strokeStyle = "#91a2b6"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(20, view.height - 31); ctx.lineTo(20, view.height - 26); ctx.lineTo(20 + barWidth, view.height - 26); ctx.lineTo(20 + barWidth, view.height - 31); ctx.stroke();
    // L353: Подписывает физическую длину линейки в метрах.
    ctx.fillStyle = "#75869c"; ctx.textAlign = "center"; ctx.font = "13px Segoe UI, sans-serif"; ctx.fillText(`${meter} м`, 20 + barWidth / 2, view.height - 10);
  // L354: Закрывает текущий блок в draw.
  }
  // L355: Рисует одного робота по координатам и углу из телеметрии.
  function drawRobot(robot) {
    // L356: Переводит положение робота из метров в экранные пиксели.
    const p = worldPoint(robot.x, robot.y);
    // L357: Берёт текущий масштаб пикселей на метр.
    const scale = view.scale;
    // L358: Выбирает цвет по id робота.
    const color = robotColor(robot.id);
    // L359: Проверяет, выбран ли робот оператором.
    const selected = robot.id === state.selectedId;
    // L360: Выделяет выбранного робота полупрозрачным кругом.
    if (selected) { ctx.beginPath(); ctx.arc(p.x, p.y, scale * .72 + 3, 0, Math.PI * 2); ctx.fillStyle = `${color}13`; ctx.fill(); ctx.strokeStyle = `${color}65`; ctx.lineWidth = 1.3; ctx.stroke(); }
    // L361: Сохраняет настройки контекста, переносит начало в центр робота и учитывает угол.
    ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(-robot.theta);
    // L362: Задаёт лёгкую тень корпуса.
    ctx.shadowColor = "#183b6a18"; ctx.shadowBlur = 5; ctx.shadowOffsetY = 2;
    // L363: Задаёт тёмный цвет для колёс.
    ctx.fillStyle = "#31435a";
    // L364: Рисует одно колесо относительно центра робота.
    ctx.fillRect(-.24 * scale, -.39 * scale, .48 * scale, .11 * scale);
    // L365: Рисует второе колесо на противоположной стороне.
    ctx.fillRect(-.24 * scale, .28 * scale, .48 * scale, .11 * scale);
    // L366: Нет связи — серый корпус; при связи используется цвет робота.
    ctx.fillStyle = robot.connected === false ? "#9aa8b8" : color;
    // L367: Рисует корпус 0,8×0,6 м как скруглённый прямоугольник.
    ctx.beginPath(); ctx.roundRect(-.4 * scale, -.3 * scale, .8 * scale, .6 * scale, Math.min(5, scale * .1)); ctx.fill();
    // L368: Отключает тень для деталей корпуса.
    ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
    // L369: Обводит корпус тонкой полупрозрачной линией.
    ctx.strokeStyle = "#ffffff55"; ctx.lineWidth = 1; ctx.stroke();
    // L370: Рисует белую стрелку направления передней части робота.
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.moveTo(.31 * scale, 0); ctx.lineTo(.08 * scale, -.12 * scale); ctx.lineTo(.08 * scale, .12 * scale); ctx.closePath(); ctx.fill();
    // L371: Рисует светлую внутреннюю деталь корпуса.
    ctx.fillStyle = "#ffffff45"; ctx.fillRect(-.27 * scale, -.14 * scale, .16 * scale, .28 * scale);
    // L372: Восстанавливает контекст, отменяя локальное смещение и поворот.
    ctx.restore();
    // L373: Получает подпись робота и задаёт шрифт.
    const label = robotName(robot.id); ctx.font = "600 14px Segoe UI, sans-serif";
    // L374: Измеряет ширину имени для размера подложки.
    const textWidth = ctx.measureText(label).width;
    // L375: Рисует светлую подложку имени над роботом.
    ctx.fillStyle = "#ffffffed"; ctx.beginPath(); ctx.roundRect(p.x - textWidth / 2 - 7, p.y - scale * .7 - 25, textWidth + 14, 23, 4); ctx.fill();
    // L376: Пишет имя робота; ошибка или потеря связи меняют цвет на красный.
    ctx.textAlign = "center"; ctx.fillStyle = robot.error || robot.connected === false ? "#c25b65" : color; ctx.fillText(label, p.x, p.y - scale * .7 - 8);
    // L377: При ошибке или потере связи рисует красный значок с восклицательным знаком.
    if (robot.error || robot.connected === false) { ctx.beginPath(); ctx.arc(p.x + scale * .42, p.y - scale * .33, 7, 0, Math.PI * 2); ctx.fillStyle = "#e56a72"; ctx.fill(); ctx.fillStyle = "#fff"; ctx.font = "bold 12px Segoe UI"; ctx.fillText("!", p.x + scale * .42, p.y - scale * .33 + 4); }
  // L378: Закрывает текущий блок в drawRobot.
  }

  // L380: Обрабатывает короткое нажатие на карту, выбирая робота или ближайший узел.
  function clickMap(point) {
    // L381: Ищет робота, чей экранный корпус находится достаточно близко к нажатию.
    let hit = state.robots.find((robot) => { const p = worldPoint(robot.x, robot.y); return Math.hypot(p.x - point.x, p.y - point.y) < Math.max(15, view.scale * .6); });
    // L382: Если найден робот, выбирает его и не ищет узел под ним.
    if (hit) { selectRobot(hit.id); return; }
    // L383: Ограничивает поиск узла радиусом 18 экранных пикселей.
    let distance = 18;
    // L384: Перебирает навигационные узлы для выбора ближайшего к нажатию.
    for (const node of state.nodes.values()) {
      // L385: Вычисляет экранные координаты узла и расстояние до указателя.
      const p = worldPoint(node.x, node.y); const current = Math.hypot(p.x - point.x, p.y - point.y);
      // L386: Сохраняет ближайший узел внутри допустимого радиуса.
      if (current < distance) { hit = node; distance = current; }
    // L387: Завершает поиск ближайшего к нажатию навигационного узла.
    }
    // L388: Выбирает найденный узел целью, обновляет список, кнопки и выделение карты.
    if (hit) { state.targetId = String(hit.id); $("targetSelect").value = state.targetId; updateDetails(); view.dirty = true; }
  // L389: Закрывает текущий блок в clickMap.
  }
  // L390: Колесо меняет масштаб относительно указателя; passive:false разрешает отменить прокрутку страницы.
  canvas.addEventListener("wheel", (event) => { event.preventDefault(); zoom(Math.exp(-event.deltaY * .0015), screenPoint(event)); }, { passive: false });
  // L391: Левая кнопка начинает жест и захватывает указатель, чтобы не терять события вне canvas.
  canvas.addEventListener("pointerdown", (event) => { if (event.button !== 0) return; const p = screenPoint(event); pointer = { id: event.pointerId, startX: p.x, startY: p.y, x: p.x, y: p.y, moved: false }; canvas.setPointerCapture(event.pointerId); });
  // L392: Смещение больше 4 px считается перетаскиванием; двигает обзор вслед за указателем.
  canvas.addEventListener("pointermove", (event) => { if (!pointer || pointer.id !== event.pointerId) return; const p = screenPoint(event); if (Math.hypot(p.x - pointer.startX, p.y - pointer.startY) > 4) pointer.moved = true; if (pointer.moved) { view.x += p.x - pointer.x; view.y += p.y - pointer.y; view.dirty = true; } pointer.x = p.x; pointer.y = p.y; });
  // L393: По отпусканию без перетаскивания выбирает объект карты и завершает жест.
  canvas.addEventListener("pointerup", (event) => { if (!pointer || pointer.id !== event.pointerId) return; if (!pointer.moved) clickMap(screenPoint(event)); pointer = null; });
  // L394: Отменённый браузером жест сбрасывает сохранённый указатель.
  canvas.addEventListener("pointercancel", () => { pointer = null; });
  // L395: Кнопка «плюс» увеличивает масштаб в 1,25 раза.
  $("zoomIn").addEventListener("click", () => zoom(1.25));
  // L396: Кнопка «минус» уменьшает масштаб до 80% прежнего.
  $("zoomOut").addEventListener("click", () => zoom(.8));
  // L397: Кнопка «Вся карта» снова вписывает склад в область просмотра.
  $("fitMap").addEventListener("click", fitMap);
  // L398: Изменение списка роботов вызывает общий обработчик выбора.
  $("robotSelect").addEventListener("change", (event) => selectRobot(event.target.value));
  // L399: Выбор цели в списке обновляет состояние управления и выделение canvas.
  $("targetSelect").addEventListener("change", (event) => { state.targetId = event.target.value; updateDetails(); view.dirty = true; });
  // L400: Кнопка «Отправить» назначает move к выбранному узлу через operatorCommand.
  $("moveButton").addEventListener("click", () => operatorCommand({ type: "move", target: state.targetId }, `Назначено перемещение → ${state.targetId}`));
  // L401: Кнопка парковки отправляет park без явной цели: парковку выбирает диспетчер.
  $("parkButton").addEventListener("click", () => operatorCommand({ type: "park" }, "Назначена парковка"));
  // L402: Кнопка остановки отправляет stop, который на сервере ставит весь парк на паузу.
  $("stopButton").addEventListener("click", () => operatorCommand({ type: "stop" }, "Парк остановлен"));
  // L403: Кнопка возобновления отправляет resume выбранному роботу.
  $("resumeButton").addEventListener("click", () => operatorCommand({ type: "resume" }, "Движение возобновлено"));
  // L404: Кнопка сброса ошибки отправляет clear_error.
  $("clearErrorButton").addEventListener("click", () => operatorCommand({ type: "clear_error" }, "Ошибка сброшена"));
  // L405: Очищает локальный журнал UI, затем добавляет запись об очистке.
  $("clearEvents").addEventListener("click", () => { state.events.length = 0; $("eventsList").replaceChildren(); addEvent("Журнал интерфейса очищен"); });
  // L406: Ограничивает количество роботов числом парковок: три на normal, пять на hardcore.
  function updateRobotCount() { const max = $("mapSelect").value === "hardcore" ? 5 : 3; $("robotCount").max = max; if (Number($("robotCount").value) > max) $("robotCount").value = max; }
  // L407: Переключение карты немедленно обновляет предел числа роботов.
  $("mapSelect").addEventListener("change", updateRobotCount);
  // L408: Кнопка новой симуляции запускает асинхронный сброс мира на сервере.
  $("resetButton").addEventListener("click", async () => {
    // L409: Преобразует введённое количество и допустимый максимум в числа.
    const count = Number($("robotCount").value); const max = Number($("robotCount").max);
    // L410: Отклоняет дробное число или количество вне диапазона карты до отправки запроса.
    if (!Number.isInteger(count) || count < 1 || count > max) { toast(`Укажите число роботов от 1 до ${max}`, true); return; }
    // L411: Блокирует ручное переключение и дожидается освобождения WASD.
    state.manualChanging = true; await releaseKeys();
    // L412: Блокирует повторное нажатие кнопки сброса на время запроса.
    $("resetButton").disabled = true;
    // L413: Отправляет новую карту и число роботов, очищает следы и уведомляет оператора.
    try { await request("/api/reset", { map: $("mapSelect").value, robots: count }); state.trails.clear(); state.manualId = null; toast("Создана новая симуляция"); addEvent("Симуляция перезапущена"); }
    // L414: Показывает сообщение при ошибке пересоздания мира.
    catch (error) { toast(error.message, true); }
    // L415: В любом исходе восстанавливает доступность управления по текущему соединению.
    finally { state.manualChanging = false; $("resetButton").disabled = !state.connected; updateDetails(); }
  // L416: Завершает обработчик кнопки пересоздания симуляции.
  });
  // L417: Переключатель «Тёмного цеха» запускает запрос изменения режима.
  $("darkFactory").addEventListener("change", async (event) => {
    // L418: Читает новое логическое значение checkbox.
    const enabled = event.target.checked;
    // L419: Отправляет enabled серверу и записывает изменение режима в локальный журнал.
    try { await request("/api/dark-factory", { enabled }); addEvent(`Тёмный цех ${enabled ? "включён" : "выключен"}`); }
    // L420: При отказе возвращает прежнее положение переключателя и показывает ошибку.
    catch (error) { event.target.checked = !enabled; toast(error.message, true); }
  // L421: Завершает обработчик переключателя «Тёмного цеха».
  });
  // L422: Связывает id трёх переключателей отказов с полями серверного JSON.
  for (const [id, field] of [["faultStuck", "stuck"], ["faultLink", "link_lost"], ["faultThrottle", "throttle"]]) {
    // L423: Для каждого переключателя регистрирует обработчик change.
    $(id).addEventListener("change", async (event) => {
      // L424: Получает выбранного робота; без него имитировать отказ некого.
      const robot = selectedRobot(); if (!robot) return;
      // L425: Читает, включил или выключил оператор выбранный отказ.
      const enabled = event.target.checked;
      // L426: Отправляет {имя_отказа: значение} выбранному роботу и пишет событие.
      try { await request(`/api/robots/${encodeURIComponent(robot.id)}/faults`, { [field]: enabled }); addEvent(`${robot.id}: ${faultLabels[field].toLowerCase()} ${enabled ? "— включено" : "— выключено"}`, enabled ? "warning" : "info"); }
      // L427: При ошибке запроса восстанавливает checkbox и показывает причину.
      catch (error) { event.target.checked = !enabled; toast(error.message, true); }
    // L428: Завершает обработчик изменения одного искусственного отказа.
    });
  // L429: Завершает регистрацию обработчиков трёх отказов.
  }

  // L431: Подсвечивает экранные WASD-кнопки согласно множеству нажатых клавиш.
  function updateKeys() { for (const button of document.querySelectorAll(".key")) button.classList.toggle("pressed", keys.has(button.dataset.key)); }
  // L432: W−S задаёт ±0,5 м/с; A−D задаёт ±1,25 рад/с; противоположные клавиши взаимно гасятся.
  function manualVelocity() { return { linear: (Number(keys.has("w")) - Number(keys.has("s"))) * .5, angular: (Number(keys.has("a")) - Number(keys.has("d"))) * 1.25 }; }
  // L433: Отправляет последнее ручное управление; force позволяет отправить нулевую остановку.
  function sendManual(force = false) {
    // L434: Получает id робота, которым управляют вручную.
    const id = state.manualId;
    // L435: Без робота или необходимости отправки возвращает уже работающую Promise-цепочку.
    if (!id || (!state.connected && !force) || (!force && !keys.size)) return manualPump;
    // L436: Сохраняет только самую новую ручную скорость вместо очереди старых движений.
    manualPending = { id, velocity: manualVelocity() };
    // L437: При активном отправителе не запускает второй параллельный цикл.
    if (manualPump) return manualPump;
    // L438: Создаёт асинхронный цикл отправки ручных команд.
    manualPump = (async () => {
      // L439: Повторяет отправку, пока есть более свежая ожидающая команда.
      while (manualPending) {
        // L440: Забирает ожидающую команду и освобождает место для нового состояния клавиш.
        const next = manualPending; manualPending = null;
        // L441: Отправляет тип manual и поля скоростей из объекта velocity.
        try { await command(next.id, { type: "manual", ...next.velocity }); }
        // L442: При сетевой ошибке очищает очередь и клавиши, снимает подсветку и показывает сообщение.
        catch (error) { manualPending = null; keys.clear(); updateKeys(); toast(error.message, true); }
      // L443: Завершает цикл отправки самых свежих ручных команд.
      }
    // L444: После завершения цикла очищает ссылку на активный отправитель.
    })().finally(() => { manualPump = null; });
    // L445: Возвращает Promise: вызывающий код может дождаться освобождения управления.
    return manualPump;
  // L446: Закрывает текущий блок в sendManual.
  }
  // L447: Отпускает все ручные клавиши и при необходимости отправляет остановку.
  function releaseKeys() {
    // L448: Запоминает, были ли нажаты какие-либо клавиши.
    const hadKeys = keys.size > 0;
    // L449: Очищает Set клавиш и обновляет экранную подсветку.
    keys.clear(); updateKeys();
    // L450: Если движение или отправка были активны, отправляет принудительную нулевую скорость.
    if (hadKeys || manualPump) return sendManual(true);
    // L451: Иначе возвращает уже завершённый Promise для единообразного await.
    return Promise.resolve();
  // L452: Закрывает текущий блок в releaseKeys.
  }
  // L453: Обработчик checkbox включает или завершает ручной режим робота.
  $("manualEnabled").addEventListener("change", async (event) => {
    // L454: Без выбранного робота переключение не выполняется.
    const robot = selectedRobot(); if (!robot) return;
    // L455: Получает желаемое состояние ручного режима из checkbox.
    const enabled = event.target.checked;
    // L456: Блокирует управление на время переключения и обновляет карточку.
    state.manualChanging = true; updateDetails();
    // L457: Начинает блок обработки возможного отказа сервера.
    try {
      // L458: Перед переключением гарантирует освобождение локальных клавиш.
      await releaseKeys();
      // L459: Для включения отправляет manual с нулевыми скоростями; для выхода — resume.
      await command(robot.id, enabled ? { type: "manual", linear: 0, angular: 0 } : { type: "resume" });
      // L460: Запоминает владельца ручного управления либо очищает его.
      state.manualId = enabled ? robot.id : null;
      // L461: Сразу обновляет локальное состояние робота до следующего серверного снимка.
      robot.state = enabled ? "manual" : "idle";
      // L462: Добавляет понятное сообщение о включении или выключении режима.
      addEvent(`${robot.id}: ручное управление ${enabled ? "включено, остальной парк на паузе" : "выключено"}`);
    // L463: Показывает ошибку переключения режима.
    } catch (error) { toast(error.message, true); }
    // L464: Всегда снимает блокировку переключения и обновляет доступность кнопок.
    finally { state.manualChanging = false; updateDetails(); }
  // L465: Завершает обработчик переключения ручного режима.
  });
  // L466: Проверяет связь, совпадение владельца ручного режима и выбранного робота и отсутствие переключения.
  function canDrive() { return Boolean(state.connected && state.manualId && state.manualId === state.selectedId && !state.manualChanging && selectedRobot()?.connected !== false); }
  // L467: Регистрирует обработчик нажатия физической клавиши.
  document.addEventListener("keydown", (event) => {
    // L468: event.code распознаёт физические WASD независимо от раскладки клавиатуры.
    const key = { KeyW: "w", KeyA: "a", KeyS: "s", KeyD: "d" }[event.code];
    // L469: Escape немедленно отпускает ручные клавиши.
    if (event.code === "Escape") { releaseKeys(); return; }
    // L470: Определяет поля ввода, в которых WASD должны вводить текст, а не управлять роботом.
    const textField = ["SELECT", "TEXTAREA"].includes(event.target.tagName) || (event.target.tagName === "INPUT" && !["checkbox", "button"].includes(event.target.type));
    // L471: Игнорирует неподходящую клавишу, заблокированное управление и редактируемые элементы.
    if (!key || !canDrive() || textField || event.target.isContentEditable) return;
    // L472: Отменяет обычное действие клавиши браузера при управлении роботом.
    event.preventDefault();
    // L473: Первое нажатие добавляет клавишу в Set и отправляет скорость; автоповторы не дублируются.
    if (!keys.has(key)) { keys.add(key); updateKeys(); sendManual(); }
  // L474: Завершает обработчик нажатия физической клавиши.
  });
  // L475: Регистрирует обработчик отпускания физической клавиши.
  document.addEventListener("keyup", (event) => {
    // L476: Преобразует физический код отпускания в логическую клавишу WASD.
    const key = { KeyW: "w", KeyA: "a", KeyS: "s", KeyD: "d" }[event.code];
    // L477: Игнорирует клавишу, которая не участвовала в ручном управлении.
    if (!key || !keys.has(key)) return;
    // L478: Удаляет отпущенную клавишу и принудительно отправляет новую, в том числе нулевую, скорость.
    event.preventDefault(); keys.delete(key); updateKeys(); sendManual(true);
  // L479: Завершает обработчик отпускания физической клавиши.
  });
  // L480: Перебирает экранные кнопки ручного управления.
  for (const button of document.querySelectorAll(".key")) {
    // L481: Нажатие экранной кнопки захватывает указатель, запоминает клавишу и отправляет движение.
    button.addEventListener("pointerdown", (event) => { if (!canDrive() || event.button !== 0) return; event.preventDefault(); button.setPointerCapture(event.pointerId); keys.add(button.dataset.key); updateKeys(); sendManual(); });
    // L482: Общий обработчик отпускания удаляет клавишу и отправляет безопасное обновление скорости.
    const release = () => { if (keys.delete(button.dataset.key)) { updateKeys(); sendManual(true); } };
    // L483: Отпускание, отмена жеста и потеря захвата одинаково прекращают удержание кнопки.
    button.addEventListener("pointerup", release); button.addEventListener("pointercancel", release); button.addEventListener("lostpointercapture", release);
    // L484: Запрещает контекстное меню на WASD-кнопке, чтобы оно не мешало управлению.
    button.addEventListener("contextmenu", (event) => event.preventDefault());
  // L485: Завершает регистрацию событий экранных WASD-кнопок.
  }
  // L486: Потеря фокуса окна отпускает все ручные клавиши.
  window.addEventListener("blur", releaseKeys);
  // L487: Скрытие вкладки или окна также прекращает ручное удержание.
  document.addEventListener("visibilitychange", () => { if (document.hidden) releaseKeys(); });
  // L488: При уходе со страницы пытается отправить остановку до завершения вкладки.
  window.addEventListener("pagehide", () => {
    // L489: Снимает текущее удержание WASD.
    releaseKeys();
    // L490: keepalive просит браузер закончить POST с нулевой скоростью при закрытии страницы; доставка не гарантируется.
    if (state.manualId) fetch(`/api/robots/${encodeURIComponent(state.manualId)}/commands`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "manual", linear: 0, angular: 0 }), keepalive: true }).catch(() => {});
  // L491: Завершает обработчик ухода со страницы.
  });
  // L492: Каждые 100 мс продлевает ручное движение, пока клавиши удерживаются и управление разрешено.
  setInterval(() => { if (keys.size && canDrive()) sendManual(); }, 100);
  // L493: Каждые 500 мс проверяет свежесть сообщений WebSocket.
  setInterval(() => {
    // L494: После 2,2 с без снимков показывает «Нет свежих данных» и закрывает сокет для переподключения.
    if (state.connected && state.lastMessage && performance.now() - state.lastMessage > 2200) { setConnection(false, "Нет свежих данных"); state.socket?.close(); }
  // L495: Завершает обработчик таймера и задаёт период проверки свежести — 500 миллисекунд.
  }, 500);
  // L496: ResizeObserver вызывает resize при изменении размера контейнера карты.
  new ResizeObserver(resize).observe($("canvasWrap"));
  // L497: requestAnimationFrame повторяет кадры в ритме браузера; draw пропускает неизменённую карту.
  function frame() { draw(); requestAnimationFrame(frame); }
  // L498: Начальное состояние заголовка — ожидание подключения.
  setConnection(false, "Подключение…");
  // L499: Настраивает canvas, открывает WebSocket и запускает цикл кадров.
  resize(); connect(); requestAnimationFrame(frame);
// L500: Завершает и немедленно вызывает внешнюю функцию изоляции переменных.
})();
