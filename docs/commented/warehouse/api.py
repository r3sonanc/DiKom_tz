# FastAPI-сервер. Принимает запросы из браузера, передаёт их диспетчеру,
# запускает управляющий цикл и предоставляет WebSocket и метрики.
# HTTP подтверждает принятие действия;
# L1: asyncio запускает фоновый контроллер и асинхронные ожидания HTTP/WebSocket.
import asyncio
# L2: copy.deepcopy создаёт независимую копию снимка для вызывающего кода REST.
import copy
# L3: json превращает общий снимок в готовую строку для всех WebSocket-клиентов.
import json
# L4: logging выводит события в журнал процесса.
import logging
# L5: os читает настройки из переменных окружения контейнера.
import os
# L6: time предоставляет монотонные часы и измерение длительности такта.
import time
# L7: uuid создаёт новый идентификатор каждой сцены.
import uuid
# L8: deque — очередь с ограниченной длиной, здесь для последних 100 событий.
from collections import deque
# L9: asynccontextmanager связывает запуск и остановку ресурсов с жизнью приложения.
from contextlib import asynccontextmanager
# L10: datetime и timezone нужны для отметок времени в UTC.
from datetime import datetime, timezone
# L11: Path строит пути к файлам без ручной склейки разделителей.
from pathlib import Path
# L12: Literal описывает перечень допустимых строковых значений.
from typing import Literal

# L14: FastAPI создаёт API; HTTPException и типы WebSocket обрабатывают ответы и соединения.
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
# L15: Импортирует исключение проверки входного запроса.
from fastapi.exceptions import RequestValidationError
# L16: Импортирует ответы файлом, JSON и обычным текстом.
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
# L17: StaticFiles раздаёт JavaScript, CSS и прочие статические файлы.
from fastapi.staticfiles import StaticFiles
# L18: Pydantic проверяет поля JSON по моделям, настройкам и ограничениям Field.
from pydantic import BaseModel, ConfigDict, Field

# L20: Импортирует диспетчер задач и совместных переходов.
from warehouse.dispatcher import Dispatcher
# L21: Импортирует загрузчик карты.
from warehouse.map import load_map
# L22: Импортирует настоящий ROS-транспорт команд и состояний.
from warehouse.transport import RosTransport

# L24: Создаёт журнал именно этого Python-модуля.
LOG = logging.getLogger(__name__)
# L25: Находит каталог static рядом с api.py, независимо от рабочей директории.
STATIC = Path(__file__).with_name("static")
# L26: Общая JSON-версия живёт в окне 0,1 с; время и свежесть связи продолжают обновляться.
SNAPSHOT_PERIOD = 0.1
# L27: Сопоставляет технические имена карт понятным названиям.
MAP_NAMES = {"normal": "обычный склад", "hardcore": "узкие коридоры"}
# L28: Начинает словарь русских названий команд для журнала событий.
COMMAND_NAMES = {
    # L29: Названия поездки, парковки и остановки всего парка.
    "move": "перемещение", "park": "возврат на парковку", "stop": "остановка парка",
    # L30: Названия возобновления и сброса ошибки.
    "resume": "возобновление движения", "clear_error": "сброс ошибки",
# L31: Завершает словарь названий команд.
}
# L32: Начинает словарь названий искусственных отказов.
FAULT_NAMES = {
    # L33: Сопоставляет имена отказов подписям на русском.
    "stuck": "застревание", "link_lost": "потеря связи", "throttle": "замедление связи",
# L34: Завершает словарь названий отказов.
}

# L36: Общая модель наследует проверку Pydantic и задаёт единые строгие настройки.
class StrictModel(BaseModel):
    # L37: Запрещает неизвестные поля JSON и вещественные NaN/Infinity.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

# L39: Модель тела запроса для управления роботом.
class Command(StrictModel):
    # L40: type принимает только один из шести перечисленных типов команд.
    type: Literal["move", "park", "stop", "manual", "resume", "clear_error"]
    # L41: target — ID цели или None; длина ограничена 32 символами.
    target: str | None = Field(default=None, max_length=32)
    # L42: Линейная скорость ручного управления, по умолчанию 0, диапазон ±0,8 м/с.
    linear: float = Field(default=0, ge=-0.8, le=0.8)
    # L43: Угловая скорость из API, по умолчанию 0, диапазон ±1,5 рад/с.
    angular: float = Field(default=0, ge=-1.5, le=1.5)

# L45: Модель запроса на создание новой сцены.
class Reset(StrictModel):
    # L46: Разрешены две карты, по умолчанию normal.
    map: Literal["normal", "hardcore"] = "normal"
    # L47: Число роботов — целое от 1 до 5; парковки дополнительно проверяются при reset.
    robots: int = Field(default=3, ge=1, le=5)

# L49: Модель переключения сценария случайных постоянных заданий.
class DarkFactory(StrictModel):
    # L50: enabled — обязательный булев флаг включения режима.
    enabled: bool

