# Учебная копия актуального warehouse/api.py; рабочий исходник — главный источник истины.
# После удаления строк «# L…:» код и номера строк совпадают с рабочим файлом.
# Recovery проверяет свежесть sim_time и положение; health отделяет доступность сервиса от флота.
# L1: asyncio запускает фоновый контроллер и асинхронные ожидания HTTP/WebSocket.
import asyncio
# L2: copy.deepcopy создаёт независимую копию снимка для вызывающего кода REST.
import copy
# L3: json превращает общий снимок в готовую строку для всех WebSocket-клиентов.
import json
# L4: logging выводит события в журнал процесса.
import logging
# L5: Подключает модуль или используемые имена.
import math
# L6: os читает настройки из переменных окружения контейнера.
import os
# L7: time предоставляет монотонные часы и измерение длительности такта.
import time
# L8: uuid создаёт новый идентификатор каждой сцены.
import uuid
# L9: deque — очередь с ограниченной длиной, здесь для последних 100 событий.
from collections import deque
# L10: asynccontextmanager связывает запуск и остановку ресурсов с жизнью приложения.
from contextlib import asynccontextmanager
# L11: datetime и timezone нужны для отметок времени в UTC.
from datetime import datetime, timezone
# L12: Path строит пути к файлам без ручной склейки разделителей.
from pathlib import Path
# L13: Literal описывает перечень допустимых строковых значений.
from typing import Literal

# L15: FastAPI создаёт API; HTTPException и типы WebSocket обрабатывают ответы и соединения.
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
# L16: Импортирует исключение проверки входного запроса.
from fastapi.exceptions import RequestValidationError
# L17: Импортирует ответы файлом, JSON и обычным текстом.
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
# L18: StaticFiles раздаёт JavaScript, CSS и прочие статические файлы.
from fastapi.staticfiles import StaticFiles
# L19: Pydantic проверяет поля JSON по моделям, настройкам и ограничениям Field.
from pydantic import BaseModel, ConfigDict, Field

# L21: Импортирует диспетчер задач и совместных переходов.
from warehouse.dispatcher import Dispatcher
# L22: Подключает модуль или используемые имена.
from warehouse.map import load_map, segment_distance
# L23: Подключает модуль или используемые имена.
from warehouse.robot import NODE_TOLERANCE, ROBOT_RADIUS
# L24: Импортирует настоящий ROS-транспорт команд и состояний.
from warehouse.transport import RosTransport

# L26: Поясняет настройку рабочего исходника.
# ROS launch creates loggers with propagation disabled; use process handlers.
# L27: Передаёт записи к обработчикам процесса; ROS LaunchLogger по умолчанию отключает propagate.
logging.getLogger(__package__).propagate = True
# L28: Создаёт журнал именно этого Python-модуля.
LOG = logging.getLogger(__name__)
# L29: Передаёт записи к обработчикам процесса; ROS LaunchLogger по умолчанию отключает propagate.
LOG.propagate = True
# L30: Находит каталог static рядом с api.py, независимо от рабочей директории.
STATIC = Path(__file__).with_name("static")
# L31: Общая JSON-версия живёт в окне 0,1 с; время и свежесть связи продолжают обновляться.
SNAPSHOT_PERIOD = 0.1
# L32: Сопоставляет технические имена карт понятным названиям.
MAP_NAMES = {"normal": "обычный склад", "hardcore": "узкие коридоры"}
# L33: Начинает словарь русских названий команд для журнала событий.
COMMAND_NAMES = {
    # L34: Названия поездки, парковки и остановки всего парка.
    "move": "перемещение", "park": "возврат на парковку", "stop": "остановка парка",
    # L35: Названия возобновления и сброса ошибки.
    "resume": "возобновление движения", "clear_error": "сброс ошибки",
# L36: Завершает словарь названий команд.
}
# L37: Начинает словарь названий искусственных отказов.
FAULT_NAMES = {
    # L38: Сопоставляет имена отказов подписям на русском.
    "stuck": "застревание", "link_lost": "потеря связи", "throttle": "замедление связи",
# L39: Завершает словарь названий отказов.
}
# L40: Сохраняет значение: HTTP, WebSocket и состояние флота.
EVENT_NAMES = {
    # L41: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "LINK_LOST": "Потеря связи: последняя позиция может быть устаревшей",
    # L42: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "LINK_RECOVERED": "Связь восстановлена, проверяется безопасное продолжение",
    # L43: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "FLEET_SAFETY_STOP": "Защитная остановка флота",
    # L44: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "ROBOT_ERROR": "Ошибка робота",
    # L45: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "ERROR_CLEARED": "Ошибка робота успешно сброшена",
    # L46: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "FLEET_RESUMED": "Движение флота возобновлено",
    # L47: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "PLANNING_FAILED": "Безопасный маршрут не построен",
    # L48: Продолжает условие или выражение: HTTP, WebSocket и состояние флота.
    "ROBOT_RESTARTED": "Робот перезапущен: требуется подтверждение продолжения",
# L49: Завершает выражение.
}

# L51: Определяет метод _finite_number: HTTP, WebSocket и состояние флота.
def _finite_number(value):
    # L52: Проверяет условие: HTTP, WebSocket и состояние флота.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # L53: Возвращает результат: HTTP, WebSocket и состояние флота.
        return False
    # L54: Начинает операцию с обработкой ошибок.
    try:
        # L55: Возвращает результат: HTTP, WebSocket и состояние флота.
        return math.isfinite(value)
    # L56: Обрабатывает исключение.
    except OverflowError:
        # L57: Возвращает результат: HTTP, WebSocket и состояние флота.
        return False

# L59: Общая модель наследует проверку Pydantic и задаёт единые строгие настройки.
class StrictModel(BaseModel):
    # L60: Запрещает неизвестные поля JSON и вещественные NaN/Infinity.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

# L62: Модель тела запроса для управления роботом.
class Command(StrictModel):
    # L63: type принимает только один из шести перечисленных типов команд.
    type: Literal["move", "park", "stop", "manual", "resume", "clear_error"]
    # L64: target — ID цели или None; длина ограничена 32 символами.
    target: str | None = Field(default=None, max_length=32)
    # L65: Линейная скорость ручного управления, по умолчанию 0, диапазон ±0,8 м/с.
    linear: float = Field(default=0, ge=-0.8, le=0.8)
    # L66: Угловая скорость из API, по умолчанию 0, диапазон ±1,5 рад/с.
    angular: float = Field(default=0, ge=-1.5, le=1.5)

