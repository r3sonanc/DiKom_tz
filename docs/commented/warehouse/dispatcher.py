# dict — словарь, set — множество, list — список; -> — тип результата, | None — значение может отсутствовать.
# _ в начале имени отмечает внутреннюю деталь; comprehension строит коллекцию через for и if.

# Самый важный файл тестового. Отвечает за распределение задач, совместное планирование маршрутов,
# ожидания, резервирование занятых участков и «Тёмный цех».
# L1: В Python 3.12 сохраняет аннотации типов как строки, позволяя отложить их вычисление.
from __future__ import annotations

# L3: deque — двусторонняя очередь для быстрого BFS по графу.
from collections import deque
# L4: dataclass описывает запись данных, field задаёт отдельные изменяемые значения по умолчанию.
from dataclasses import dataclass, field
# L5: Подключает операции минимальной кучи — очереди приоритетов A*.
from heapq import heappop, heappush
# L6: permutations перебирает порядки приоритетов роботов.
from itertools import permutations
# L7: math даёт бесконечность и проверки конечности чисел.
import math
# L8: random нужен для воспроизводимого выбора целей «Тёмного цеха».
import random
# L9: Any обозначает произвольный тип значения в аннотациях.
from typing import Any

# L11: Декоратор автоматически создаёт конструктор записи состояния робота.
@dataclass
# L12: RobotRecord хранит последнее состояние одного робота и метаданные связи.
class RobotRecord:
    # L13: state — телеметрия; default_factory создаёт отдельный словарь каждому роботу.
    state: dict[str, Any] = field(default_factory=dict)
    # L14: received_at — время приёма; минус бесконечность означает отсутствие данных.
    received_at: float = -math.inf
    # L15: seq — номер последнего состояния; -1 позволяет принять первое сообщение с seq=0.
    seq: int = -1
    # L16: boot_id — ID запуска робота; None означает, что он ещё неизвестен.
    boot_id: str | None = None
    # L17: ID прошлых запусков защищают от запоздалой телеметрии до перезапуска.
    retired_boots: set[str] = field(default_factory=set)