# L52: Модель частичного изменения искусственных отказов.
class Fault(StrictModel):
    # L53: None означает не менять stuck; True/False — включить/выключить.
    stuck: bool | None = None
    # L54: Аналогичное необязательное поле потери связи.
    link_lost: bool | None = None
    # L55: Аналогичное поле замедления связи.
    throttle: bool | None = None

# L57: Runtime связывает транспорт, диспетчер, снимки UI и фоновый цикл управления.
class FleetRuntime:
    # L58: Конструктор принимает транспорт и параметры начальной сцены.
    def __init__(self, transport, map_name="normal", robots=3):
        # L59: Сохраняет транспорт; в рабочем запуске это RosTransport.
        self.transport = transport
        # L60: Хранит только последние 100 событий, ограничивая рост памяти.
        self.events = deque(maxlen=100)
        # L61: Фоновый управляющий цикл ещё не запущен.
        self.running = False
        # L62: Счётчик выполненных тактов контроллера.
        self.tick_count = 0
        # L63: Длительность последнего такта в секундах.
        self.tick_seconds = 0.0
        # L64: Время последней принятой телеметрии, нужно для health-check.
        self.last_received = 0.0
        # L65: Изначально ключ временного окна кэша неизвестен: первая выдача построит снимок.
        self._snapshot_cache_key = None
        # L66: Готовая JSON-строка ещё отсутствует; хранится только одна версия, без истории.
        self._snapshot_cache = None
        # L67: Создаёт начальную сцену через общий механизм reset.
        self.reset(map_name, robots)

    # L69: Сбрасывает общий JSON-кэш после изменения состояния, не меняя физику или диспетчер.
    def invalidate_snapshot(self):
        # L70: Удаляет ключ старого окна: следующая выдача не сможет принять его за свежий.
        self._snapshot_cache_key = None
        # L71: Освобождает ссылку на устаревшую JSON-строку до следующего формирования снимка.
        self._snapshot_cache = None

    # L73: Добавляет событие с уровнем info, warning или error.
    def event(self, message, level="info"):
        # L74: Начинает добавление записи в ограниченную очередь событий.
        self.events.append(
            # L75: Формирует UTC-время, уровень и текст события.
            {"time": datetime.now(timezone.utc).isoformat(), "level": level, "message": message}
        # L76: Завершает вызов добавления записи в очередь.
        )
        # L77: Выбирает уровень Python logging по строковому имени.
        LOG.log({"info": logging.INFO, "warning": logging.WARNING,
                 # L78: Для неизвестного уровня использует INFO и записывает сообщение в журнал.
                 "error": logging.ERROR}.get(level, logging.INFO), message)
        # L79: Новое событие меняет журнал снимка, поэтому прежнюю общую JSON-версию нужно сбросить.
        self.invalidate_snapshot()

    # L81: Создаёт новую логическую сцену диспетчера и запрашивает reset симулятора.
    def reset(self, map_name, robots):
        # L82: Загружает выбранную карту.
        graph = load_map(map_name)
        # L83: Генератор списка выбирает ID узлов с признаком парковки.
        parking = [node.id for node in graph.nodes.values() if node.parking]
        # L84: Проверяет, хватит ли парковок на запрошенное число роботов.
        if not 1 <= robots <= len(parking):
            # L85: Объясняет ошибку конфигурации с названием карты и числом парковок.
            raise ValueError(f"На карте «{MAP_NAMES[map_name]}» доступно парковок: {len(parking)}")
        # L86: Сохраняет новый граф.
        self.graph = graph
        # L87: Преобразует неизменную карту в словарь один раз на сцену; последующие снимки его используют.
        self._map_snapshot = graph.to_dict()
        # L88: Формирует robot1, robot2, …; нумерация для пользователя начинается с единицы.
        self.robot_ids = [f"robot{i + 1}" for i in range(robots)]
        # L89: Создаёт новый диспетчер с чистыми целями и резервами.
        self.dispatcher = Dispatcher(graph, self.robot_ids)
        # L90: Уникальный world_id отсекает сообщения старой сцены.
        self.world_id = uuid.uuid4().hex
        # L91: Номер reset основан на системном времени в наносекундах и упорядочивает reset-команды.
        self.reset_seq = time.time_ns()
        # L92: Очищает последние принятые состояния роботов.
        self.raw_states = {}
        # L93: Начинает таблицу искусственных отказов новой сцены.
        self.faults = {
            # L94: Каждый робот получает три выключенных отказа.
            robot_id: {"stuck": False, "link_lost": False, "throttle": False}
            # L95: Генератор словаря перебирает все ID нового парка.
            for robot_id in self.robot_ids
        # L96: Завершает словарь отказов.
        }
        # L97: Минус бесконечность разрешает немедленно отправить первый reset.
        self._last_reset = float("-inf")
        # L98: Пока не пришли состояния всех роботов, сцена считается неготовой.
        self._reset_pending = True
        # L99: Сценарий «Тёмный цех» в новой сцене выключен.
        self._dark = False
        # L100: Записывает создание симуляции в журнал.
        self.event(f"Новая симуляция: {MAP_NAMES[map_name]}, роботов: {robots}")

    # L102: Общий выход команд в транспорт.
    def send(self, command):
        # L103: **command копирует поля приказа, а world_id привязывает его к текущей сцене.
        self.transport.send({**command, "world_id": self.world_id})

    # L105: Один управляющий такт: принять состояния, обновить диспетчер, отправить приказы.
    def step(self, now):
        # L106: Начинает измерение длительности такта высокоточным таймером.
        start = time.perf_counter()
        # L107: Забирает из транспорта последние состояния роботов.
        for state in self.transport.receive():
            # L108: Проверяет, относится ли сообщение к текущей сцене.
            if state.get("world_id") != self.world_id:
                # L109: continue пропускает телеметрию старого мира.
                continue
            # L110: Читает ID робота из состояния.
            robot_id = state.get("robot_id")
            # L111: Принимает сообщения только от роботов заданного парка.
            if robot_id not in self.robot_ids:
                # L112: Игнорирует неизвестный ID.
                continue
            # L113: Находит предыдущее принятое состояние этого робота, если оно было.
            previous = self.raw_states.get(robot_id)
            # L114: Сравнивать seq можно непосредственно только внутри того же запуска boot_id.
            if previous and previous.get("boot_id") == state.get("boot_id"):
                # L115: seq не должен повторяться или уменьшаться в рамках запуска.
                if state.get("seq", -1) <= previous.get("seq", -1):
                    # L116: Пропускает старое или повторное состояние.
                    continue
            # L117: Диспетчер дополнительно проверяет корректность и историю перезапусков.
            if not self.dispatcher.ingest(state, now):
                # L118: Не сохраняет состояние, отвергнутое диспетчером.
                continue
            # L119: Событие ошибки записывается при появлении или изменении ошибки, без постоянных дублей.
            if state.get("error") != (previous or {}).get("error") and state.get("error"):
                # L120: Получает структуру возникшей ошибки.
                error = state["error"]
                # L121: Выбирает её текст, безопасно обрабатывая неожиданный формат.
                message = error.get("message", "Ошибка робота") if isinstance(error, dict) else "Ошибка робота"
                # L122: Записывает ошибку с понятным номером робота.
                self.event(f"Робот {robot_id.removeprefix('robot')}: {message}", "error")
            # L123: Сохраняет последнее принятое состояние робота.
            self.raw_states[robot_id] = state
            # L124: Фиксирует свежесть принятой телеметрии по монотонным часам.
            self.last_received = now
        # L125: Проверяет, получено ли хотя бы по состоянию от всех роботов.
        if len(self.raw_states) == len(self.robot_ids):
            # L126: После полного начального набора сцена готова принимать команды.
            self._reset_pending = False
        # L127: Пока подтверждения reset нет, повторяет его каждые 0,5 с.
        if self._reset_pending and now - self._last_reset >= 0.5:
            # L128: Начинает отправку reset через общий выход с world_id.
            self.send(
                # L129: Команда несёт тип reset, имя карты и размер парка.
                {"type": "reset", "map": self.graph.name, "robots": len(self.robot_ids),
                 # L130: Передаёт неизменную ревизию текущего reset, чтобы повтор был идемпотентным.
                 "reset_seq": self.reset_seq}
            # L131: Завершает отправку reset.
            )
            # L132: Запоминает время последней попытки reset для интервала повторения.
            self._last_reset = now
        # L133: Получает разрешённые на этом такте команды диспетчера.
        for command in self.dispatcher.tick(now):
            # L134: Отправляет каждую команду в ROS-транспорт.
            self.send(command)
        # L135: Увеличивает счётчик тактов управления.
        self.tick_count += 1
        # L136: Сохраняет длительность последнего такта для метрик.
        self.tick_seconds = time.perf_counter() - start
        # L137: После такта с новой телеметрией, целями и счётчиками прежняя JSON-версия уже неактуальна.
        self.invalidate_snapshot()

    # L139: async def объявляет корутину фонового контроллера.
    async def run(self):
        # L140: Ставит флаг работающего управляющего цикла.
        self.running = True
        # L141: try/finally гарантирует очистку флага даже при отмене корутины.
        try:
            # L142: Бесконечный цикл выполняется до отмены задачи при остановке сервера.
            while True:
                # L143: Передаёт монотонное время текущему такту; оно не зависит от перевода системных часов.
                self.step(time.monotonic())
                # L144: await отдаёт управление другим задачам на 0,1 с, а не блокирует сервер.
                await asyncio.sleep(0.1)
        # L145: finally выполняется при любом способе выхода из цикла.
        finally:
            # L146: Снимает флаг работоспособности контроллера.
            self.running = False

    # L148: Публичный snapshot возвращает свежий независимый словарь; изменения вызывающего не затронут карту и мир.
    def snapshot(self, now=None):
        # L149: Глубоко копирует внутренний снимок вместе с вложенными коллекциями для безопасного внешнего чтения.
        return copy.deepcopy(self._snapshot(now))

    # L151: Возвращает общую сериализованную JSON-версию для WebSocket, избегая повторной сборки каждому клиенту.
    def snapshot_json(self, now=None):
        # L152: Берёт монотонное время либо переданное тестом; оно определяет окно и давность телеметрии.
        now = time.monotonic() if now is None else now
        # L153: Целочисленный номер окна по 0,1 с: переход окна обновляет снимок даже без новых ROS-сообщений.
        key = int(now / SNAPSHOT_PERIOD)
        # L154: Перестраивает JSON только для нового окна или после явного сброса кэша.
        if self._snapshot_cache_key != key:
            # L155: Начинает однократную сериализацию внутреннего снимка и сохраняет итоговую строку.
            self._snapshot_cache = json.dumps(
                # L156: Строит свежие поля; сохраняет русский текст, убирает лишние пробелы и запрещает NaN/Infinity в JSON.
                self._snapshot(now), ensure_ascii=False, separators=(",", ":"), allow_nan=False
            # L157: Завершает вызов сериализации общей JSON-версии.
            )
            # L158: Связывает сохранённую строку с текущим временным окном.
            self._snapshot_cache_key = key
        # L159: Возвращает ту же неизменяемую строку всем клиентам этой версии, не копируя словарь карты.
        return self._snapshot_cache

    # L161: Собирает только числовые метрики, без построения карты, роботов и полного снимка.
    def metrics_snapshot(self):
        # L162: Начинает новый небольшой словарь текущих счётчиков.
        return {
            # L163: Копирует счётчики диспетчера распаковкой словаря, не отдавая наружу изменяемый оригинал.
            **self.dispatcher.metrics,
            # L164: Добавляет число выполненных тактов управляющего цикла.
            "controller_ticks": self.tick_count,
            # L165: Переводит длительность последнего такта в миллисекунды и округляет до трёх знаков.
            "controller_tick_ms": round(self.tick_seconds * 1000, 3),
        # L166: Завершает возвращаемый словарь метрик.
        }

    # L168: Внутренний построитель объединяет актуальное состояние с сохранённой картой; наружу его словарь не отдаётся напрямую.
    def _snapshot(self, now=None):
        # L169: При отсутствии now берёт текущие монотонные часы.
        now = time.monotonic() if now is None else now
        # L170: Получает цели, резервы, свежесть связи и метрики диспетчера.
        dispatch = self.dispatcher.snapshot(now)
        # L171: Извлекает список или словарь записей роботов.
        entries = dispatch.get("robots", [])
        # L172: Поддерживает словарное представление записей диспетчера.
        if isinstance(entries, dict):
            # L173: Преобразует ID → поля в список объектов с явным полем id.
            entries = [{"id": key, **value} for key, value in entries.items()]
        # L174: Строит индекс записей по ID для быстрого объединения с телеметрией.
        by_id = {entry.get("id", entry.get("robot_id")): entry for entry in entries}
        # L175: Готовит список представлений роботов для браузера.
        robots = []
        # L176: Обрабатывает каждого настроенного робота, даже если состояния ещё нет.
        for robot_id in self.robot_ids:
            # L177: Берёт последнее сырое состояние или пустой словарь.
            state = self.raw_states.get(robot_id, {})
            # L178: Берёт диспетчерские поля этого робота или пустой словарь.
            control = by_id.get(robot_id, {})
            # L179: Извлекает pose — координаты и угол.
            pose = state.get("pose", {})
            # L180: Извлекает velocity — скорости робота.
            velocity = state.get("velocity", {})
            # L181: Активный приказ или пустой словарь, если приказа нет.
            current = state.get("current_order") or {}
            # L182: Выбирает конечную цель задачи; иначе последний узел текущего перехода.
            target = control.get("goal") or (current.get("path") or [None])[-1]
            # L183: По умолчанию UI-приказ отсутствует.
            order = None
            # L184: Создаёт описание приказа, если известен активный переход или конечная цель.
            if current or target:
                # L185: Начинает объект приказа для браузера.
                order = {
                    # L186: Копирует ID текущего перехода; до его выдачи значение может быть None.
                    "id": current.get("command_id"),
                    # L187: Передаёт выбранную цель.
                    "target": target,
                    # L188: Передаёт тип приказа, по умолчанию move для ещё не выданной цели.
                    "type": current.get("type", "move"),
                    # L189: Отражает общую паузу диспетчера в статусе приказа.
                    "status": "paused" if dispatch.get("paused") else "active",
                # L190: Завершает объект приказа.
                }
            # L191: Пока телеметрии нет, статус робота — connecting.
            status = state.get("status", "connecting")
            # L192: При общей паузе idle заменяется более понятным для UI paused.
            if dispatch.get("paused") and status == "idle":
                # L193: Устанавливает отображаемый статус паузы.
                status = "paused"
            # L194: Добавляет объект одного робота в выходной список.
            robots.append(
                # L195: Начинает словарь данных робота.
                {
                    # L196: **control переносит поля целей, свежести и резервов диспетчера.
                    **control,
                    # L197: Явно указывает ID робота.
                    "id": robot_id,
                    # L198: Координата X в метрах; до телеметрии — ноль.
                    "x": pose.get("x", 0),
                    # L199: Координата Y в метрах.
                    "y": pose.get("y", 0),
                    # L200: theta — угол yaw робота в радианах.
                    "theta": pose.get("yaw", 0),
                    # L201: v — линейная скорость в м/с.
                    "v": velocity.get("linear", 0),
                    # L202: omega — угловая скорость в рад/с.
                    "omega": velocity.get("angular", 0),
                    # L203: Отображаемое состояние с учётом общей паузы.
                    "state": status,
                    # L204: Связь с роботом определяется свежестью телеметрии, поле online диспетчера.
                    "connected": control.get("online", False),
                    # L205: last_seen — давность последней принятой телеметрии в секундах.
                    "last_seen": control.get("age"),
                    # L206: Добавляет подготовленный объект приказа или None.
                    "order": order,
                    # L207: Копирует согласованный маршрут этого робота для отображения.
                    "route": list(self.dispatcher._schedule.get(robot_id, [])),
                    # L208: Передаёт текущую ошибку модели.
                    "error": state.get("error"),
                    # L209: Текущий узел либо None посреди ребра.
                    "node_id": state.get("node_id"),
                    # L210: Концы занимаемого ребра либо None.
                    "edge": state.get("edge"),
                    # L211: Добавляет настройки имитации отказов для панели управления.
                    "fault": self.faults[robot_id],
                # L212: Завершает объект робота.
                }
            # L213: Завершает append и переходит к следующему роботу.
            )
        # L214: Возвращает единый JSON-совместимый снимок мира.
        return {
            # L215: type позволяет браузеру распознать сообщение snapshot.
            "type": "snapshot",
            # L216: revision совпадает со счётчиком управляющих тактов.
            "revision": self.tick_count,
            # L217: Добавляет календарную отметку UTC в ISO-формате.
            "time": datetime.now(timezone.utc).isoformat(),
            # L218: Идентификатор сцены позволяет браузеру сбросить старые следы и данные.
            "world_id": self.world_id,
            # L219: Подставляет заранее подготовленную карту текущей сцены вместо повторного graph.to_dict().
            "map": self._map_snapshot,
            # L220: Добавляет уже собранный список роботов.
            "robots": robots,
            # L221: Передаёт состояние сценария постоянных случайных заданий.
            "dark_factory": self._dark,
            # L222: Добавляет актуальные счётчики через небольшой отдельный метод.
            "metrics": self.metrics_snapshot(),
            # L223: Превращает ограниченную очередь событий в JSON-список.
            "events": list(self.events),
            # L224: Передаёт остальные поля диспетчера без повторного списка роботов.
            "dispatcher": {key: value for key, value in dispatch.items() if key != "robots"},
        # L225: Завершает возвращаемый снимок.
        }