# L68: Модель запроса на создание новой сцены.
class Reset(StrictModel):
    # L69: Разрешены две карты, по умолчанию normal.
    map: Literal["normal", "hardcore"] = "normal"
    # L70: Число роботов — целое от 1 до 5; парковки дополнительно проверяются при reset.
    robots: int = Field(default=3, ge=1, le=5)

# L72: Модель переключения сценария случайных постоянных заданий.
class DarkFactory(StrictModel):
    # L73: enabled — обязательный булев флаг включения режима.
    enabled: bool

# L75: Модель частичного изменения искусственных отказов.
class Fault(StrictModel):
    # L76: None означает не менять stuck; True/False — включить/выключить.
    stuck: bool | None = None
    # L77: Аналогичное необязательное поле потери связи.
    link_lost: bool | None = None
    # L78: Аналогичное поле замедления связи.
    throttle: bool | None = None

# L80: Runtime связывает транспорт, диспетчер, снимки UI и фоновый цикл управления.
class FleetRuntime:
    # L81: Конструктор принимает транспорт и параметры начальной сцены.
    def __init__(self, transport, map_name="normal", robots=3):
        # L82: Сохраняет транспорт; в рабочем запуске это RosTransport.
        self.transport = transport
        # L83: Хранит только последние 100 событий, ограничивая рост памяти.
        self.events = deque(maxlen=100)
        # L84: Фоновый управляющий цикл ещё не запущен.
        self.running = False
        # L85: Счётчик выполненных тактов контроллера.
        self.tick_count = 0
        # L86: Длительность последнего такта в секундах.
        self.tick_seconds = 0.0
        # L87: Время последней принятой телеметрии, нужно для health-check.
        self.last_received = 0.0
        # L88: Изначально ключ временного окна кэша неизвестен: первая выдача построит снимок.
        self._snapshot_cache_key = None
        # L89: Готовая JSON-строка ещё отсутствует; хранится только одна версия, без истории.
        self._snapshot_cache = None
        # L90: Создаёт начальную сцену через общий механизм reset.
        self.reset(map_name, robots)

    # L92: Сбрасывает общий JSON-кэш после изменения состояния, не меняя физику или диспетчер.
    def invalidate_snapshot(self):
        # L93: Удаляет ключ старого окна: следующая выдача не сможет принять его за свежий.
        self._snapshot_cache_key = None
        # L94: Освобождает ссылку на устаревшую JSON-строку до следующего формирования снимка.
        self._snapshot_cache = None

    # L96: Определяет метод event: структурированное событие UTC и журнал интерфейса.
    def event(self, message, level="info", *, code="OPERATOR_EVENT", robot_id=None, reason=""):
        # L97: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
        level = level.lower()
        # L98: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        # L99: Вызывает операцию: структурированное событие UTC и журнал интерфейса.
        self.events.append({"time": timestamp, "level": level, "message": message,
                            # L100: Продолжает условие или выражение: структурированное событие UTC и журнал интерфейса.
                            "code": code, "robot_id": robot_id, "reason": reason})
        # L101: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
        log_reason = str(reason or message).replace("\r", "\\r").replace("\n", "\\n")
        # L102: Выбирает уровень Python logging по строковому имени.
        LOG.log({"info": logging.INFO, "warning": logging.WARNING,
                 # L103: Продолжает условие или выражение: структурированное событие UTC и журнал интерфейса.
                 "error": logging.ERROR}.get(level, logging.INFO),
                # L104: Продолжает условие или выражение: структурированное событие UTC и журнал интерфейса.
                "%s | %s | %s | %s | %s", timestamp, level.upper(), robot_id or "-", code, log_reason,
                # L105: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
                extra={"event_code": code, "robot_id": robot_id, "event_reason": reason})
        # L106: Новое событие меняет журнал снимка, поэтому прежнюю общую JSON-версию нужно сбросить.
        self.invalidate_snapshot()

    # L108: Определяет метод drain_dispatcher_events: структурированное событие UTC и журнал интерфейса.
    def drain_dispatcher_events(self):
        # L109: Перебирает элементы: структурированное событие UTC и журнал интерфейса.
        for change in self.dispatcher.drain_events():
            # L110: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
            code = change["code"]
            # L111: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
            robot_id = change.get("robot_id")
            # L112: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
            reason = change.get("reason", "")
            # L113: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
            message = EVENT_NAMES.get(code, code)
            # L114: Проверяет условие: структурированное событие UTC и журнал интерфейса.
            if robot_id:
                # L115: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
                message = f"Робот {robot_id.removeprefix('robot')}: {message}"
            # L116: Проверяет условие: структурированное событие UTC и журнал интерфейса.
            if reason:
                # L117: Вызывает операцию: структурированное событие UTC и журнал интерфейса.
                message += f": {reason}"
            # L118: Вызывает операцию: структурированное событие UTC и журнал интерфейса.
            self.event(message, change.get("level", "info"), code=code,
                       # L119: Сохраняет значение: структурированное событие UTC и журнал интерфейса.
                       robot_id=robot_id, reason=reason)

    # L121: Создаёт новую логическую сцену диспетчера и запрашивает reset симулятора.
    def reset(self, map_name, robots):
        # L122: Загружает выбранную карту.
        graph = load_map(map_name)
        # L123: Генератор списка выбирает ID узлов с признаком парковки.
        parking = [node.id for node in graph.nodes.values() if node.parking]
        # L124: Проверяет, хватит ли парковок на запрошенное число роботов.
        if not 1 <= robots <= len(parking):
            # L125: Объясняет ошибку конфигурации с названием карты и числом парковок.
            raise ValueError(f"На карте «{MAP_NAMES[map_name]}» доступно парковок: {len(parking)}")
        # L126: Сохраняет новый граф.
        self.graph = graph
        # L127: Преобразует неизменную карту в словарь один раз на сцену; последующие снимки его используют.
        self._map_snapshot = graph.to_dict()
        # L128: Формирует robot1, robot2, …; нумерация для пользователя начинается с единицы.
        self.robot_ids = [f"robot{i + 1}" for i in range(robots)]
        # L129: Создаёт новый диспетчер с чистыми целями и резервами.
        self.dispatcher = Dispatcher(graph, self.robot_ids)
        # L130: Уникальный world_id отсекает сообщения старой сцены.
        self.world_id = uuid.uuid4().hex
        # L131: Номер reset основан на системном времени в наносекундах и упорядочивает reset-команды.
        self.reset_seq = time.time_ns()
        # L132: Очищает последние принятые состояния роботов.
        self.raw_states = {}
        # L133: Начинает таблицу искусственных отказов новой сцены.
        self.faults = {
            # L134: Каждый робот получает три выключенных отказа.
            robot_id: {"stuck": False, "link_lost": False, "throttle": False}
            # L135: Генератор словаря перебирает все ID нового парка.
            for robot_id in self.robot_ids
        # L136: Завершает словарь отказов.
        }
        # L137: Минус бесконечность разрешает немедленно отправить первый reset.
        self._last_reset = float("-inf")
        # L138: Пока не пришли состояния всех роботов, сцена считается неготовой.
        self._reset_pending = True
        # L139: Сценарий «Тёмный цех» в новой сцене выключен.
        self._dark = False
        # L140: Вызывает операцию: HTTP, WebSocket и состояние флота.
        self.event(f"Новая симуляция: {MAP_NAMES[map_name]}, роботов: {robots}",
                   # L141: Сохраняет значение: HTTP, WebSocket и состояние флота.
                   code="WORLD_RESET", reason=f"map={map_name}, robots={robots}")

    # L143: Общий выход команд в транспорт.
    def send(self, command):
        # L144: **command копирует поля приказа, а world_id привязывает его к текущей сцене.
        self.transport.send({**command, "world_id": self.world_id})

    # L146: Определяет метод _valid_telemetry: проверка полной и согласованной телеметрии.
    def _valid_telemetry(self, state):
        # L147: Сохраняет значение: проверка полной и согласованной телеметрии.
        sequence = state.get("seq")
        # L148: Сохраняет значение: проверка полной и согласованной телеметрии.
        boot_id = state.get("boot_id")
        # L149: Проверяет условие: проверка полной и согласованной телеметрии.
        if (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
                # L150: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                or not isinstance(boot_id, str) or not 1 <= len(boot_id) <= 128
                # L151: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                or not _finite_number(state.get("sim_time"))):
            # L152: Возвращает результат: проверка полной и согласованной телеметрии.
            return False
        # L153: Сохраняет значение: проверка полной и согласованной телеметрии.
        pose, velocity = state.get("pose"), state.get("velocity")
        # L154: Проверяет условие: проверка полной и согласованной телеметрии.
        if not isinstance(pose, dict) or not isinstance(velocity, dict):
            # L155: Возвращает результат: проверка полной и согласованной телеметрии.
            return False
        # L156: Проверяет условие: проверка полной и согласованной телеметрии.
        if (not all(_finite_number(pose.get(key)) for key in ("x", "y", "yaw"))
                # L157: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                or not all(_finite_number(velocity.get(key)) for key in ("linear", "angular"))):
            # L158: Возвращает результат: проверка полной и согласованной телеметрии.
            return False
        # L159: Сохраняет значение: проверка полной и согласованной телеметрии.
        x, y = pose["x"], pose["y"]
        # L160: Проверяет условие: проверка полной и согласованной телеметрии.
        if not (ROBOT_RADIUS <= x <= self.graph.width - ROBOT_RADIUS
                # L161: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                and ROBOT_RADIUS <= y <= self.graph.height - ROBOT_RADIUS):
            # L162: Возвращает результат: проверка полной и согласованной телеметрии.
            return False
        # L163: Проверяет условие: проверка полной и согласованной телеметрии.
        if (not isinstance(state.get("status"), str)
                # L164: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                or state["status"] not in {"idle", "moving", "paused", "manual", "error"}):
            # L165: Возвращает результат: проверка полной и согласованной телеметрии.
            return False
        # L166: Сохраняет значение: проверка полной и согласованной телеметрии.
        node = state.get("node_id")
        # L167: Проверяет условие: проверка полной и согласованной телеметрии.
        if node is not None:
            # L168: Проверяет условие: проверка полной и согласованной телеметрии.
            if not isinstance(node, str) or node not in self.graph.nodes:
                # L169: Возвращает результат: проверка полной и согласованной телеметрии.
                return False
            # L170: Сохраняет значение: проверка полной и согласованной телеметрии.
            point = self.graph.nodes[node]
            # L171: Проверяет условие: проверка полной и согласованной телеметрии.
            if math.hypot(x - point.x, y - point.y) > NODE_TOLERANCE + 1e-9:
                # L172: Возвращает результат: проверка полной и согласованной телеметрии.
                return False
        # L173: Сохраняет значение: проверка полной и согласованной телеметрии.
        edge = state.get("edge")
        # L174: Проверяет условие: проверка полной и согласованной телеметрии.
        if edge is not None:
            # L175: Проверяет условие: проверка полной и согласованной телеметрии.
            if (not isinstance(edge, (list, tuple)) or len(edge) != 2
                    # L176: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                    or not all(isinstance(item, str) and item in self.graph.nodes for item in edge)
                    # L177: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                    or edge[1] not in self.graph.adjacency[edge[0]]):
                # L178: Возвращает результат: проверка полной и согласованной телеметрии.
                return False
            # L179: Сохраняет значение: проверка полной и согласованной телеметрии.
            start, end = (self.graph.nodes[item] for item in edge)
            # L180: Проверяет условие: проверка полной и согласованной телеметрии.
            if (segment_distance(x, y, start.x, start.y, end.x, end.y) > NODE_TOLERANCE + 1e-9
                    # L181: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                    or node is not None and node not in edge):
                # L182: Возвращает результат: проверка полной и согласованной телеметрии.
                return False
        # L183: Сохраняет значение: проверка полной и согласованной телеметрии.
        order = state.get("current_order")
        # L184: Проверяет условие: проверка полной и согласованной телеметрии.
        if order is not None:
            # L185: Проверяет условие: проверка полной и согласованной телеметрии.
            if (not isinstance(order, dict) or not isinstance(order.get("command_id"), str)
                    # L186: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                    or not order["command_id"] or not isinstance(order.get("type"), str)
                    # L187: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                    or order["type"] not in {"move", "manual"}):
                # L188: Возвращает результат: проверка полной и согласованной телеметрии.
                return False
            # L189: Проверяет условие: проверка полной и согласованной телеметрии.
            if order["type"] == "move" and (edge is None or order.get("path") != list(edge)):
                # L190: Возвращает результат: проверка полной и согласованной телеметрии.
                return False
        # L191: Сохраняет значение: проверка полной и согласованной телеметрии.
        error = state.get("error")
        # L192: Проверяет условие: проверка полной и согласованной телеметрии.
        if error is not None and (not isinstance(error, dict)
                                  # L193: Продолжает условие или выражение: проверка полной и согласованной телеметрии.
                                  or not isinstance(error.get("code"), str) or not error["code"]):
            # L194: Возвращает результат: проверка полной и согласованной телеметрии.
            return False
        # L195: Возвращает результат: проверка полной и согласованной телеметрии.
        return True

    # L197: Один управляющий такт: принять состояния, обновить диспетчер, отправить приказы.
    def step(self, now):
        # L198: Начинает измерение длительности такта высокоточным таймером.
        start = time.perf_counter()
        # L199: Забирает из транспорта последние состояния роботов.
        for state in self.transport.receive():
            # L200: Проверяет условие: HTTP, WebSocket и состояние флота.
            if not isinstance(state, dict) or state.get("world_id") != self.world_id:
                # L201: continue пропускает телеметрию старого мира.
                continue
            # L202: Читает ID робота из состояния.
            robot_id = state.get("robot_id")
            # L203: Принимает сообщения только от роботов заданного парка.
            if robot_id not in self.robot_ids:
                # L204: Игнорирует неизвестный ID.
                continue
            # L205: Проверяет условие: HTTP, WebSocket и состояние флота.
            if not self._valid_telemetry(state):
                # L206: Пропускает непригодное сообщение.
                continue
            # L207: Диспетчер дополнительно проверяет корректность и историю перезапусков.
            if not self.dispatcher.ingest(state, now):
                # L208: Не сохраняет состояние, отвергнутое диспетчером.
                continue
            # L209: Сохраняет последнее принятое состояние робота.
            self.raw_states[robot_id] = state
            # L210: Фиксирует свежесть принятой телеметрии по монотонным часам.
            self.last_received = now
        # L211: Проверяет, получено ли хотя бы по состоянию от всех роботов.
        if len(self.raw_states) == len(self.robot_ids):
            # L212: После полного начального набора сцена готова принимать команды.
            self._reset_pending = False
        # L213: Пока подтверждения reset нет, повторяет его каждые 0,5 с.
        if self._reset_pending and now - self._last_reset >= 0.5:
            # L214: Начинает отправку reset через общий выход с world_id.
            self.send(
                # L215: Команда несёт тип reset, имя карты и размер парка.
                {"type": "reset", "map": self.graph.name, "robots": len(self.robot_ids),
                 # L216: Передаёт неизменную ревизию текущего reset, чтобы повтор был идемпотентным.
                 "reset_seq": self.reset_seq}
            # L217: Завершает отправку reset.
            )
            # L218: Запоминает время последней попытки reset для интервала повторения.
            self._last_reset = now
        # L219: Получает разрешённые на этом такте команды диспетчера.
        for command in self.dispatcher.tick(now):
            # L220: Отправляет каждую команду в ROS-транспорт.
            self.send(command)
        # L221: Вызывает операцию: HTTP, WebSocket и состояние флота.
        self.drain_dispatcher_events()
        # L222: Увеличивает счётчик тактов управления.
        self.tick_count += 1
        # L223: Сохраняет длительность последнего такта для метрик.
        self.tick_seconds = time.perf_counter() - start
        # L224: После такта с новой телеметрией, целями и счётчиками прежняя JSON-версия уже неактуальна.
        self.invalidate_snapshot()

    # L226: async def объявляет корутину фонового контроллера.
    async def run(self):
        # L227: Ставит флаг работающего управляющего цикла.
        self.running = True
        # L228: try/finally гарантирует очистку флага даже при отмене корутины.
        try:
            # L229: Бесконечный цикл выполняется до отмены задачи при остановке сервера.
            while True:
                # L230: Передаёт монотонное время текущему такту; оно не зависит от перевода системных часов.
                self.step(time.monotonic())
                # L231: await отдаёт управление другим задачам на 0,1 с, а не блокирует сервер.
                await asyncio.sleep(0.1)
        # L232: finally выполняется при любом способе выхода из цикла.
        finally:
            # L233: Снимает флаг работоспособности контроллера.
            self.running = False

    # L235: Публичный snapshot возвращает свежий независимый словарь; изменения вызывающего не затронут карту и мир.
    def snapshot(self, now=None):
        # L236: Глубоко копирует внутренний снимок вместе с вложенными коллекциями для безопасного внешнего чтения.
        return copy.deepcopy(self._snapshot(now))

    # L238: Возвращает общую сериализованную JSON-версию для WebSocket, избегая повторной сборки каждому клиенту.
    def snapshot_json(self, now=None):
        # L239: Берёт монотонное время либо переданное тестом; оно определяет окно и давность телеметрии.
        now = time.monotonic() if now is None else now
        # L240: Целочисленный номер окна по 0,1 с: переход окна обновляет снимок даже без новых ROS-сообщений.
        key = int(now / SNAPSHOT_PERIOD)
        # L241: Перестраивает JSON только для нового окна или после явного сброса кэша.
        if self._snapshot_cache_key != key:
            # L242: Начинает однократную сериализацию внутреннего снимка и сохраняет итоговую строку.
            self._snapshot_cache = json.dumps(
                # L243: Строит свежие поля; сохраняет русский текст, убирает лишние пробелы и запрещает NaN/Infinity в JSON.
                self._snapshot(now), ensure_ascii=False, separators=(",", ":"), allow_nan=False
            # L244: Завершает вызов сериализации общей JSON-версии.
            )
            # L245: Связывает сохранённую строку с текущим временным окном.
            self._snapshot_cache_key = key
        # L246: Возвращает ту же неизменяемую строку всем клиентам этой версии, не копируя словарь карты.
        return self._snapshot_cache

    # L248: Определяет метод metrics_snapshot: счётчики событий и время планирования.
    def metrics_snapshot(self, now=None):
        # L249: Сохраняет значение: счётчики событий и время планирования.
        now = time.monotonic() if now is None else now
        # L250: Начинает новый небольшой словарь текущих счётчиков.
        return {
            # L251: Копирует счётчики диспетчера распаковкой словаря, не отдавая наружу изменяемый оригинал.
            **self.dispatcher.metrics,
            # L252: Продолжает условие или выражение: счётчики событий и время планирования.
            "offline_robots": sum(now - record.received_at > self.dispatcher.heartbeat_timeout
                                  # L253: Перебирает элементы: счётчики событий и время планирования.
                                  for record in self.dispatcher.robots.values()),
            # L254: Добавляет число выполненных тактов управляющего цикла.
            "controller_ticks": self.tick_count,
            # L255: Переводит длительность последнего такта в миллисекунды и округляет до трёх знаков.
            "controller_tick_ms": round(self.tick_seconds * 1000, 3),
        # L256: Завершает возвращаемый словарь метрик.
        }

    # L258: Определяет метод health: здоровье контроллера, транспорта и телеметрии.
    def health(self, now=None):
        # L259: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        now = time.monotonic() if now is None else now
        # L260: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        dispatch = self.dispatcher.snapshot(now)
        # L261: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        entries = list(dispatch["robots"].values())
        # L262: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        online = sum(bool(robot.get("online")) for robot in entries)
        # L263: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        probe = getattr(self.transport, "is_healthy", None)
        # L264: Начинает операцию с обработкой ошибок.
        try:
            # L265: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
            transport_healthy = bool(probe()) if callable(probe) else self.running
        # L266: Обрабатывает исключение.
        except (RuntimeError, AttributeError):
            # L267: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
            transport_healthy = False
        # L268: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        age = max(0.0, now - self.last_received) if self.raw_states else None
        # L269: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        recent = age is not None and age < 3
        # L270: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        ready = self.running and transport_healthy and not self._reset_pending and recent
        # L271: Возвращает результат: здоровье контроллера, транспорта и телеметрии.
        return {
            # L272: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
            "ok": bool(ready), "world_id": self.world_id, "controller": "running" if self.running else "stopped",
            # L273: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
            "transport": "ROS2 Jazzy",
            # L274: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
            "service": {"controller_running": self.running, "transport_healthy": transport_healthy,
                        # L275: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
                        "telemetry_recent": recent, "telemetry_age": age,
                        # L276: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
                        "initializing": self._reset_pending},
            # L277: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
            "fleet": {"total": len(self.robot_ids), "online": online,
                      # L278: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
                      "offline": len(self.robot_ids) - online,
                      # L279: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
                      "recovering": sum(robot.get("online", False)
                                        # L280: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
                                        and robot.get("link_state") == "RECOVERING" for robot in entries),
                      # L281: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
                      "errors": sum(bool(state.get("error")) for state in self.raw_states.values()),
                      # L282: Продолжает условие или выражение: здоровье контроллера, транспорта и телеметрии.
                      "safety_stop": bool(dispatch.get("safety_stop") or online < len(self.robot_ids))},
        # L283: Завершает выражение.
        }

    # L285: Внутренний построитель объединяет актуальное состояние с сохранённой картой; наружу его словарь не отдаётся напрямую.
    def _snapshot(self, now=None):
        # L286: При отсутствии now берёт текущие монотонные часы.
        now = time.monotonic() if now is None else now
        # L287: Получает цели, резервы, свежесть связи и метрики диспетчера.
        dispatch = self.dispatcher.snapshot(now)
        # L288: Извлекает список или словарь записей роботов.
        entries = dispatch.get("robots", [])
        # L289: Поддерживает словарное представление записей диспетчера.
        if isinstance(entries, dict):
            # L290: Преобразует ID → поля в список объектов с явным полем id.
            entries = [{"id": key, **value} for key, value in entries.items()]
        # L291: Строит индекс записей по ID для быстрого объединения с телеметрией.
        by_id = {entry.get("id", entry.get("robot_id")): entry for entry in entries}
        # L292: Готовит список представлений роботов для браузера.
        robots = []
        # L293: Обрабатывает каждого настроенного робота, даже если состояния ещё нет.
        for robot_id in self.robot_ids:
            # L294: Берёт последнее сырое состояние или пустой словарь.
            state = self.raw_states.get(robot_id, {})
            # L295: Берёт диспетчерские поля этого робота или пустой словарь.
            control = by_id.get(robot_id, {})
            # L296: Извлекает pose — координаты и угол.
            pose = state.get("pose", {})
            # L297: Извлекает velocity — скорости робота.
            velocity = state.get("velocity", {})
            # L298: Активный приказ или пустой словарь, если приказа нет.
            current = state.get("current_order") or {}
            # L299: Выбирает конечную цель задачи; иначе последний узел текущего перехода.
            target = control.get("goal") or (current.get("path") or [None])[-1]
            # L300: По умолчанию UI-приказ отсутствует.
            order = None
            # L301: Создаёт описание приказа, если известен активный переход или конечная цель.
            if current or target:
                # L302: Начинает объект приказа для браузера.
                order = {
                    # L303: Копирует ID текущего перехода; до его выдачи значение может быть None.
                    "id": current.get("command_id"),
                    # L304: Передаёт выбранную цель.
                    "target": target,
                    # L305: Передаёт тип приказа, по умолчанию move для ещё не выданной цели.
                    "type": current.get("type", "move"),
                    # L306: Отражает общую паузу диспетчера в статусе приказа.
                    "status": "paused" if dispatch.get("paused") else "active",
                # L307: Завершает объект приказа.
                }
            # L308: Пока телеметрии нет, статус робота — connecting.
            status = state.get("status", "connecting")
            # L309: При общей паузе idle заменяется более понятным для UI paused.
            if dispatch.get("paused") and status == "idle":
                # L310: Устанавливает отображаемый статус паузы.
                status = "paused"
            # L311: Сохраняет значение: доступность и защитная остановка флота.
            online = control.get("online", False)
            # L312: Сохраняет значение: доступность и защитная остановка флота.
            link_state = control.get("link_state", "ONLINE") if online else "OFFLINE"
            # L313: Сохраняет значение: доступность и защитная остановка флота.
            manual_active = dispatch.get("manual_robot") == robot_id and status == "manual"
            # L314: Сохраняет значение: доступность и защитная остановка флота.
            availability = (link_state if link_state != "ONLINE" else "ERROR" if state.get("error")
                            # L315: Вызывает операцию: доступность и защитная остановка флота.
                            else "PAUSED" if (dispatch.get("paused") and not manual_active)
                            # L316: Продолжает условие или выражение: доступность и защитная остановка флота.
                            or dispatch.get("safety_stop")
                            # L317: Вызывает операцию: доступность и защитная остановка флота.
                            else "ONLINE")
            # L318: Добавляет объект одного робота в выходной список.
            robots.append(
                # L319: Начинает словарь данных робота.
                {
                    # L320: **control переносит поля целей, свежести и резервов диспетчера.
                    **control,
                    # L321: Явно указывает ID робота.
                    "id": robot_id,
                    # L322: Координата X в метрах; до телеметрии — ноль.
                    "x": pose.get("x", 0),
                    # L323: Координата Y в метрах.
                    "y": pose.get("y", 0),
                    # L324: theta — угол yaw робота в радианах.
                    "theta": pose.get("yaw", 0),
                    # L325: v — линейная скорость в м/с.
                    "v": velocity.get("linear", 0),
                    # L326: omega — угловая скорость в рад/с.
                    "omega": velocity.get("angular", 0),
                    # L327: Отображаемое состояние с учётом общей паузы.
                    "state": status,
                    # L328: Продолжает условие или выражение: доступность и защитная остановка флота.
                    "connected": online,
                    # L329: Продолжает условие или выражение: доступность и защитная остановка флота.
                    "link_state": link_state,
                    # L330: Продолжает условие или выражение: доступность и защитная остановка флота.
                    "availability": availability,
                    # L331: Продолжает условие или выражение: доступность и защитная остановка флота.
                    "recovery_reason": control.get("recovery_reason", ""),
                    # L332: Продолжает условие или выражение: доступность и защитная остановка флота.
                    "recovery_ready": control.get("recovery_ready", False),
                    # L333: Продолжает условие или выражение: доступность и защитная остановка флота.
                    "position_stale": link_state != "ONLINE",
                    # L334: last_seen — давность последней принятой телеметрии в секундах.
                    "last_seen": control.get("age"),
                    # L335: Добавляет подготовленный объект приказа или None.
                    "order": order,
                    # L336: Копирует согласованный маршрут этого робота для отображения.
                    "route": list(self.dispatcher._schedule.get(robot_id, [])),
                    # L337: Передаёт текущую ошибку модели.
                    "error": state.get("error"),
                    # L338: Текущий узел либо None посреди ребра.
                    "node_id": state.get("node_id"),
                    # L339: Концы занимаемого ребра либо None.
                    "edge": state.get("edge"),
                    # L340: Добавляет настройки имитации отказов для панели управления.
                    "fault": self.faults[robot_id],
                # L341: Завершает объект робота.
                }
            # L342: Завершает append и переходит к следующему роботу.
            )
        # L343: Возвращает единый JSON-совместимый снимок мира.
        return {
            # L344: type позволяет браузеру распознать сообщение snapshot.
            "type": "snapshot",
            # L345: revision совпадает со счётчиком управляющих тактов.
            "revision": self.tick_count,
            # L346: Добавляет календарную отметку UTC в ISO-формате.
            "time": datetime.now(timezone.utc).isoformat(),
            # L347: Идентификатор сцены позволяет браузеру сбросить старые следы и данные.
            "world_id": self.world_id,
            # L348: Подставляет заранее подготовленную карту текущей сцены вместо повторного graph.to_dict().
            "map": self._map_snapshot,
            # L349: Добавляет уже собранный список роботов.
            "robots": robots,
            # L350: Передаёт состояние сценария постоянных случайных заданий.
            "dark_factory": self._dark,
            # L351: Продолжает условие или выражение: доступность и защитная остановка флота.
            "metrics": self.metrics_snapshot(now),
            # L352: Превращает ограниченную очередь событий в JSON-список.
            "events": list(self.events),
            # L353: Передаёт остальные поля диспетчера без повторного списка роботов.
            "dispatcher": {key: value for key, value in dispatch.items() if key != "robots"},
        # L354: Завершает возвращаемый снимок.
        }

# L356: Фабрика приложения допускает передать транспорт и параметры явно.
def create_app(transport=None, map_name=None, robots=None):
    # L357: При обычном запуске создаёт ROS-транспорт; явно переданный транспорт сохраняет.
    transport = RosTransport() if transport is None else transport
    # L358: Начинает создание общего runtime приложения.
    runtime = FleetRuntime(
        # L359: Передаёт выбранный транспорт.
        transport,
        # L360: Имя карты берёт из аргумента, затем окружения; запасное значение normal.
        map_name or os.getenv("WAREHOUSE_MAP", "normal"),
        # L361: Число роботов берёт из аргумента либо окружения; запасное значение 3.
        robots if robots is not None else int(os.getenv("WAREHOUSE_ROBOTS", "3")),
    # L362: Завершает создание runtime.
    )

    # L364: Декоратор делает корутину менеджером запуска и завершения приложения.
    @asynccontextmanager
    # L365: lifespan вызывается FastAPI на весь срок жизни сервера.
    async def lifespan(app):
        # L366: Создаёт ROS-ноду, publisher, subscriber и поток обмена.
        transport.start()
        # L367: Запускает корутину контроллера как отдельную асинхронную задачу.
        task = asyncio.create_task(runtime.run(), name="fleet-controller")
        # L368: try/finally обеспечивает завершение задачи и транспорта при остановке сервера.
        try:
            # L369: yield передаёт управление серверу до момента завершения приложения.
            yield
        # L370: Начинает гарантированное освобождение ресурсов.
        finally:
            # L371: Просит фоновую задачу контроллера остановиться.
            task.cancel()
            # L372: Ожидает фактическое завершение задачи после отмены.
            try:
                # L373: await передаёт управление задаче до её завершения или исключения отмены.
                await task
            # L374: Ловит ожидаемый сигнал штатной отмены фоновой задачи.
            except asyncio.CancelledError:
                # L375: pass ничего не делает: отмена здесь является нормальным завершением.
                pass
            # L376: Останавливает ROS-executor, поток и контекст.
            transport.close()

    # L378: Создаёт FastAPI-сервер с названием API и указанным lifecycle.
    app = FastAPI(title="DiKom · Симуляция склада", version="1.0.0", lifespan=lifespan)
    # L379: Сохраняет runtime в состоянии приложения для доступа при диагностике.
    app.state.runtime = runtime

    # L381: Регистрирует собственный перевод ошибок проверки входного запроса.
    @app.exception_handler(RequestValidationError)
    # L382: Обработчик получает исходный запрос и исключение валидации.
    async def validation_error(request, exc):
        # L383: Начинает словарь понятных названий полей.
        names = {
            # L384: Подписи полей карты, размера парка и типа команды.
            "map": "Карта", "robots": "Число роботов", "type": "Команда",
            # L385: Подписи цели и линейной скорости.
            "target": "Точка назначения", "linear": "Линейная скорость",
            # L386: Подписи угловой скорости и переключателя режима.
            "angular": "Угловая скорость", "enabled": "Состояние режима",
            # L387: Подписи застревания и потери связи.
            "stuck": "Застревание", "link_lost": "Потеря связи",
            # L388: Подпись имитации замедления канала.
            "throttle": "Замедление связи",
        # L389: Завершает таблицу названий полей.
        }
        # L390: Начинает перевод кодов ошибок Pydantic на русский.
        messages = {
            # L391: missing означает, что обязательное поле не передали.
            "missing": "обязательное поле",
            # L392: extra_forbidden означает неизвестное лишнее поле.
            "extra_forbidden": "неизвестное поле",
            # L393: literal_error означает значение вне разрешённого перечня.
            "literal_error": "выберите одно из допустимых значений",
            # L394: Переводы неверного типа и разбора целого числа.
            "int_type": "нужно целое число", "int_parsing": "нужно целое число",
            # L395: Отдельная ошибка дробного числа там, где требуется целое.
            "int_from_float": "нужно целое число",
            # L396: Переводы неверного типа или разбора вещественного числа.
            "float_type": "нужно число", "float_parsing": "нужно число",
            # L397: Ошибка типа булева поля.
            "bool_type": "нужно значение «да» или «нет»",
            # L398: Ошибка преобразования булева значения.
            "bool_parsing": "нужно значение «да» или «нет»",
            # L399: Переводы неверной строки и превышения допустимой длины.
            "string_type": "нужна строка", "string_too_long": "слишком длинная строка",
            # L400: Перевод запрета NaN и бесконечных чисел.
            "finite_number": "нужно конечное число",
            # L401: Ошибка синтаксиса самого JSON.
            "json_invalid": "некорректный формат JSON",
            # L402: Ошибка формата тела, если вместо объекта передали другое значение.
            "model_attributes_type": "нужен объект с параметрами",
        # L403: Завершает словарь переводов ошибок.
        }
        # L404: Готовит список подробностей всех ошибок запроса.
        details = []
        # L405: Обрабатывает каждую обнаруженную Pydantic ошибку.
        for error in exc.errors():
            # L406: Получает путь к полю ошибки, например body → robots.
            location = error.get("loc", ())
            # L407: Выбирает имя конечного поля; для всего тела используется body.
            field = location[-1] if location and isinstance(location[-1], str) else "body"
            # L408: Переводит имя поля, подставляя общее название для неизвестного.
            label = names.get(field, "Запрос" if field == "body" else "Поле запроса")
            # L409: Читает машинный код ошибки.
            kind = error["type"]
            # L410: Получает параметры проверки, например минимально допустимое число.
            context = error.get("ctx", {})
            # L411: Ветка нарушения нижней границы значения.
            if kind == "greater_than_equal":
                # L412: Формирует сообщение с фактическим допустимым минимумом ge.
                message = f"значение должно быть не меньше {context['ge']}"
            # L413: Ветка нарушения верхней границы значения.
            elif kind == "less_than_equal":
                # L414: Формирует сообщение с допустимым максимумом le.
                message = f"значение должно быть не больше {context['le']}"
            # L415: Для остальных кодов использует таблицу переводов.
            else:
                # L416: Неизвестный код получает нейтральное понятное описание.
                message = messages.get(kind, "некорректное значение")
            # L417: Добавляет путь, русское сообщение и код одной ошибки в список detail.
            details.append({"loc": list(location), "msg": f"{label}: {message}", "type": kind})
        # L418: Возвращает HTTP 422 с JSON-описанием ошибок валидации.
        return JSONResponse(status_code=422, content={"detail": details})

    # L420: Регистрирует GET главной страницы.
    @app.get("/")
    # L421: Обработчик index отдаёт HTML интерфейса.
    def index():
        # L422: Возвращает index.html из каталога static файлом.
        return FileResponse(STATIC / "index.html")

    # L424: Регистрирует endpoint проверки готовности приложения.
    @app.get("/api/health")
    # L425: Определяет метод health: здоровье контроллера, транспорта и телеметрии.
    async def health():
        # L426: Сохраняет значение: здоровье контроллера, транспорта и телеметрии.
        status = runtime.health()
        # L427: Возвращает результат: здоровье контроллера, транспорта и телеметрии.
        return JSONResponse(status, status_code=200 if status["ok"] else 503)

    # L429: Регистрирует GET для полного текущего снимка.
    @app.get("/api/state")
    # L430: Асинхронный обработчик чтения состояния.
    async def state():
        # L431: Возвращает снимок, собранный runtime.
        return runtime.snapshot()

    # L433: Регистрирует POST создания новой сцены.
    @app.post("/api/reset")
    # L434: request уже проверен по модели Reset.
    async def reset(request: Reset):
        # L435: Параметры карты могут не соответствовать числу её парковок.
        try:
            # L436: Меняет логическую сцену и запускает повторяемый ROS reset.
            runtime.reset(request.map, request.robots)
        # L437: Ловит ошибку допустимости конфигурации сцены.
        except ValueError as exc:
            # L438: Превращает её в HTTP 422, сохранив исходную причину исключения.
            raise HTTPException(422, str(exc)) from exc
        # L439: Подтверждает принятие reset и возвращает новый world_id.
        return {"ok": True, "world_id": runtime.world_id}

    # L441: Регистрирует POST управления роботом; robot_id берётся из URL.
    @app.post("/api/robots/{robot_id}/commands")
    # L442: Тело запроса проверяет Command, идентификатор адресата — обработчик.
    async def command(robot_id: str, request: Command):
        # L443: Проверяет наличие робота в текущем парке.
        if robot_id not in runtime.robot_ids:
            # L444: Возвращает HTTP 404 для неизвестного адресата.
            raise HTTPException(404, "Робот не найден")
        # L445: Проверяет, завершено ли начальное получение телеметрии.
        if runtime._reset_pending:
            # L446: HTTP 409 показывает, что сцена пока не готова принимать команды.
            raise HTTPException(409, "Ожидаем начальную телеметрию роботов")
        # L447: Перемещение требует непустую цель; для остальных команд она необязательна.
        if request.type == "move" and not request.target:
            # L448: Возвращает HTTP 422 при отсутствии target у move.
            raise HTTPException(422, "Для перемещения выберите точку назначения")
        # L449: Вызов диспетчера может отклонить цель или текущий режим работы.
        try:
            # L450: Передаёт команду в Dispatcher.submit, который сохраняет задачу или меняет режим.
            result = runtime.dispatcher.submit(
                # L451: Передаёт адресата приказа.
                robot_id,
                # L452: Передаёт тип действия.
                request.type,
                # L453: Передаёт конечную цель движения по имени аргумента.
                target=request.target,
                # L454: Передаёт запрошенную ручную линейную скорость.
                linear=request.linear,
                # L455: Передаёт запрошенную угловую скорость.
                angular=request.angular,
                # L456: Добавляет монотонную отметку получения операторской команды.
                now=time.monotonic(),
            # L457: Завершает вызов диспетчера.
            )
            # L458: Принятая команда меняет цели или режимы; немедленно сбрасывает JSON, включая частые ручные команды.
            runtime.invalidate_snapshot()
            # L459: Вызывает операцию: HTTP, WebSocket и состояние флота.
            runtime.drain_dispatcher_events()
        # L460: Ловит неверную цель, конфликт назначения или неизвестные данные.
        except (ValueError, KeyError) as exc:
            # L461: Возвращает HTTP 409 с причиной невозможности принять действие.
            raise HTTPException(409, str(exc)) from exc
        # L462: Частые ручные команды не засоряют событийный журнал.
        if request.type != "manual":
            # L463: Получает русское название принятого действия.
            action = COMMAND_NAMES[request.type]
            # L464: Для движения дополняет описание конечной целью.
            if request.type == "move":
                # L465: Добавляет ID целевого узла к сообщению.
                action += f" к узлу {request.target}"
            # L466: Вызывает операцию: HTTP, WebSocket и состояние флота.
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {action}",
                          # L467: Сохраняет значение: HTTP, WebSocket и состояние флота.
                          code="COMMAND_ACCEPTED", robot_id=robot_id, reason=request.type)
        # L468: HTTP-успех означает принятие задачи; прибытие подтверждается последующей телеметрией.
        return {"ok": True, "result": result}

    # L470: Регистрирует POST изменения искусственных отказов конкретного робота.
    @app.post("/api/robots/{robot_id}/faults")
    # L471: Тело Fault содержит только переданные переключатели отказов.
    async def fault(robot_id: str, request: Fault):
        # L472: Проверяет наличие адресата в текущей сцене.
        if robot_id not in runtime.robot_ids:
            # L473: Неизвестный робот получает HTTP 404.
            raise HTTPException(404, "Робот не найден")
        # L474: Сохраняет значение: HTTP, WebSocket и состояние флота.
        requested = request.model_dump(exclude_none=True)
        # L475: Сохраняет значение: HTTP, WebSocket и состояние флота.
        changes = {name: enabled for name, enabled in requested.items()
                   # L476: Проверяет условие: HTTP, WebSocket и состояние флота.
                   if runtime.faults[robot_id][name] != enabled}
        # L477: Вызывает операцию: HTTP, WebSocket и состояние флота.
        runtime.faults[robot_id].update(requested)
        # L478: Изменение имитации отказов сразу делает общий снимок устаревшим.
        runtime.invalidate_snapshot()
        # L479: Отправляет симулятору полный актуальный набор флагов через ROS.
        runtime.send({"type": "fault", "robot_id": robot_id, **runtime.faults[robot_id]})
        # L480: Записывает событие только при наличии переданных изменений.
        if changes:
            # L481: Начинает соединять описания нескольких флагов через точку с запятой.
            description = "; ".join(
                # L482: Формирует понятный текст включения или выключения одного отказа.
                f"{FAULT_NAMES[name]} — {'включено' if enabled else 'выключено'}"
                # L483: Генератор перебирает изменённые поля и их значения.
                for name, enabled in changes.items()
            # L484: Завершает формирование описания отказов.
            )
            # L485: Вызывает операцию: HTTP, WebSocket и состояние флота.
            runtime.event(f"Робот {robot_id.removeprefix('robot')}: {description}", "warning",
                          # L486: Сохраняет значение: HTTP, WebSocket и состояние флота.
                          code="FAULT_INJECTION_CHANGED", robot_id=robot_id, reason=description)
        # L487: Подтверждает обработку изменения отказов.
        return {"ok": True}

    # L489: Регистрирует POST переключения сценария «Тёмный цех».
    @app.post("/api/dark-factory")
    # L490: enabled берётся из проверенной модели DarkFactory.
    async def dark_factory(request: DarkFactory):
        # L491: Сохраняет значение: HTTP, WebSocket и состояние флота.
        changed = runtime._dark != request.enabled
        # L492: Меняет режим назначения случайных целей в диспетчере.
        runtime.dispatcher.set_dark_factory(request.enabled)
        # L493: Сохраняет флаг для снимков браузера.
        runtime._dark = request.enabled
        # L494: Проверяет условие: HTTP, WebSocket и состояние флота.
        if changed:
            # L495: Вызывает операцию: HTTP, WebSocket и состояние флота.
            runtime.event(f"Тёмный цех: {'включён' if request.enabled else 'выключен'}",
                          # L496: Сохраняет значение: HTTP, WebSocket и состояние флота.
                          code="DARK_FACTORY_CHANGED", reason=f"enabled={request.enabled}")
        # L497: Подтверждает принятие нового состояния режима.
        return {"ok": True}

    # L499: Регистрирует GET текстовых метрик в формате Prometheus.
    @app.get("/metrics", response_class=PlainTextResponse)
    # L500: Обработчик преобразует числовые счётчики в строки.
    async def metrics():
        # L501: Читает только метрики: опрос мониторинга не создаёт полный снимок с картой и маршрутами.
        values = runtime.metrics_snapshot()
        # L502: Начинает список строк ответа.
        lines = []
        # L503: Перебирает имя и значение каждой метрики.
        for key, value in values.items():
            # L504: Публикует только числовые значения.
            if isinstance(value, (int, float)):
                # L505: Заменяет неподходящие символы имени на подчёркивания.
                safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in key)
                # L506: Добавляет строку с общим префиксом warehouse_ и числовым значением.
                lines.append(f"warehouse_{safe_key} {float(value)}")
        # L507: Соединяет строки с переводами строк и задаёт текстовый формат экспозиции метрик.
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # L509: Регистрирует WebSocket-канал постоянных снимков по /ws.
    @app.websocket("/ws")
    # L510: Обработчик получает объект двунаправленного соединения WebSocket.
    async def websocket(socket: WebSocket):
        # L511: Подтверждает открытие WebSocket браузеру.
        await socket.accept()
        # L512: Ошибки закрытия или медленного клиента обрабатываются как завершение соединения.
        try:
            # L513: Пока соединение работает, постоянно отправляет новые снимки.
            while True:
                # L514: Отправляет общую JSON-строку этому клиенту; его отдельное ожидание ограничено двумя секундами.
                await asyncio.wait_for(socket.send_text(runtime.snapshot_json()), timeout=2)
                # L515: Между снимками ждёт 0,2 с: частота примерно 5 Гц без учёта времени отправки.
                await asyncio.sleep(0.2)
        # L516: Ловит отключение клиента, тайм-аут отправки или закрытый сокет.
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            # L517: Нормально завершает обработчик при закрытии канала.
            pass

    # L519: Подключает раздачу статических файлов по URL /static.
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # L520: Возвращает полностью настроенный экземпляр FastAPI.
    return app

# L522: Точка входа консольной команды dispatcher из setup.py.
def main(args=None):
    # L523: Импортирует HTTP-сервер Uvicorn только при реальном запуске процесса.
    import uvicorn

    # L525: Настраивает вывод INFO-событий без технического префикса.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # L526: Читает порт из окружения, запасное значение — 8000.
    port = int(os.getenv("WAREHOUSE_PORT", "8000"))
    # L527: Выводит адрес запуска в журнал процесса.
    LOG.info("Запуск веб-интерфейса: http://localhost:%s", port)
    # L528: Запускает Uvicorn на всех интерфейсах контейнера; Compose ограничивает внешний доступ localhost.
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="warning", access_log=False)

# L530: Проверяет, запущен ли файл напрямую, а не импортирован модулем.
if __name__ == "__main__":
    # L531: Вызывает точку входа при прямом запуске файла.
    main()