# L19: CooperativePlanner согласует маршруты роботов во времени.
class CooperativePlanner:
    # L20: Конструктор принимает карту и общий лимит раскрытий состояний: 300 тысяч.
    def __init__(self, warehouse_map: Any, max_expansions: int = 300_000):
        # L21: Сохраняет карту для доступа к узлам и соседям.
        self.map = warehouse_map
        # L22: Запоминает ограничение вычислительной работы планировщика.
        self.max_expansions = max_expansions
        # L23: Обнуляет число раскрытых состояний последнего планирования.
        self.last_expansions = 0
        # L24: Обнуляет число проверенных порядков роботов.
        self.last_attempts = 0
        # L25: reason будет объяснять причину отсутствия плана.
        self.reason = ""
        # L26: Для каждого начального узла таблица хранит расстояния до остальных.
        self._distances: dict[str, dict[str, int]] = {}
        # L27: Запускает BFS от каждого узла карты.
        for source in self.map.nodes:
            # L28: Расстояние от начального узла до себя равно нулю.
            distances = {source: 0}
            # L29: Помещает начальный узел в очередь BFS.
            queue = deque([source])
            # L30: Продолжает обход, пока очередь непуста.
            while queue:
                # L31: Забирает самый ранний добавленный узел за постоянное время.
                node = queue.popleft()
                # L32: Перебирает его соседей по разрешённым рёбрам.
                for neighbor in self.map.adjacency[node]:
                    # L33: Посещает соседа только при первом обнаружении.
                    if neighbor not in distances:
                        # L34: В невзвешенном графе сосед на одно ребро дальше текущего узла.
                        distances[neighbor] = distances[node] + 1
                        # L35: Добавляет найденного соседа в конец очереди.
                        queue.append(neighbor)
            # L36: Сохраняет найденные расстояния от текущего источника.
            self._distances[source] = distances

    # L38: distance возвращает кратчайшее расстояние в рёбрах между ID узлов.
    def distance(self, source: str, destination: str) -> float:
        # L39: При неизвестном источнике или недостижимой цели возвращает бесконечность.
        return self._distances.get(source, {}).get(destination, math.inf)

    # L41: plan принимает начальные и целевые узлы каждого робота.
    def plan(self, starts: dict[str, str], goals: dict[str, str],
             # L42: blocked — запрещённые узлы; результат — маршруты либо None при неудаче.
             blocked: set[str] | None = None) -> dict[str, list[str]] | None:
        # L43: Сбрасывает счётчики работы для нового планирования.
        self.last_expansions = self.last_attempts = 0
        # L44: Копирует запреты в множество; при None создаёт пустое множество.
        blocked = set(blocked or ())
        # L45: Убирает причину ошибки предыдущего поиска.
        self.reason = ""
        # L46: Проверяет уникальность стартов, сравнивая число разных узлов с числом роботов.
        if len(set(starts.values())) != len(starts):
            # L47: Запоминает причину отказа: два робота стоят на одном узле.
            self.reason = "Два робота сообщают об одном и том же узле"
            # L48: Возвращает отсутствие плана при конфликте начальных позиций.
            return None
        # L49: Проверяет, что конечные цели всех роботов различны.
        if len(set(goals.values())) != len(goals):
            # L50: Сохраняет объяснение конфликта конечных целей.
            self.reason = "Точки назначения роботов должны различаться"
            # L51: Прекращает планирование при совпавших целях.
            return None
        # L52: Проверяет, не входит ли хотя бы один старт в запреты.
        if any(node in blocked for node in starts.values()):
            # L53: Запоминает, что начальная позиция заблокирована.
            self.reason = "Начальный узел заблокирован"
            # L54: Не строит план из запрещённой позиции.
            return None
        # L55: Проверяет достижимость целей по заранее вычисленным расстояниям.
        if any(math.isinf(self.distance(starts[r], goals[r])) for r in starts):
            # L56: Запоминает, что одна из целей недостижима по графу.
            self.reason = "Точка назначения недоступна"
            # L57: Завершает поиск заведомо невозможной задачи.
            return None
        # L58: Стабильно сортирует роботов: сначала тех, кому действительно нужно ехать.
        robot_ids = sorted(starts, key=lambda r: (starts[r] == goals[r], r))
        # L59: Ограничивает число шагов размером карты и квадратом числа роботов.
        horizon = 3 * len(self.map.nodes) + len(robot_ids) ** 2
        # L60: Перебирает перестановки приоритетов, меняя порядок построения путей.
        for order in permutations(robot_ids):
            # L61: Увеличивает число проверенных порядков.
            self.last_attempts += 1
            # L62: Создаёт пустой набор путей для текущего порядка роботов.
            planned: dict[str, list[str]] = {}
            # L63: Обходит роботов по очереди вместе с индексами их приоритетов.
            for index, robot_id in enumerate(order):
                # L64: Собирает старты роботов, чьи пути ещё не спланированы.
                unplanned = {starts[r] for r in order[index + 1:]}
                # L65: Вычисляет срок начальной защиты стартов оставшихся роботов.
                initial_hold = (len(order) - index - 1) * len(order)
                # L66: Ищет путь текущего робота с учётом ранее спланированных путей.
                path = self._search(starts[robot_id], goals[robot_id], planned,
                                    # L67: Передаёт блокировки, ещё занятые старты, срок их защиты и горизонт.
                                    blocked, unplanned, initial_hold, horizon)
                # L68: Проверяет, удалось ли найти путь очередному роботу.
                if path is None:
                    # L69: При неудаче бросает этот порядок и пробует другой.
                    break
                # L70: Сохраняет путь, чтобы следующие роботы учитывали его ресурсы.
                planned[robot_id] = path
            # L71: else у for выполняется без break: пути всем роботам найдены.
            else:
                # L72: Находит максимальную длину пути; для пустого набора использует 1.
                length = max(map(len, planned.values()), default=1)
                # L73: Дополняет короткие пути ожиданием в цели до общей длины расписания.
                return {r: p + [p[-1]] * (length - len(p)) for r, p in planned.items()}
            # L74: После неудачного порядка проверяет общий бюджет раскрытий.
            if self.last_expansions >= self.max_expansions:
                # L75: Объясняет исчерпание лимита: разрешать движение без плана нельзя.
                self.reason = "Лимит поиска исчерпан; роботы остаются на месте"
                # L76: Возвращает отсутствие плана при исчерпании бюджета.
                return None
        # L77: Сообщает, что перебор не дал план в пределах горизонта.
        self.reason = "Безопасный маршрут в пределах глубины поиска не найден"
        # L78: Возвращает отсутствие согласованных маршрутов.
        return None

    # L80: _search ищет путь одного робота с учётом путей более приоритетных роботов.
    def _search(self, start: str, goal: str, planned: dict[str, list[str]],
                # L81: Принимает постоянные блокировки и временно защищённые чужие старты.
                blocked: set[str], unplanned: set[str], initial_hold: int,
                # L82: horizon ограничивает число шагов; результат — список узлов или None.
                horizon: int) -> list[str] | None:
        # L83: На каждом шаге создаёт множество занятых узлов с постоянными запретами.
        occupied: list[set[str]] = [set(blocked) for _ in range(horizon + 1)]
        # L84: Перебирает пути уже спланированных роботов.
        for path in planned.values():
            # L85: Добавляет их резервы на каждый шаг горизонта.
            for time in range(1, horizon + 1):
                # L86: Резервирует исходный конец перехода, удерживая последнюю позицию после прибытия.
                occupied[time].update((path[min(time - 1, len(path) - 1)],
                                       # L87: Резервирует конечный конец перехода, запрещая встречное движение и тесное следование.
                                       path[min(time, len(path) - 1)]))
        # L88: На первых шагах защищает старты ещё не спланированных роботов.
        for time in range(1, min(initial_hold, horizon) + 1):
            # L89: Добавляет эти старты в занятые узлы соответствующего шага.
            occupied[time].update(unplanned)
        # L90: Проверяет постоянную блокировку самой цели.
        if goal in blocked:
            # L91: Сразу отказывается от пути к постоянно занятой цели.
            return None
        # L92: Ищет последний шаг, на котором цель нужна чужому пути.
        latest_goal_use = max((t for t in range(1, horizon + 1)
                               # L93: Если цель никому не нужна, считает этот шаг нулевым.
                               if goal in occupied[t]), default=0)
        # L94: parents хранит предка состояния (узел, шаг); у старта предка нет.
        parents: dict[tuple[str, int], tuple[str, int] | None] = {(start, 0): None}
        # L95: Создаёт очередь A*: оценка полной длины, текущий шаг и узел.
        queue: list[tuple[float, int, str]] = [(self.distance(start, goal), 0, start)]
        # L96: Продолжает поиск, пока очередь состояний непуста.
        while queue:
            # L97: Извлекает состояние с минимальной оценкой; _ обозначает ненужный приоритет.
            _, time, node = heappop(queue)
            # L98: Учитывает раскрытие состояния в общем бюджете планирования.
            self.last_expansions += 1
            # L99: Проверяет превышение разрешённого числа раскрытий.
            if self.last_expansions > self.max_expansions:
                # L100: Прерывает поиск при превышении бюджета.
                return None
            # L101: Принимает цель, только если она уже не понадобится чужому пути.
            if node == goal and time >= latest_goal_use:
                # L102: Создаёт список для восстановления пути от конца к началу.
                path = []
                # L103: Начинает восстановление с достигнутого состояния цели.
                cursor: tuple[str, int] | None = (node, time)
                # L104: Идёт по предкам, пока не дойдёт до старта без предка.
                while cursor is not None:
                    # L105: Добавляет ID очередного узла в обратный путь.
                    path.append(cursor[0])
                    # L106: Переходит к состоянию, из которого пришёл сюда.
                    cursor = parents[cursor]
                # L107: Срез с шагом -1 разворачивает путь от старта к цели.
                return path[::-1]
            # L108: Не расширяет горизонт и не выходит из узла, занятого на следующем шаге.
            if time >= horizon or node in occupied[time + 1]:
                # L109: Пропускает такое состояние и берёт следующее из очереди.
                continue
            # L110: Рассматривает соседние узлы и текущий: последний вариант означает ожидание.
            for neighbor in (*self.map.adjacency[node], node):
                # L111: Новое состояние — кандидат в узел и следующий шаг времени.
                state = (neighbor, time + 1)
                # L112: Отбрасывает занятые узлы и уже рассмотренные сочетания узла со временем.
                if neighbor in occupied[time + 1] or state in parents:
                    # L113: Переходит к другому варианту перехода.
                    continue
                # L114: Использует статическое расстояние кандидата до цели как эвристику A*.
                distance = self.distance(neighbor, goal)
                # L115: Проверяет достижимость и возможность уложиться в горизонт.
                if math.isinf(distance) or time + 1 + distance > horizon:
                    # L116: Не добавляет заведомо неподходящее состояние.
                    continue
                # L117: Запоминает предка нового состояния для восстановления пути.
                parents[state] = (node, time)
                # L118: Добавляет состояние в кучу с оценкой: сделанные шаги плюс расстояние до цели.
                heappush(queue, (time + 1 + distance, time + 1, neighbor))
        # L119: При пустой очереди сообщает отсутствие пути.
        return None