# L227: Фабрика приложения допускает передать транспорт и параметры явно.
def create_app(transport=None, map_name=None, robots=None):
    # L228: При обычном запуске создаёт ROS-транспорт; явно переданный транспорт сохраняет.
    transport = RosTransport() if transport is None else transport
    # L229: Начинает создание общего runtime приложения.
    runtime = FleetRuntime(
        # L230: Передаёт выбранный транспорт.
        transport,
        # L231: Имя карты берёт из аргумента, затем окружения; запасное значение normal.
        map_name or os.getenv("WAREHOUSE_MAP", "normal"),
        # L232: Число роботов берёт из аргумента либо окружения; запасное значение 3.
        robots if robots is not None else int(os.getenv("WAREHOUSE_ROBOTS", "3")),
    # L233: Завершает создание runtime.
    )

    # L235: Декоратор делает корутину менеджером запуска и завершения приложения.
    @asynccontextmanager
    # L236: lifespan вызывается FastAPI на весь срок жизни сервера.
    async def lifespan(app):
        # L237: Создаёт ROS-ноду, publisher, subscriber и поток обмена.
        transport.start()
        # L238: Запускает корутину контроллера как отдельную асинхронную задачу.
        task = asyncio.create_task(runtime.run(), name="fleet-controller")
        # L239: try/finally обеспечивает завершение задачи и транспорта при остановке сервера.
        try:
            # L240: yield передаёт управление серверу до момента завершения приложения.
            yield
        # L241: Начинает гарантированное освобождение ресурсов.
        finally:
            # L242: Просит фоновую задачу контроллера остановиться.
            task.cancel()
            # L243: Ожидает фактическое завершение задачи после отмены.
            try:
                # L244: await передаёт управление задаче до её завершения или исключения отмены.
                await task
            # L245: Ловит ожидаемый сигнал штатной отмены фоновой задачи.
            except asyncio.CancelledError:
                # L246: pass ничего не делает: отмена здесь является нормальным завершением.
                pass
            # L247: Останавливает ROS-executor, поток и контекст.
            transport.close()

    # L249: Создаёт FastAPI-сервер с названием API и указанным lifecycle.
    app = FastAPI(title="DiKom · Симуляция склада", version="1.0.0", lifespan=lifespan)
    # L250: Сохраняет runtime в состоянии приложения для доступа при диагностике.
    app.state.runtime = runtime

    # L252: Регистрирует собственный перевод ошибок проверки входного запроса.
    @app.exception_handler(RequestValidationError)
    # L253: Обработчик получает исходный запрос и исключение валидации.
    async def validation_error(request, exc):
        # L254: Начинает словарь понятных названий полей.
        names = {
            # L255: Подписи полей карты, размера парка и типа команды.
            "map": "Карта", "robots": "Число роботов", "type": "Команда",
            # L256: Подписи цели и линейной скорости.
            "target": "Точка назначения", "linear": "Линейная скорость",
            # L257: Подписи угловой скорости и переключателя режима.
            "angular": "Угловая скорость", "enabled": "Состояние режима",
            # L258: Подписи застревания и потери связи.
            "stuck": "Застревание", "link_lost": "Потеря связи",
            # L259: Подпись имитации замедления канала.
            "throttle": "Замедление связи",
        # L260: Завершает таблицу названий полей.
        }
        # L261: Начинает перевод кодов ошибок Pydantic на русский.
        messages = {
            # L262: missing означает, что обязательное поле не передали.
            "missing": "обязательное поле",
            # L263: extra_forbidden означает неизвестное лишнее поле.
            "extra_forbidden": "неизвестное поле",
            # L264: literal_error означает значение вне разрешённого перечня.
            "literal_error": "выберите одно из допустимых значений",
            # L265: Переводы неверного типа и разбора целого числа.
            "int_type": "нужно целое число", "int_parsing": "нужно целое число",
            # L266: Отдельная ошибка дробного числа там, где требуется целое.
            "int_from_float": "нужно целое число",
            # L267: Переводы неверного типа или разбора вещественного числа.
            "float_type": "нужно число", "float_parsing": "нужно число",
            # L268: Ошибка типа булева поля.
            "bool_type": "нужно значение «да» или «нет»",
            # L269: Ошибка преобразования булева значения.
            "bool_parsing": "нужно значение «да» или «нет»",
            # L270: Переводы неверной строки и превышения допустимой длины.
            "string_type": "нужна строка", "string_too_long": "слишком длинная строка",
            # L271: Перевод запрета NaN и бесконечных чисел.
            "finite_number": "нужно конечное число",
            # L272: Ошибка синтаксиса самого JSON.
            "json_invalid": "некорректный формат JSON",
            # L273: Ошибка формата тела, если вместо объекта передали другое значение.
            "model_attributes_type": "нужен объект с параметрами",
        # L274: Завершает словарь переводов ошибок.
        }
        # L275: Готовит список подробностей всех ошибок запроса.
        details = []
        # L276: Обрабатывает каждую обнаруженную Pydantic ошибку.
        for error in exc.errors():
            # L277: Получает путь к полю ошибки, например body → robots.
            location = error.get("loc", ())
            # L278: Выбирает имя конечного поля; для всего тела используется body.
            field = location[-1] if location and isinstance(location[-1], str) else "body"
            # L279: Переводит имя поля, подставляя общее название для неизвестного.
            label = names.get(field, "Запрос" if field == "body" else "Поле запроса")
            # L280: Читает машинный код ошибки.
            kind = error["type"]
            # L281: Получает параметры проверки, например минимально допустимое число.
            context = error.get("ctx", {})
            # L282: Ветка нарушения нижней границы значения.
            if kind == "greater_than_equal":
                # L283: Формирует сообщение с фактическим допустимым минимумом ge.
                message = f"значение должно быть не меньше {context['ge']}"
            # L284: Ветка нарушения верхней границы значения.
            elif kind == "less_than_equal":
                # L285: Формирует сообщение с допустимым максимумом le.
                message = f"значение должно быть не больше {context['le']}"
            # L286: Для остальных кодов использует таблицу переводов.
            else:
                # L287: Неизвестный код получает нейтральное понятное описание.
                message = messages.get(kind, "некорректное значение")
            # L288: Добавляет путь, русское сообщение и код одной ошибки в список detail.
            details.append({"loc": list(location), "msg": f"{label}: {message}", "type": kind})
        # L289: Возвращает HTTP 422 с JSON-описанием ошибок валидации.
        return JSONResponse(status_code=422, content={"detail": details})

    # L291: Регистрирует GET главной страницы.
    @app.get("/")
    # L292: Обработчик index отдаёт HTML интерфейса.
    def index():
        # L293: Возвращает index.html из каталога static файлом.
        return FileResponse(STATIC / "index.html")

    # L295: Регистрирует endpoint проверки готовности приложения.
    @app.get("/api/health")
    # L296: health оценивает контроллер и поступление телеметрии.
    def health():
        # L297: Требует запущенный цикл управления и подтверждённый начальный reset.
        ready = (runtime.running and not runtime._reset_pending
                 # L298: Дополнительно требует хотя бы одну принятую телеметрию за последние три секунды.
                 and time.monotonic() - runtime.last_received < 3)
        # L299: Начинает формирование ответа проверки готовности.
        return JSONResponse(
            # L300: Возвращает флаг ok, текущий мир и имя используемого транспорта.
            {"ok": ready, "world_id": runtime.world_id, "transport": "ROS2 Jazzy"},
            # L301: Готовому приложению соответствует HTTP 200, неготовому — 503.
            status_code=200 if ready else 503,
        # L302: Завершает ответ health-check.
        )

    # L304: Регистрирует GET для полного текущего снимка.
    @app.get("/api/state")
    # L305: Асинхронный обработчик чтения состояния.
    async def state():
        # L306: Возвращает снимок, собранный runtime.
        return runtime.snapshot()

    # L308: Регистрирует POST создания новой сцены.
    @app.post("/api/reset")
    # L309: request уже проверен по модели Reset.
    async def reset(request: Reset):
        # L310: Параметры карты могут не соответствовать числу её парковок.
        try:
            # L311: Меняет логическую сцену и запускает повторяемый ROS reset.
            runtime.reset(request.map, request.robots)
        # L312: Ловит ошибку допустимости конфигурации сцены.
        except ValueError as exc:
            # L313: Превращает её в HTTP 422, сохранив исходную причину исключения.
            raise HTTPException(422, str(exc)) from exc
        # L314: Подтверждает принятие reset и возвращает новый world_id.
        return {"ok": True, "world_id": runtime.world_id}

    # L316: Регистрирует POST управления роботом; robot_id берётся из URL.
    @app.post("/api/robots/{robot_id}/commands")
    # L317: Тело запроса проверяет Command, идентификатор адресата — обработчик.
    async def command(robot_id: str, request: Command):
        # L318: Проверяет наличие робота в текущем парке.
        if robot_id not in runtime.robot_ids:
            # L319: Возвращает HTTP 404 для неизвестного адресата.
            raise HTTPException(404, "Робот не найден")
        # L320: Проверяет, завершено ли начальное получение телеметрии.
        if runtime._reset_pending:
            # L321: HTTP 409 показывает, что сцена пока не готова принимать команды.
            raise HTTPException(409, "Ожидаем начальную телеметрию роботов")
        # L322: Перемещение требует непустую цель; для остальных команд она необязательна.
        if request.type == "move" and not request.target:
            # L323: Возвращает HTTP 422 при отсутствии target у move.
            raise HTTPException(422, "Для перемещения выберите точку назначения")
        # L324: Вызов диспетчера может отклонить цель или текущий режим работы.
        try:
            # L325: Передаёт команду в Dispatcher.submit, который сохраняет задачу или меняет режим.
            result = runtime.dispatcher.submit(
                # L326: Передаёт адресата приказа.
                robot_id,
                # L327: Передаёт тип действия.
                request.type,
                # L328: Передаёт конечную цель движения по имени аргумента.
                target=request.target,
                # L329: Передаёт запрошенную ручную линейную скорость.
                linear=request.linear,
                # L330: Передаёт запрошенную угловую скорость.
                angular=request.angular,
                # L331: Добавляет монотонную отметку получения операторской команды.
                now=time.monotonic(),
            # L332: Завершает вызов диспетчера.
            )
            # L333: Принятая команда меняет цели или режимы; немедленно сбрасывает JSON, включая частые ручные команды.
            runtime.invalidate_snapshot()
        # L334: Ловит неверную цель, конфликт назначения или неизвестные данные.
        except (ValueError, KeyError) as exc:
            # L335: Возвращает HTTP 409 с причиной невозможности принять действие.
            raise HTTPException(409, str(exc)) from exc
        # L336: Частые ручные команды не засоряют событийный журнал.
        if request.type != "manual":
            # L337: Получает русское название принятого действия.
            action = COMMAND_NAMES[request.type]
            # L338: Для движения дополняет описание конечной целью.
            if request.type == "move":
                # L339: Добавляет ID целевого узла к сообщению.
                action += f" к узлу {request.target}"
            # L340: Записывает принятое операторское действие с номером робота.
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {action}")
        # L341: HTTP-успех означает принятие задачи; прибытие подтверждается последующей телеметрией.
        return {"ok": True, "result": result}

    # L343: Регистрирует POST изменения искусственных отказов конкретного робота.
    @app.post("/api/robots/{robot_id}/faults")
    # L344: Тело Fault содержит только переданные переключатели отказов.
    async def fault(robot_id: str, request: Fault):
        # L345: Проверяет наличие адресата в текущей сцене.
        if robot_id not in runtime.robot_ids:
            # L346: Неизвестный робот получает HTTP 404.
            raise HTTPException(404, "Робот не найден")
        # L347: Преобразует модель в словарь, исключая None — поля без изменений.
        changes = request.model_dump(exclude_none=True)
        # L348: Обновляет сохранённые настройки отказов этого робота.
        runtime.faults[robot_id].update(changes)
        # L349: Изменение имитации отказов сразу делает общий снимок устаревшим.
        runtime.invalidate_snapshot()
        # L350: Отправляет симулятору полный актуальный набор флагов через ROS.
        runtime.send({"type": "fault", "robot_id": robot_id, **runtime.faults[robot_id]})
        # L351: Записывает событие только при наличии переданных изменений.
        if changes:
            # L352: Начинает соединять описания нескольких флагов через точку с запятой.
            description = "; ".join(
                # L353: Формирует понятный текст включения или выключения одного отказа.
                f"{FAULT_NAMES[name]} — {'включено' if enabled else 'выключено'}"
                # L354: Генератор перебирает изменённые поля и их значения.
                for name, enabled in changes.items()
            # L355: Завершает формирование описания отказов.
            )
            # L356: Публикует предупреждение об искусственных отказах в журнале событий.
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {description}", "warning")
        # L357: Подтверждает обработку изменения отказов.
        return {"ok": True}

    # L359: Регистрирует POST переключения сценария «Тёмный цех».
    @app.post("/api/dark-factory")
    # L360: enabled берётся из проверенной модели DarkFactory.
    async def dark_factory(request: DarkFactory):
        # L361: Меняет режим назначения случайных целей в диспетчере.
        runtime.dispatcher.set_dark_factory(request.enabled)
        # L362: Сохраняет флаг для снимков браузера.
        runtime._dark = request.enabled
        # L363: Записывает переключение режима в журнал.
        runtime.event(f"Тёмный цех: {'включён' if request.enabled else 'выключен'}")
        # L364: Подтверждает принятие нового состояния режима.
        return {"ok": True}

    # L366: Регистрирует GET текстовых метрик в формате Prometheus.
    @app.get("/metrics", response_class=PlainTextResponse)
    # L367: Обработчик преобразует числовые счётчики в строки.
    async def metrics():
        # L368: Читает только метрики: опрос мониторинга не создаёт полный снимок с картой и маршрутами.
        values = runtime.metrics_snapshot()
        # L369: Начинает список строк ответа.
        lines = []
        # L370: Перебирает имя и значение каждой метрики.
        for key, value in values.items():
            # L371: Публикует только числовые значения.
            if isinstance(value, (int, float)):
                # L372: Заменяет неподходящие символы имени на подчёркивания.
                safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in key)
                # L373: Добавляет строку с общим префиксом warehouse_ и числовым значением.
                lines.append(f"warehouse_{safe_key} {float(value)}")
        # L374: Соединяет строки с переводами строк и задаёт текстовый формат экспозиции метрик.
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # L376: Регистрирует WebSocket-канал постоянных снимков по /ws.
    @app.websocket("/ws")
    # L377: Обработчик получает объект двунаправленного соединения WebSocket.
    async def websocket(socket: WebSocket):
        # L378: Подтверждает открытие WebSocket браузеру.
        await socket.accept()
        # L379: Ошибки закрытия или медленного клиента обрабатываются как завершение соединения.
        try:
            # L380: Пока соединение работает, постоянно отправляет новые снимки.
            while True:
                # L381: Отправляет общую JSON-строку этому клиенту; его отдельное ожидание ограничено двумя секундами.
                await asyncio.wait_for(socket.send_text(runtime.snapshot_json()), timeout=2)
                # L382: Между снимками ждёт 0,2 с: частота примерно 5 Гц без учёта времени отправки.
                await asyncio.sleep(0.2)
        # L383: Ловит отключение клиента, тайм-аут отправки или закрытый сокет.
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            # L384: Нормально завершает обработчик при закрытии канала.
            pass

    # L386: Подключает раздачу статических файлов по URL /static.
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # L387: Возвращает полностью настроенный экземпляр FastAPI.
    return app

# L389: Точка входа консольной команды dispatcher из setup.py.
def main(args=None):
    # L390: Импортирует HTTP-сервер Uvicorn только при реальном запуске процесса.
    import uvicorn

    # L392: Настраивает вывод INFO-событий без технического префикса.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # L393: Читает порт из окружения, запасное значение — 8000.
    port = int(os.getenv("WAREHOUSE_PORT", "8000"))
    # L394: Выводит адрес запуска в журнал процесса.
    LOG.info("Запуск веб-интерфейса: http://localhost:%s", port)
    # L395: Запускает Uvicorn на всех интерфейсах контейнера; Compose ограничивает внешний доступ localhost.
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="warning", access_log=False)

# L397: Проверяет, запущен ли файл напрямую, а не импортирован модулем.
if __name__ == "__main__":
    # L398: Вызывает точку входа при прямом запуске файла.
    main()