# L121: Dispatcher хранит задачи и выдаёт согласованные переходы роботов.
class Dispatcher:
    # L122: Конструктор получает карту и список либо кортеж известных ID роботов.
    def __init__(self, warehouse_map: Any, robot_ids: list[str] | tuple[str, ...] = (),
                 # L123: Пороги: свежесть телеметрии 1,5 с, lease движения 0,9 с.
                 heartbeat_timeout: float = 1.5, command_lease: float = 0.9,
                 # L124: seed задаёт повторяемую случайность для воспроизведения сценариев.
                 seed: int = 7):
        # L125: Сохраняет карту для проверки целей и резервов.
        self.map = warehouse_map
        # L126: Создаёт запись телеметрии каждому известному роботу.
        self.robots = {r: RobotRecord() for r in robot_ids}
        # L127: Запоминает фиксированный состав: ID извне затем отклоняются.
        self._configured_ids = bool(robot_ids)
        # L128: goals сопоставляет ID робота конечной цели задания.
        self.goals: dict[str, str] = {}
        # L129: Отмечает случайные задачи отдельно от целей оператора.
        self._auto_goals: set[str] = set()
        # L130: Создаёт планировщик совместных маршрутов для карты.
        self.planner = CooperativePlanner(warehouse_map)
        # L131: Запоминает допустимый возраст телеметрии.
        self.heartbeat_timeout = heartbeat_timeout
        # L132: Запоминает срок автоматической команды без продления.
        self.command_lease = command_lease
        # L133: При запуске «Тёмный цех» выключен.
        self.dark_factory = False
        # L134: Явная пауза оператора при запуске выключена.
        self.paused = False
        # L135: Начальная причина ожидания — ещё нет состояний роботов.
        self.reason = "Ожидание телеметрии"
        # L136: Создаёт собственный генератор случайных чисел с заданным seed.
        self._random = random.Random(seed)
        # L137: Начинает нумерацию новых команд с нуля.
        self._serial = 0
        # L138: _round хранит выданные переходы без подтверждённого завершения.
        self._round: dict[str, dict[str, Any]] = {}
        # L139: _held хранит аварийные переходы, чьи ресурсы нельзя освобождать.
        self._held: dict[str, dict[str, Any]] = {}
        # L140: _recover отмечает запросы восстановления после сброса ошибки.
        self._recover: set[str] = set()
        # L141: _schedule хранит согласованные последовательности узлов.
        self._schedule: dict[str, list[str]] = {}
        # L142: Признак устаревания расписания пока снят: задач нет.
        self._schedule_dirty = False
        # L143: _outbox — очередь разовых команд до ближайшего tick.
        self._outbox: list[dict[str, Any]] = []
        # L144: Ручной робот пока не выбран.
        self._manual_robot: str | None = None
        # L145: Начальные линейная и угловая ручные скорости равны нулю.
        self._manual_velocity = (0.0, 0.0)
        # L146: Ручных обновлений ещё не было: минус бесконечность сразу делает их устаревшими.
        self._manual_updated = -math.inf
        # L147: Автоматическое движение ещё не отправлялось.
        self._last_motion_sent = -math.inf
        # L148: Кэширует stop-команды для безопасных повторов одного ID.
        self._stop_commands: dict[str, dict[str, Any]] = {}
        # L149: _frozen отмечает временную заморозку из-за паузы или несвежей связи.
        self._frozen = False
        # L150: Подпись предыдущей задачи планирования пока отсутствует.
        self._last_plan_signature: Any = None
        # L151: Предыдущей попытки планирования ещё не было.
        self._last_plan_at = -math.inf
        # L152: Создаёт счётчики принятой и отброшенной телеметрии.
        self.metrics = {"accepted_telemetry": 0, "discarded_telemetry": 0,
                        # L153: Добавляет счётчики команд, завершённых переходов и запусков поиска.
                        "commands": 0, "completed_moves": 0, "plans": 0,
                        # L154: Добавляет счётчики неудач планирования, выполненных задач и поэтапных планов.
                        "planning_failures": 0, "completed_tasks": 0, "staged_plans": 0}

    # L156: ingest принимает состояние и сообщает, удалось ли его принять.
    def ingest(self, state: dict[str, Any], now: float) -> bool:
        # L157: Извлекает ID робота без ошибки при отсутствии ключа.
        robot_id = state.get("robot_id")
        # L158: Требует строковый ID и при фиксированном составе запрещает чужих роботов.
        if not isinstance(robot_id, str) or (self._configured_ids and robot_id not in self.robots):
            # L159: Отклоняет сообщение с неподходящим ID.
            return False
        # L160: В режиме без фиксированного состава проверяет появление нового робота.
        if robot_id not in self.robots:
            # L161: Регистрирует нового робота с пустой записью.
            self.robots[robot_id] = RobotRecord()
        # L162: Берёт запись для обновления этой телеметрией.
        robot = self.robots[robot_id]
        # L163: Достаёт номер сообщения и ID запуска робота.
        seq, boot_id = state.get("seq"), state.get("boot_id")
        # L164: Проверяет целый seq без bool и строковый boot_id.
        if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(boot_id, str):
            # L165: Отклоняет состояние с некорректными метаданными.
            return False
        # L166: Отбрасывает прошлый запуск, повторный или уменьшившийся seq текущего запуска.
        if boot_id in robot.retired_boots or (boot_id == robot.boot_id and seq <= robot.seq):
            # L167: Учитывает отбрасывание повторной или устаревшей телеметрии.
            self.metrics["discarded_telemetry"] += 1
            # L168: Не заменяет актуальные данные устаревшим сообщением.
            return False
        # L169: Обнаруживает перезапуск по смене известного boot_id.
        if robot.boot_id is not None and boot_id != robot.boot_id:
            # L170: Запоминает прошлый boot_id для отбрасывания запоздалых сообщений.
            robot.retired_boots.add(robot.boot_id)
            # L171: После перезапуска включает явную паузу всего парка.
            self.paused = True
            # L172: Просит оператора проверить положение перезапущенного робота.
            self.reason = f"Робот {robot_id} перезапущен; проверьте его положение перед возобновлением"
            # L173: Считает прежнее расписание недействительным после перезапуска.
            self._schedule_dirty = True
        # L174: Копирует принятый словарь состояния.
        robot.state = dict(state)
        # L175: Сохраняет местное время приёма, номер сообщения и ID запуска.
        robot.received_at, robot.seq, robot.boot_id = now, seq, boot_id
        # L176: Увеличивает число успешно принятых состояний.
        self.metrics["accepted_telemetry"] += 1
        # L177: True подтверждает принятие телеметрии диспетчером.
        return True

    # L179: submit получает ID робота, тип команды или её словарь и необязательную цель.
    def submit(self, robot_id: str, kind: str | dict[str, Any], target: str | None = None,
               # L180: Принимает ручные скорости и время; возвращает подтверждение назначения.
               linear: float = 0.0, angular: float = 0.0, now: float | None = None) -> dict[str, Any]:
        # L181: Проверяет, что робот известен диспетчеру.
        if robot_id not in self.robots:
            # L182: При неизвестном ID вызывает исключение для ответа API с ошибкой.
            raise ValueError(f"Неизвестный робот: {robot_id}")
        # L183: Позволяет передать словарь вместо строки типа команды.
        if isinstance(kind, dict):
            # L184: Сохраняет словарь отдельно для извлечения его полей.
            payload = kind
            # L185: Повторно вызывает submit с выделенными типом и целью.
            return self.submit(robot_id, payload.get("type", ""), payload.get("target"),
                               # L186: Передаёт скорости по умолчанию 0 и сохраняет время вызова.
                               payload.get("linear", 0.0), payload.get("angular", 0.0), now)
        # L187: Обрабатывает поездку и парковку как задачи с конечной точкой.
        if kind in {"move", "park"}:
            # L188: Для park нужно выбрать конечную парковку самостоятельно.
            if kind == "park":
                # L189: Объединяет чужие физические резервы и назначенные цели в занятые узлы.
                occupied = set(self.goals.values()) | self._occupied_by_others(robot_id)
                # L190: Берёт текущий узел; посреди ребра он может быть None.
                source = self.robots[robot_id].state.get("node_id")
                # L191: Начинает выбор парковочных узлов из карты.
                candidates = [n for n, node in self.map.nodes.items()
                              # L192: Оставляет парковки вне занятого множества.
                              if node.parking and n not in occupied]
                # L193: Проверяет наличие свободного кандидата в парковку.
                if not candidates:
                    # L194: Отказывает, если все парковочные узлы заняты.
                    raise ValueError("Нет свободной парковки")
                # L195: Из текущего узла выбирает ближайшую парковку; при равенстве сравнивает ID.
                target = min(candidates, key=lambda n: (self.planner.distance(source, n), n))
            # L196: Проверяет существование конечного узла.
            if target not in self.map.nodes:
                # L197: Отказывает в поездке к неизвестному ID цели.
                raise ValueError(f"Неизвестная точка назначения: {target}")
            # L198: Проверяет, не назначена ли эта цель другому роботу.
            if target in {g for r, g in self.goals.items() if r != robot_id}:
                # L199: Запрещает двум задачам закончиться на одном узле.
                raise ValueError("Эта точка назначения уже назначена другому роботу")
            # L200: Сохраняет конечную цель до её фактического достижения.
            self.goals[robot_id] = target
            # L201: Снимает отметку случайной задачи после операторского назначения.
            self._auto_goals.discard(robot_id)
            # L202: Новая цель делает прежнее расписание устаревшим.
            self._schedule_dirty = True
        # L203: Обрабатывает операторскую остановку парка.
        elif kind == "stop":
            # L204: Включает явную паузу до resume.
            self.paused = True
            # L205: Снимает выбор ручного робота при общей остановке.
            self._manual_robot = None
            # L206: Сбрасывает кэш для формирования свежих stop-команд.
            self._stop_commands.clear()
            # L207: Сохраняет причину явной паузы.
            self.reason = "Парк остановлен оператором"
        # L208: Обрабатывает возобновление автоматического движения.
        elif kind == "resume":
            # L209: Проверяет безопасность по всем роботам парка.
            for rid, robot in self.robots.items():
                # L210: Берёт последнее состояние очередного робота.
                state = robot.state
                # L211: Проверяет несброшенную ошибку.
                if state.get("error"):
                    # L212: Требует сбросить ошибку до возобновления.
                    raise ValueError(f"Перед возобновлением сбросьте ошибку у робота {rid}")
                # L213: Вне узла допускает только сохранённый автоматический переход, исключая ручного робота.
                if state.get("node_id") is None and (rid not in self._round or rid == self._manual_robot):
                    # L214: Требует вернуть свободно перемещённого робота к узлу.
                    raise ValueError(f"Перед возобновлением верните робота {rid} к навигационному узлу")
            # L215: При выходе из ручного режима очищает прежние переходы выбранного робота.
            if self._manual_robot is not None:
                # L216: Удаляет ручного робота из текущего раунда, если он там был.
                self._round.pop(self._manual_robot, None)
                # L217: Удаляет его аварийно удерживаемый переход, если он был.
                self._held.pop(self._manual_robot, None)
            # L218: Снимает явную паузу всего парка.
            self.paused = False
            # L219: Завершает выделенное ручное управление.
            self._manual_robot = None
            # L220: Удаляет старые stop-команды после возобновления.
            self._stop_commands.clear()
            # L221: Требует пересчитать маршруты для текущего положения и задач.
            self._schedule_dirty = True
        # L222: Обрабатывает запрос ручных скоростей.
        elif kind == "manual":
            # L223: Не разрешает выбрать второго ручного робота одновременно.
            if self._manual_robot not in (None, robot_id):
                # L224: Объясняет ограничение ручного управления одним роботом.
                raise ValueError("Ручное управление доступно только для одного робота одновременно")
            # L225: Проверяет обе скорости на числовой тип и конечность без NaN/Infinity.
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (linear, angular)):
                # L226: Отклоняет некорректные ручные скорости.
                raise ValueError("Скорости ручного управления должны быть конечными числами")
            # L227: Приостанавливает автоматическое движение всего парка.
            self.paused = True
            # L228: Назначает выбранного робота единственным ручным.
            self._manual_robot = robot_id
            # L229: Ограничивает скорости: линейную до ±0,5 м/с, угловую до ±1,5 рад/с.
            self._manual_velocity = (max(-0.5, min(0.5, linear)), max(-1.5, min(1.5, angular)))
            # L230: Сохраняет время ручного обновления; при отсутствии времени tick задаст его позже.
            self._manual_updated = now if now is not None else math.inf
            # L231: Объясняет, почему остальные роботы остановились.
            self.reason = "Ручное управление: остальной парк на паузе"
            # L232: После ручного перемещения прежний автоматический план устаревает.
            self._schedule_dirty = True
        # L233: Обрабатывает запрос сброса ошибки робота.
        elif kind == "clear_error":
            # L234: Кладёт clear_error в очередь ближайшей отправки.
            self._outbox.append(self._command(robot_id, "clear_error"))
            # L235: Помечает робота кандидатом на восстановление перехода.
            self._recover.add(robot_id)
            # L236: После сброса ошибки понадобится актуальное расписание.
            self._schedule_dirty = True
        # L237: В эту ветку попадают неизвестные типы команд.
        else:
            # L238: Отклоняет неподдерживаемый тип с объяснением.
            raise ValueError(f"Неизвестный тип команды: {kind}")
        # L239: Подтверждает назначение задачи; движение ещё не обязательно выполнено.
        return {"accepted": True, "robot_id": robot_id, "type": kind, "target": target}

    # L241: Включает или выключает постоянную выдачу случайных задач.
    def set_dark_factory(self, enabled: bool) -> None:
        # L242: Приводит флаг к bool и сохраняет режим «Тёмный цех».
        self.dark_factory = bool(enabled)

    # L244: tick выполняет шаг управления и возвращает команды для транспорта.
    def tick(self, now: float) -> list[dict[str, Any]]:
        # L245: Забирает разовые команды и сразу заменяет очередь пустой.
        commands, self._outbox = self._outbox, []
        # L246: Начинает собирать ID роботов с несвежей телеметрией.
        stale = [r for r, record in self.robots.items()
                 # L247: Считает данные устаревшими после heartbeat_timeout, обычно 1,5 с.
                 if now - record.received_at > self.heartbeat_timeout]
        # L248: Проверяет ручную команду, поданную без времени.
        if self._manual_updated == math.inf:
            # L249: Привязывает такую команду ко времени текущего шага.
            self._manual_updated = now
        # L250: При несвежести хотя бы одного робота или явной паузе запрещает автоматику.
        if stale or self.paused:
            # L251: Запоминает заморозку для обновления команд после восстановления.
            self._frozen = True
            # L252: Если причина — связь, обновляет текст ожидания.
            if stale:
                # L253: Перечисляет ID роботов без свежей телеметрии.
                self.reason = "Нет свежей телеметрии: " + ", ".join(stale)
            # L254: Посылает stop всем; ручного исключает только при свежести всего парка.
            commands.extend(self._stops(exclude=self._manual_robot if not stale else None))
            # L255: Для ручного движения связь должна быть свежей и робот выбран.
            manual_ready = (not stale and self._manual_robot is not None
                            # L256: Требует выждать lease после последней автоматической отправки движения.
                            and now - self._last_motion_sent >= self.command_lease
                            # L257: Проверяет по состояниям, что остальные роботы уже стоят.
                            and all(self._stationary(r.state) for rid, r in self.robots.items()
                                    # L258: Исключает ручного робота из проверки остановки остальных.
                                    if rid != self._manual_robot))
            # L259: При выполненных условиях разрешает ручную команду.
            if manual_ready:
                # L260: Использует ручные скорости не дольше 0,35 с без обновления, затем выдаёт нули.
                velocity = self._manual_velocity if now - self._manual_updated <= 0.35 else (0.0, 0.0)
                # L261: Создаёт свежую manual-команду выбранному роботу.
                commands.append(self._command(self._manual_robot, "manual",
                                              # L262: Подставляет в неё линейную и угловую скорости.
                                              linear=velocity[0], angular=velocity[1]))
            # L263: Если ручной робот выбран, но ехать пока небезопасно, останавливает и его.
            elif self._manual_robot is not None:
                # L264: Добавляет stop только для ручного робота.
                commands.extend(self._stops(only=self._manual_robot))
            # L265: Возвращает команды паузы или ручного движения с учётом их числа.
            return self._count(commands)
        # L266: Узнаёт, нужно ли обновить ID переходов после заморозки.
        refresh_round = self._frozen
        # L267: Выполняет восстановление после замороженного шага.
        if refresh_round:
            # L268: Снимает признак временной заморозки.
            self._frozen = False
            # L269: Удаляет прежние stop-команды из кэша.
            self._stop_commands.clear()
        # L270: Проверяет отсутствие зарегистрированных роботов.
        if not self.robots:
            # L271: Показывает ожидание подключения вместо поиска пути.
            self.reason = "Ожидание подключения роботов"
            # L272: Возвращает только накопленные разовые команды.
            return commands
        # L273: При незавершённом раунде сначала проверяет его подтверждения.
        if self._round:
            # L274: Создаёт набор ещё не завершённых переходов.
            unfinished = {}
            # L275: Проверяет каждый уже разрешённый переход раунда.
            for robot_id, command in self._round.items():
                # L276: Берёт последнее состояние участника перехода.
                state = self.robots[robot_id].state
                # L277: При ошибке робота нельзя считать его участок свободным.
                if state.get("error"):
                    # L278: Переносит его переход в аварийно удерживаемые резервы.
                    self._held[robot_id] = command
                    # L279: Требует перепланировать остальных с учётом неисправности.
                    self._schedule_dirty = True
                    # L280: Дополнительно посылает неисправному роботу stop.
                    commands.extend(self._stops(only=robot_id))
                    # L281: Пропускает обычную проверку прибытия неисправного робота.
                    continue
                # L282: Для завершения перехода требует телеметрию конечного узла пути.
                arrived = (state.get("node_id") == command["path"][-1]
                           # L283: Дополнительно требует idle — отсутствие активного движения.
                           and state.get("status") == "idle"
                           # L284: Требует подтверждение конкретного ID текущей команды.
                           and (state.get("completed_command_id") == command["command_id"]
                                # L285: После заморозки допускает подтверждение актуальной позицией без старого ID.
                                or refresh_round))
                # L286: Обрабатывает подтверждённое прибытие.
                if arrived:
                    # L287: Увеличивает число завершённых переходов по рёбрам.
                    self.metrics["completed_moves"] += 1
                # L288: Иначе переход нужно оставить в раунде.
                else:
                    # L289: После заморозки создаёт новую move-команду того же ребра.
                    unfinished[robot_id] = (self._command(robot_id, "move", path=command["path"])
                                            # L290: Без заморозки повторяет прежнюю команду с тем же ID для продления lease.
                                            if refresh_round else command)
            # L291: Оставляет в текущем раунде только незавершённые переходы.
            self._round = unfinished
            # L292: Пока есть незавершённый участник, следующий шаг запрещён.
            if self._round:
                # L293: Добавляет повторы текущих переходов к выдаваемым командам.
                commands.extend(self._round.values())
                # L294: Запоминает время автоматической отправки для защиты ручного режима.
                self._last_motion_sent = now
                # L295: Возвращает команды без продвижения расписания, ожидая телеметрию.
                return self._count(commands)
            # L296: После подтверждения всего раунда продвигает пути всех роботов.
            for path in self._schedule.values():
                # L297: Проверяет наличие следующего шага в пути.
                if len(path) > 1:
                    # L298: Убирает пройденный старт, делая следующий узел текущим.
                    path.pop(0)
        # L299: Если задачи или отказы изменили план, прежнее расписание недействительно.
        if self._schedule_dirty:
            # L300: Очищает устаревшее расписание после завершения раунда.
            self._schedule = {}
            # L301: Снимает флаг устаревания перед новым поиском.
            self._schedule_dirty = False
        # L302: Стабильно перебирает запросы восстановления после ошибки.
        for robot_id in sorted(self._recover):
            # L303: Продолжает аварийный переход только после телеметрии без ошибки.
            if robot_id in self._held and not self.robots[robot_id].state.get("error"):
                # L304: Забирает переход из аварийного набора резервов.
                command = self._held.pop(robot_id)
                # L305: Создаёт новую команду того же ребра и возвращает её в раунд.
                self._round[robot_id] = self._command(robot_id, "move", path=command["path"])
        # L306: Удаляет из ожидающих восстановления тех, чей переход вернулся в раунд.
        self._recover.difference_update(self._round)
        # L307: Восстановленные переходы должны закончиться раньше нового планирования.
        if self._round:
            # L308: Добавляет восстановленные переходы к отправке.
            commands.extend(self._round.values())
            # L309: Обновляет время последней команды движения.
            self._last_motion_sent = now
            # L310: Возвращает команды восстановления без выдачи нового раунда.
            return self._count(commands)
        # L311: Перебирает копию целей для безопасного удаления выполненных задач.
        for robot_id, target in list(self.goals.items()):
            # L312: Проверяет достижение конечной цели по текущему узлу робота.
            if self.robots[robot_id].state.get("node_id") == target:
                # L313: Удаляет фактически выполненную задачу из активных целей.
                del self.goals[robot_id]
                # L314: Снимает её автоматическую отметку, если она была.
                self._auto_goals.discard(robot_id)
                # L315: Считает завершённую конечную задачу, отдельно от переходов по рёбрам.
                self.metrics["completed_tasks"] += 1
        # L316: Проверяет включение «Тёмного цеха».
        if self.dark_factory:
            # L317: Назначает новые дальние цели свободным от задач роботам.
            self._assign_random_goals(now)
        # L318: При отсутствии конечных задач движение не планирует.
        if not self.goals:
            # L319: Очищает ненужное расписание.
            self._schedule = {}
            # L320: Показывает ожидание новых задач.
            self.reason = "Ожидание заданий"
            # L321: Возвращает разовые команды без новых поездок.
            return self._count(commands)
        # L322: Если цели есть, но расписания нет, нужен поиск.
        if not self._schedule:
            # L323: Строит маршруты с учётом текущих состояний и резервов.
            self._build_schedule(now)
        # L324: Пропускает шаги общего ожидания, пока остаются переходы.
        while self._schedule and max(map(len, self._schedule.values())) > 1:
            # L325: Берёт первые два узла каждого пути и исключает ожидания на месте.
            moving = {r: p[:2] for r, p in self._schedule.items() if len(p) > 1 and p[0] != p[1]}
            # L326: Если кто-то должен ехать, формирует раунд движения.
            if moving:
                # L327: Обходит разрешённые на этом шаге независимые переходы.
                for robot_id, path in moving.items():
                    # L328: Выдаёт каждому участнику move только на ближайшее ребро.
                    self._round[robot_id] = self._command(robot_id, "move", path=path)
                # L329: Добавляет весь раунд в команды ROS-транспорту.
                commands.extend(self._round.values())
                # L330: Обновляет время последней автоматической отправки движения.
                self._last_motion_sent = now
                # L331: Сообщает о выполнении согласованных маршрутов.
                self.reason = "Выполнение согласованных маршрутов"
                # L332: Прекращает сдвиг шагов, пока этот раунд физически не закончится.
                break
            # L333: Если на шаге все стоят, сдвигает их пути без поездки.
            for path in self._schedule.values():
                # L334: Проверяет наличие ещё одного узла расписания.
                if len(path) > 1:
                    # L335: Убирает шаг общего ожидания.
                    path.pop(0)
        # L336: Проверяет, исчерпан ли план без создания нового раунда.
        if self._schedule and not self._round:
            # L337: Удаляет завершённое расписание.
            self._schedule = {}
        # L338: Учитывает количество выдаваемых команд и возвращает их.
        return self._count(commands)

    # L340: _build_schedule строит план из актуальных стартов, целей и аварийных блокировок.
    def _build_schedule(self, now: float) -> None:
        # L341: Создаёт словарь стартов роботов, которым можно планировать поездку.
        starts: dict[str, str] = {}
        # L342: Создаёт множество ресурсов, заблокированных неисправными роботами.
        blocked: set[str] = set()
        # L343: Рассматривает запись каждого робота.
        for robot_id, record in self.robots.items():
            # L344: Берёт его последнее состояние.
            state = record.state
            # L345: Читает текущий узел; между узлами он может отсутствовать.
            node = state.get("node_id")
            # L346: Ошибка, неизвестный узел или manual исключают робота из обычного поиска.
            if state.get("error") or node not in self.map.nodes or state.get("status") == "manual":
                # L347: Делает все его ресурсы запрещёнными для остальных.
                blocked.update(self._resources(robot_id))
            # L348: В противном случае робот может участвовать в планировании.
            else:
                # L349: Сохраняет подтверждённый узел как его старт.
                starts[robot_id] = node
        # L350: Оставляет цели только роботов с допустимыми стартами.
        active = {r: g for r, g in self.goals.items() if r in starts}
        # L351: Если доступных задач нет, поиск не запускается.
        if not active:
            # L352: Объясняет ожидание неисправных или остановленных роботов.
            self.reason = "Ожидание остановленных или неисправных роботов"
            # L353: Завершает построение без разрешения движения.
            return
        # L354: Подбирает конечные позиции, включая уступающих роботов без задачи.
        goals = self._choose_endpoints(starts, active, blocked)
        # L355: Проверяет возможность назначить разные конечные узлы.
        if goals is None:
            # L356: Объясняет отсутствие места для уступания.
            self.reason = "Нет свободного узла, чтобы уступить дорогу"
            # L357: Не запускает поиск при невозможном распределении конечных мест.
            return
        # L358: Формирует стабильную подпись задачи из стартов, целей и блокировок.
        signature = (tuple(sorted(starts.items())), tuple(sorted(goals.items())), tuple(sorted(blocked)))
        # L359: Не повторяет точно такой же поиск чаще раза в секунду.
        if signature == self._last_plan_signature and now - self._last_plan_at < 1.0:
            # L360: Возвращается до дорогого поиска, если попытка повторилась слишком рано.
            return
        # L361: Запоминает подпись и время текущего поиска.
        self._last_plan_signature, self._last_plan_at = signature, now
        # L362: Увеличивает метрику построений расписания.
        self.metrics["plans"] += 1
        # L363: Просит планировщик согласовать маршруты всех участников.
        schedule = self.planner.plan(starts, goals, blocked)
        # L364: После неудачи и при наличии операторских целей пробует поэтапное выполнение.
        if schedule is None and any(r not in self._auto_goals for r in active):
            # L365: Сохраняет исходный общий бюджет поиска.
            budget = self.planner.max_expansions
            # L366: Запоминает уже потраченные раскрытия и число проверенных порядков.
            expansions, attempts = self.planner.last_expansions, self.planner.last_attempts
            # L367: try гарантирует восстановление бюджета через finally.
            try:
                # L368: Предпочитает операторские задачи автоматическим при поэтапном поиске.
                for robot_id in sorted(active, key=lambda r: (r in self._auto_goals,
                                                              # L369: Затем предпочитает ближнюю цель и использует ID для стабильного порядка.
                                                              self.planner.distance(starts[r], active[r]), r)):
                    # L370: Проверяет остаток общего бюджета.
                    if expansions >= budget:
                        # L371: Прекращает попытки при исчерпании бюджета.
                        break
                    # L372: Подбирает конечные узлы ради выполнения пока одной выбранной задачи.
                    single_goals = self._choose_endpoints(starts, {robot_id: active[robot_id]}, blocked)
                    # L373: Проверяет, смогут ли остальные уступить этому роботу.
                    if single_goals is None:
                        # L374: Если уступить негде, пробует следующую одиночную задачу.
                        continue
                    # L375: Выделяет очередной попытке только остаток общего бюджета.
                    self.planner.max_expansions = budget - expansions
                    # L376: Ищет согласованные пути ради выбранной одиночной цели.
                    schedule = self.planner.plan(starts, single_goals, blocked)
                    # L377: Прибавляет раскрытия этой попытки к общим расходам.
                    expansions += self.planner.last_expansions
                    # L378: Прибавляет количество проверенных порядков к сумме попыток.
                    attempts += self.planner.last_attempts
                    # L379: Проверяет, нашёлся ли поэтапный план.
                    if schedule is not None:
                        # L380: Увеличивает метрику успешных поэтапных планов.
                        self.metrics["staged_plans"] += 1
                        # L381: Завершает перебор одиночных целей после успеха.
                        break
            # L382: finally выполняется при успехе, исключении и выходе через break.
            finally:
                # L383: Восстанавливает исходную настройку полного бюджета планировщика.
                self.planner.max_expansions = budget
                # L384: Записывает суммарные раскрытия и попытки всех поисков этого построения.
                self.planner.last_expansions, self.planner.last_attempts = expansions, attempts
        # L385: Если общий и поэтапный поиск не помогли, фиксирует неудачу.
        if schedule is None:
            # L386: Увеличивает количество неудачных построений плана.
            self.metrics["planning_failures"] += 1
            # L387: Передаёт оператору последнюю причину отказа планировщика.
            self.reason = self.planner.reason
            # L388: В «Тёмном цехе» допускает перевыбор неудачных случайных целей.
            if self.dark_factory:
                # L389: Перебирает только автоматически назначенные задачи.
                for robot_id in self._auto_goals:
                    # L390: Удаляет случайную цель, сохраняя операторские.
                    self.goals.pop(robot_id, None)
                # L391: Очищает отметки автоматических целей перед следующим выбором.
                self._auto_goals.clear()
        # L392: При успешном поиске нужно сохранить результат.
        else:
            # L393: Записывает согласованные последовательности узлов в _schedule.
            self._schedule = schedule

    # L395: _choose_endpoints подбирает конечные узлы активным и уступающим роботам.
    def _choose_endpoints(self, starts: dict[str, str], active: dict[str, str],
                          # L396: Получает блокировки; возвращает цели либо None, если уступить негде.
                          blocked: set[str]) -> dict[str, str] | None:
        # L397: Начинает с копии настоящих конечных целей активных задач.
        goals = dict(active)
        # L398: Считает конечные цели и блокировки уже занятыми местами.
        used = set(goals.values()) | blocked
        # L399: Начинает собирать узлы кратчайших маршрутов активных задач.
        active_routes = {node for rid, goal in active.items() for node in self.map.nodes
                         # L400: Считает расстояние от старта до промежуточного узла.
                         if self.planner.distance(starts[rid], node)
                         # L401: Прибавляет расстояние от промежуточного узла до цели.
                         + self.planner.distance(node, goal)
                         # L402: Равенство кратчайшему полному расстоянию означает узел на кратчайшем пути.
                         == self.planner.distance(starts[rid], goal)}
        # L403: Рассматривает старты роботов, у которых может не быть задачи.
        for robot_id, source in starts.items():
            # L404: Проверяет, назначено ли роботу конечное место.
            if robot_id in goals:
                # L405: Пропускает робота с уже заданной целью.
                continue
            # L406: Если текущий старт не занят чужой целью или блокировкой, можно остаться там.
            if source not in used:
                # L407: Назначает собственный старт как конечную позицию такого робота.
                goals[robot_id] = source
                # L408: Резервирует эту позицию от назначения другим.
                used.add(source)
        # L409: Снова обходит роботов, которым ещё не выбрали конечное место.
        for robot_id, source in starts.items():
            # L410: Проверяет наличие уже назначенной настоящей или временной цели.
            if robot_id in goals:
                # L411: Пропускает тех, чья конечная позиция уже определена.
                continue
            # L412: Ищет узлы вне занятых конечных мест, блокировок и текущих стартов.
            candidates = [n for n in self.map.nodes if n not in used and n not in starts.values()]
            # L413: Проверяет наличие свободных мест уступания.
            if not candidates:
                # L414: Возвращает None, если уступание невозможно.
                return None
            # L415: В выборе места уступания прежде всего предпочитает узлы вне активных кратчайших путей.
            target = min(candidates, key=lambda n: (n in active_routes,
                                                    # L416: Затем предпочитает близкий к уступающему роботу узел.
                                                    self.planner.distance(source, n),
                                                    # L417: При равенстве предпочитает узел с двумя и более соседями, затем сравнивает ID.
                                                    len(self.map.adjacency[n]) < 2, n))
            # L418: Назначает временную конечную позицию для уступания.
            goals[robot_id] = target
            # L419: Исключает это место из кандидатов следующих роботов.
            used.add(target)
        # L420: Возвращает разные конечные узлы для всех участников плана.
        return goals

    # L422: _assign_random_goals выдаёт задания «Тёмного цеха»; now в этом методе не используется.
    def _assign_random_goals(self, now: float) -> None:
        # L423: Собирает уже назначенные цели, исключая их повторы.
        occupied_goals = set(self.goals.values())
        # L424: Перебирает роботов в стабильном порядке ID.
        for robot_id, record in sorted(self.robots.items()):
            # L425: Берёт текущий подтверждённый узел робота.
            source = record.state.get("node_id")
            # L426: Пропускает занятого задачей, неисправного или находящегося вне узла робота.
            if robot_id in self.goals or source not in self.map.nodes or record.state.get("error"):
                # L427: Переходит к следующему участнику, если новое задание не нужно или небезопасно.
                continue
            # L428: Строит пары расстояния и ID кандидатов в новую цель.
            candidates = [(self.planner.distance(source, n), n) for n in self.map.nodes
                          # L429: Исключает уже назначенные цели и собственный текущий узел.
                          if n not in occupied_goals and n != source]
            # L430: Убирает недостижимые узлы с бесконечным расстоянием.
            candidates = [(d, n) for d, n in candidates if not math.isinf(d)]
            # L431: Проверяет наличие доступного кандидата.
            if not candidates:
                # L432: Если целей нет, оставляет робота без нового задания.
                continue
            # L433: Сортирует кандидатов от дальних к ближним.
            candidates.sort(reverse=True)
            # L434: Случайно выбирает из дальней трети; max гарантирует хотя бы одного кандидата.
            _, target = self._random.choice(candidates[:max(1, len(candidates) // 3)])
            # L435: Сохраняет выбранную конечную цель.
            self.goals[robot_id] = target
            # L436: Помечает её автоматической, чтобы разрешить перевыбор при неудаче.
            self._auto_goals.add(robot_id)
            # L437: Исключает выбранный узел из целей следующих роботов этого прохода.
            occupied_goals.add(target)
            # L438: Запрашивает новый совместный план для изменившихся задач.
            self._schedule_dirty = True

    # L440: snapshot готовит словарь состояния диспетчера для API и интерфейса.
    def snapshot(self, now: float) -> dict[str, Any]:
        # L441: Публикует флаги общей паузы и «Тёмного цеха».
        return {"paused": self.paused, "dark_factory": self.dark_factory,
                # L442: Добавляет ID ручного робота и объяснение текущего ожидания или движения.
                "manual_robot": self._manual_robot, "reason": self.reason,
                # L443: Копирует задачи и публикует отсортированные удерживаемые узлы каждого робота.
                "goals": dict(self.goals), "reservations": {r: sorted(self._resources(r)) for r in self.robots},
                # L444: Вычисляет online по возрасту телеметрии относительно heartbeat_timeout.
                "robots": {r: {"online": now - v.received_at <= self.heartbeat_timeout,
                               # L445: age — возраст принятого состояния; None означает отсутствие телеметрии.
                               "age": max(0.0, now - v.received_at) if math.isfinite(v.received_at) else None,
                               # L446: Добавляет конечную цель робота, если она назначена.
                               "goal": self.goals.get(r)} for r, v in self.robots.items()},
                # L447: Копирует числовые метрики диспетчера.
                "metrics": dict(self.metrics),
                # L448: Начинает статистику последнего поиска с числа раскрытых состояний.
                "planner": {"expansions": self.planner.last_expansions,
                            # L449: Добавляет число проверенных порядков и завершает словарь снимка.
                            "attempts": self.planner.last_attempts}}

    # L451: _resources возвращает физически или логически удерживаемые роботом узлы.
    def _resources(self, robot_id: str) -> set[str]:
        # L452: Берёт последнюю телеметрию выбранного робота.
        state = self.robots[robot_id].state
        # L453: Добавляет текущий физический узел, если он есть в карте.
        resources = {n for n in (state.get("node_id"),) if n in self.map.nodes}
        # L454: Добавляет оба конца текущего ребра, в том числе при остановке между узлами.
        resources.update(n for n in (state.get("edge") or ()) if n in self.map.nodes)
        # L455: Проверяет наличие разрешённого перехода текущего раунда.
        if robot_id in self._round:
            # L456: Добавляет оба конца выданного перехода даже до новой телеметрии.
            resources.update(self._round[robot_id]["path"])
        # L457: Проверяет наличие аварийно удерживаемого перехода.
        if robot_id in self._held:
            # L458: Сохраняет оба его конца занятыми до безопасного устранения ошибки.
            resources.update(self._held[robot_id]["path"])
        # L459: Возвращает объединённое множество резервов.
        return resources

    # L461: _occupied_by_others собирает ресурсы всех, кроме выбранного робота.
    def _occupied_by_others(self, robot_id: str) -> set[str]:
        # L462: Распаковывает чужие множества в union, объединяя занятые ими узлы.
        return set().union(*(self._resources(r) for r in self.robots if r != robot_id))

    # L464: _command создаёт словарь приказа; kwargs принимает дополнительные поля, например path.
    def _command(self, robot_id: str, kind: str, **kwargs: Any) -> dict[str, Any]:
        # L465: Увеличивает счётчик для уникального ID нового приказа.
        self._serial += 1
        # L466: Любой приказ, кроме stop, делает прежнюю кэшированную остановку устаревшей.
        if kind != "stop":
            # L467: Удаляет старую остановку, чтобы будущая stop получила свежий ID.
            self._stop_commands.pop(robot_id, None)
        # L468: Формирует адресата, ID dispatch-N, тип и дополнительные параметры команды.
        return {"robot_id": robot_id, "command_id": f"dispatch-{self._serial}", "type": kind, **kwargs}

    # L470: _stops выдаёт stop всем, кроме exclude, либо только выбранному через only.
    def _stops(self, exclude: str | None = None, only: str | None = None) -> list[dict[str, Any]]:
        # L471: Создаёт список стоп-команд для возврата вызывающему коду.
        result = []
        # L472: Перебирает известных диспетчеру роботов.
        for robot_id in self.robots:
            # L473: Исключает выбранный exclude либо всех не совпавших с only.
            if robot_id == exclude or (only is not None and robot_id != only):
                # L474: Пропускает stop для неподходящего ID.
                continue
            # L475: Проверяет наличие кэшированной остановки этого робота.
            if robot_id not in self._stop_commands:
                # L476: При первом запросе создаёт stop и сохраняет её для повторных доставок.
                self._stop_commands[robot_id] = self._command(robot_id, "stop")
            # L477: Добавляет существующую или новую остановку в список отправки.
            result.append(self._stop_commands[robot_id])
        # L478: Возвращает stop-команды выбранным роботам.
        return result

    # L480: staticmethod делает помощник независимым от конкретного self.
    @staticmethod
    # L481: _stationary проверяет по телеметрии фактическую остановку робота.
    def _stationary(state: dict[str, Any]) -> bool:
        # L482: Берёт словарь скоростей, заменяя отсутствующий пустым.
        velocity = state.get("velocity") or {}
        # L483: Требует одно из состояний без активной автоматической поездки.
        return (state.get("status") in {"idle", "paused", "manual", "error"}
                # L484: Дополнительно требует линейную скорость менее 0,001 м/с по модулю.
                and abs(velocity.get("linear", 0.0)) < 0.001
                # L485: Требует угловую скорость менее 0,001 рад/с по модулю.
                and abs(velocity.get("angular", 0.0)) < 0.001)

    # L487: _count учитывает количество выдаваемых команд и возвращает их.
    def _count(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # L488: Прибавляет длину списка, включая повторные отправки тех же ID.
        self.metrics["commands"] += len(commands)
        # L489: Возвращает исходный список для последующей публикации транспортом.
        return commands
