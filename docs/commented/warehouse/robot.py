# Здесь находится виртуальный робот: принимает команды, вычисляет движение, поворачивается,
# проверяет столкновения и отправляет телеметрию.
# WorldCore объединяет несколько роботов в один виртуальный склад.
# L1: OrderedDict — словарь с порядком вставки; нужен для ограниченной истории команд.
from collections import OrderedDict
# L2: math предоставляет тригонометрию, расстояния и проверку чисел.
import math
# L3: uuid создаёт уникальный идентификатор запуска робота.
import uuid

# L5: Импортирует тип карты, загрузчик схем и геометрическое расстояние до отрезка.
from warehouse.map import WarehouseMap, load_map, segment_distance

# L7: Длина прямоугольного корпуса в метрах.
BODY_LENGTH = 0.8
# L8: Ширина корпуса в метрах.
BODY_WIDTH = 0.6
# L9: Радиус описанной окружности: hypot вычисляет корень из суммы квадратов полусторон.
ROBOT_RADIUS = math.hypot(BODY_LENGTH / 2, BODY_WIDTH / 2)
# L10: Расстояние между ведущими колёсами в метрах.
WHEEL_TRACK = 0.5
# L11: Радиус каждого колеса в метрах.
WHEEL_RADIUS = 0.1
# L12: Ограничение линейной скорости, м/с.
MAX_LINEAR = 0.8
# L13: Ограничение скорости поворота, рад/с.
MAX_ANGULAR = 1.8
# L14: Автоматическое движение разрешено ещё 0,9 с после продления команды.
MOVE_LEASE = 0.9
# L15: Ручное движение разрешено ещё 0,35 с после команды.
MANUAL_LEASE = 0.35
# L16: Допустимое удаление от навигационного графа, м.
GRAPH_TOLERANCE = 0.6
# L17: Допуск привязки к узлу или сохранённому ребру, м.
NODE_TOLERANCE = 0.06

# L19: def объявляет функцию; аннотации float описывают аргументы и результат.
def angle_difference(target: float, current: float) -> float:
    # L20: Приводит разницу углов к диапазону от −π включительно до π невключительно.
    return (target - current + math.pi) % (2 * math.pi) - math.pi

# L22: Помощник проверяет числовое поле команды; name нужен для сообщения об ошибке.
def _number(value: object, name: str) -> float:
    # L23: isinstance проверяет тип; bool запрещён, хотя в Python он является подтипом int.
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        # L24: raise прерывает проверку исключением ValueError с именем неверного поля.
        raise ValueError(f"Поле {name} должно быть конечным числом")
    # L25: try начинает участок, где преобразование может завершиться исключением.
    try:
        # L26: Преобразует целое или вещественное число к float.
        result = float(value)
    # L27: except ловит слишком большое целое, которое невозможно представить как float.
    except OverflowError as exc:
        # L28: Выдаёт понятную ошибку; from exc сохраняет исходную причину исключения.
        raise ValueError(f"Поле {name} должно быть конечным числом") from exc
    # L29: Проверяет, что результат конечен: исключает NaN и бесконечность.
    if not math.isfinite(result):
        # L30: Отклоняет нечисловые в практическом смысле значения NaN/Infinity.
        raise ValueError(f"Поле {name} должно быть конечным числом")
    # L31: return возвращает проверенное число вызывающему коду.
    return result

# L33: class объединяет состояние и поведение одного симуляционного робота.
class RobotCore:
    # L34: __init__ инициализирует экземпляр; self — именно этот робот.
    def __init__(self, robot_id: str, warehouse_map: WarehouseMap, start_node: str):
        # L35: Проверяет наличие стартового узла в словаре карты.
        if start_node not in warehouse_map.nodes:
            # L36: Запрещает появление робота в неизвестной точке.
            raise ValueError("Неизвестный начальный узел робота")
        # L37: Сохраняет строковый идентификатор, например robot1.
        self.robot_id = robot_id
        # L38: Сохраняет ссылку на общую неизменяемую схему склада.
        self.map = warehouse_map
        # L39: Получает координаты стартового узла.
        node = self.map.nodes[start_node]
        # L40: Устанавливает позицию в узле и начальный угол π радиан, то есть 180°.
        self.x, self.y, self.yaw = node.x, node.y, math.pi
        # L41: До первой команды линейная и угловая скорости равны нулю.
        self.linear = self.angular = 0.0
        # L42: Запоминает последний подтверждённый узел.
        self.last_node_id = start_node
        # L43: Текущее ребро — пара ID узлов либо None, если ребро не занято.
        self.edge: tuple[str, str] | None = None
        # L44: idle означает ожидание команды.
        self.status = "idle"
        # L45: Ошибки пока нет; dict будет хранить код и текст ошибки.
        self.error: dict | None = None
        # L46: Активного приказа при создании нет.
        self.current_order: dict | None = None
        # L47: Идентификатор последней принятой команды ещё неизвестен.
        self.last_command_id: str | None = None
        # L48: Ни одна команда ещё не завершена.
        self.completed_command_id: str | None = None
        # L49: Уникальный boot_id позволяет отличить перезапуск от старой телеметрии.
        self.boot_id = uuid.uuid4().hex
        # L50: Счётчик сообщений состояния начинается с нуля.
        self.seq = 0
        # L51: Начальный срок действия движения уже истёк.
        self._lease_until = 0.0
        # L52: Запрошенные ручные скорости: линейная и угловая.
        self._manual = (0.0, 0.0)
        # L53: Имитация застревания при создании выключена.
        self._injected_stuck = False
        # L54: История command_id → command_seq сохраняет порядок вставки.
        self._seen: OrderedDict[str, int] = OrderedDict()
        # L55: Ни одной ревизии команды ещё не принято.
        self._last_command_seq = -1

    # L57: @property позволяет читать вычисляемое значение как robot.node_id без скобок.
    @property
    # L58: Возвращает ID ближайшего узла или None, когда робот находится между узлами.
    def node_id(self) -> str | None:
        # L59: Получает ID ближайшего узла и расстояние до него.
        node_id, distance = self.map.nearest_node(self.x, self.y)
        # L60: Условное выражение возвращает узел только при расстоянии не более 6 см.
        return node_id if distance <= NODE_TOLERANCE else None

    # L62: Общая обработка ошибки: записать причину, остановить робота и вернуть False.
    def _fail(self, code: str, message: str) -> bool:
        # L63: Формирует машиночитаемый код и понятное описание ошибки.
        self.error = {"code": code, "message": message}
        # L64: Меняет статус на error.
        self.status = "error"
        # L65: Немедленно обнуляет обе скорости.
        self.linear = self.angular = 0.0
        # L66: False сообщает вызывающему коду, что действие не удалось.
        return False

    # L68: Запоминает принятую команду для защиты от повторов и старых ревизий.
    def _remember(self, command_id: str, command_seq: int) -> None:
        # L69: Сохраняет соответствие уникального ID номеру команды.
        self._seen[command_id] = command_seq
        # L70: Ограничивает историю 256 записями.
        if len(self._seen) > 256:
            # L71: last=False удаляет самую старую вставленную запись.
            self._seen.popitem(last=False)
        # L72: Запоминает ID последней принятой команды для телеметрии.
        self.last_command_id = command_id
        # L73: Обновляет максимальную принятую ревизию команды.
        self._last_command_seq = command_seq

    # L75: Включает или выключает искусственное застревание.
    def set_stuck(self, enabled: bool) -> None:
        # L76: Сохраняет значение флага имитации отказа.
        self._injected_stuck = enabled
        # L77: Ошибка возникает при включении; выключение само ошибку не сбрасывает.
        if enabled:
            # L78: Регистрирует STUCK и обнуляет скорости через общий обработчик.
            self._fail("STUCK", "Движение заблокировано препятствием")

    # L80: Проверяет, остался ли робот на ранее занятом ребре.
    def _on_edge(self, edge: tuple[str, str]) -> bool:
        # L81: Распаковывает координаты двух концов ребра через генератор.
        a, b = (self.map.nodes[node_id] for node_id in edge)
        # L82: Считает точку на ребре, если расстояние до его отрезка не превышает 6 см.
        return segment_distance(self.x, self.y, a.x, a.y, b.x, b.y) <= NODE_TOLERANCE

    # L84: Обрабатывает одну команду; others — остальные физические модели для столкновений.
    def command(self, command: dict, now: float, others=()) -> bool:
        # L85: Внутренний контракт требует словарь, соответствующий JSON-объекту.
        if not isinstance(command, dict):
            # L86: Некорректный тип команды переводит робота в INVALID_COMMAND.
            return self._fail("INVALID_COMMAND", "Команда должна быть объектом")
        # L87: Читает тип команды безопасно через get, отсутствующее поле даёт None.
        kind = command.get("type")
        # L88: Читает уникальный идентификатор команды.
        command_id = command.get("command_id")
        # L89: Требует непустую строку длиной до 128 символов.
        if not isinstance(command_id, str) or not command_id or len(command_id) > 128:
            # L90: Отказывает при отсутствующем или некорректном command_id.
            return self._fail("INVALID_COMMAND", "Нужен непустой строковый идентификатор команды")
        # L91: Читает возрастающий номер команды.
        command_seq = command.get("command_seq")
        # L92: Не допускает bool, нецелый или отрицательный номер ревизии.
        if isinstance(command_seq, bool) or not isinstance(command_seq, int) or command_seq < 0:
            # L93: Регистрирует ошибку формата command_seq.
            return self._fail("INVALID_COMMAND", "Номер команды должен быть неотрицательным целым числом")
        # L94: Отдельно обрабатывает уже встречавшийся command_id.
        if command_id in self._seen:
            # L95: Повтор с иной ревизией либо более старая команда недопустимы.
            if self._seen[command_id] != command_seq or command_seq < self._last_command_seq:
                # L96: Отклоняет устаревший или противоречивый повтор без изменения движения.
                return False
            # L97: Продление относится только к ещё активному приказу с тем же ID.
            if self.current_order and self.current_order["command_id"] == command_id:
                # L98: У повторного move должен совпадать и маршрут перехода.
                if kind == "move" and command.get("path") == self.current_order["path"]:
                    # L99: Продлевает lease существующего перехода без повторного старта движения.
                    self._lease_until = now + MOVE_LEASE
                    # L100: Повтор команды не должен автоматически снимать уже возникшую ошибку.
                    if not self.error:
                        # L101: Возвращает движению статус moving, например после временного истечения lease.
                        self.status = "moving"
                # L102: elif обрабатывает повтор ручной команды отдельно от автоматической.
                elif kind == "manual":
                    # L103: Продлевает срок уже активного ручного приказа.
                    self._lease_until = now + MANUAL_LEASE
            # L104: Сообщает, что корректный повтор обработан; завершённая команда не запускается вновь.
            return True
        # L105: Новая по ID команда всё равно должна иметь ревизию новее последней принятой.
        if command_seq <= self._last_command_seq:
            # L106: Игнорирует запоздалую команду, чтобы старое move не отменило stop.
            return False
        # L107: Фиксирует ID и ревизию до обработки конкретного действия.
        self._remember(command_id, command_seq)
        # L108: Ветка команды аварийной для автоматики остановки.
        if kind == "stop":
            # L109: Снимает текущий приказ, но сведения о занятом ребре сохраняются отдельно.
            self.current_order = None
            # L110: Прекращает движение и поворот.
            self.linear = self.angular = 0.0
            # L111: Сохраняет error при ошибке, иначе переводит в ожидание.
            self.status = "error" if self.error else "idle"
            # L112: Отмечает stop выполненным в телеметрии.
            self.completed_command_id = command_id
            # L113: Проверяет, не остановился ли исправный робот слишком далеко от графа.
            if not self.error and self.map.distance_to_graph(self.x, self.y) > GRAPH_TOLERANCE:
                # L114: Записывает OFF_GRAPH, если дальнейшая автоматика с этой позиции недопустима.
                self._fail("OFF_GRAPH", "Робот остановился слишком далеко от навигационного графа")
            # L115: Подтверждает обработку stop, даже если при этом обнаружена ошибка позиции.
            return True
        # L116: Ветка явного сброса ошибки оператором.
        if kind == "clear_error":
            # L117: Сброс запрещён, пока действует имитация застревания или есть столкновение.
            if self._injected_stuck or self._collision(self.x, self.y, others):
                # L118: Оставляет STUCK до фактического устранения препятствия.
                return self._fail("STUCK", "Перед сбросом ошибки устраните препятствие")
            # L119: Ищет ближайший навигационный узел.
            node_id, distance = self.map.nearest_node(self.x, self.y)
            # L120: Проверяет возможность сохранить текущее автоматическое ребро.
            retained_edge = bool(self.edge and self._on_edge(self.edge))
            # L121: Вне сохранённого ребра допускает привязку только с расстояния до 0,2 м.
            if distance > 0.2 and not retained_edge:
                # L122: Просит вручную вернуть робота к узлу вместо небезопасного продолжения.
                return self._fail("OFF_GRAPH", "В ручном режиме верните робота на расстояние не более 0,2 м от узла")
            # L123: Получает объект ближайшего узла для точной привязки координат.
            node = self.map.nodes[node_id]
            # L124: При сохранённом ребре координаты посреди него не меняются.
            if not retained_edge:
                # L125: Перед привязкой проверяет, что сам ближайший узел свободен.
                if self._collision(node.x, node.y, others):
                    # L126: Запрещает сброс с переносом робота на занятую позицию.
                    return self._fail("STUCK", "Ближайший узел занят другим роботом")
                # L127: Привязывает координаты к ближайшему свободному узлу.
                self.x, self.y = node.x, node.y
            # L128: Снимает сохранённую ошибку.
            self.error = None
            # L129: Убирает старый приказ: продолжение выдаёт диспетчер.
            self.current_order = None
            # L130: Переводит робота в ожидание.
            self.status = "idle"
            # L131: Сброс ошибки сам по себе не запускает движение.
            self.linear = self.angular = 0.0
            # L132: Подтверждает завершение команды clear_error.
            self.completed_command_id = command_id
            # L133: При привязке к узлу обновляет графовое положение.
            if self.node_id and not retained_edge:
                # L134: Сохраняет текущий узел и освобождает сведения о старом ребре.
                self.last_node_id, self.edge = self.node_id, None
            # L135: Сообщает об успешном сбросе ошибки.
            return True
        # L136: Ветка ручного задания линейной и угловой скорости.
        if kind == "manual":
            # L137: Числовые поля ручного управления могут быть некорректными.
            try:
                # L138: Проверяет линейную скорость, подставляя ноль при отсутствии поля.
                linear = _number(command.get("linear", 0.0), "linear")
                # L139: Проверяет угловую скорость аналогичным образом.
                angular = _number(command.get("angular", 0.0), "angular")
            # L140: Перехватывает ошибку проверки числового поля.
            except ValueError as exc:
                # L141: Превращает исключение проверки в ошибку робота.
                return self._fail("INVALID_COMMAND", str(exc))
            # L142: Ручной режим не отменяет физическую имитацию застревания.
            if self._injected_stuck:
                # L143: Требует выключить застревание перед ручным движением.
                return self._fail("STUCK", "Сначала выключите имитацию застревания")
            # L144: Ограничивает ручную линейную скорость диапазоном ±MAX_LINEAR.
            self._manual = (max(-MAX_LINEAR, min(MAX_LINEAR, linear)),
                            # L145: Ограничивает ручную угловую скорость диапазоном ±MAX_ANGULAR; завершает пару скоростей.
                            max(-MAX_ANGULAR, min(MAX_ANGULAR, angular)))
            # L146: Устанавливает короткий lease ручного управления.
            self._lease_until = now + MANUAL_LEASE
            # L147: Запоминает ручной приказ и его ID.
            self.current_order = {"command_id": command_id, "type": "manual"}
            # L148: Свободное ручное движение больше не привязано к автоматическому ребру.
            self.edge = None
            # L149: При разрешённом входе в ручной режим снимает прежнюю ошибку.
            self.error = None
            # L150: Устанавливает статус manual.
            self.status = "manual"
            # L151: Подтверждает приём ручного управления.
            return True
        # L152: После остальных веток поддерживается только move.
        if kind != "move":
            # L153: Любой оставшийся неизвестный тип — ошибка INVALID_COMMAND.
            return self._fail("INVALID_COMMAND", "Неподдерживаемый тип команды")
        # L154: Автоматический move запрещён при несброшенной ошибке.
        if self.error:
            # L155: Отклоняет движение до устранения и сброса ошибки.
            return False
        # L156: Читает путь одного разрешённого перехода.
        path = command.get("path")
        # L157: Требует список из ровно двух строковых ID узлов.
        if not isinstance(path, list) or len(path) != 2 or not all(isinstance(n, str) for n in path):
            # L158: Отклоняет отсутствующий или неверно составленный путь.
            return self._fail("INVALID_PATH", "Для перемещения нужны начальный и конечный узлы")
        # L159: Проверяет наличие каждого ID пути на карте.
        if any(node_id not in self.map.nodes for node_id in path):
            # L160: Выдаёт POINT_NOT_FOUND при неизвестном узле.
            return self._fail("POINT_NOT_FOUND", "Указанный узел не существует")
        # L161: Распаковывает путь в начальный и конечный узлы.
        source, destination = path
        # L162: Проверяет реальное ребро через список соседей начального узла.
        if destination not in self.map.adjacency[source]:
            # L163: Запрещает ехать напрямую между несоседними точками.
            return self._fail("INVALID_PATH", "Движение разрешено только по существующему ребру")
        # L164: Проверяет расстояние текущей позиции до навигационного графа.
        if self.map.distance_to_graph(self.x, self.y) > GRAPH_TOLERANCE:
            # L165: При слишком большом отклонении выдаёт OFF_GRAPH.
            return self._fail("OFF_GRAPH", "Робот находится слишком далеко от навигационного графа")
        # L166: Получает координаты исходного узла перехода.
        source_node = self.map.nodes[source]
        # L167: Отличает продолжение уже занятого ребра от нового перехода.
        resume = self.edge == (source, destination) and self._on_edge(self.edge)
        # L168: Для нового перехода требует находиться у исходного узла; для сохранённого ребра делает исключение.
        if math.hypot(self.x - source_node.x, self.y - source_node.y) > NODE_TOLERANCE and not resume:
            # L169: Выдаёт NOT_AT_NODE, если стартовая позиция для этого перехода неверна.
            return self._fail("NOT_AT_NODE", "Верните робота к начальному узлу в ручном режиме")
        # L170: Сохраняет активный move; копия path защищает от внешнего изменения списка.
        self.current_order = {"command_id": command_id, "type": "move", "path": path.copy()}
        # L171: Фиксирует занятое направленное ребро.
        self.edge = (source, destination)
        # L172: Запоминает подтверждённый узел, из которого начался переход.
        self.last_node_id = source
        # L173: Разрешает движение на ближайшие 0,9 с без повторного продления.
        self._lease_until = now + MOVE_LEASE
        # L174: Отмечает, что робот выполняет автоматическое движение.
        self.status = "moving"
        # L175: Возвращает успешный результат приёма перехода.
        return True

    # L177: Проверяет предполагаемую позицию на столкновения; padding — дополнительный запас радиуса.
    def _collision(self, x: float, y: float, others, padding: float = 0.0) -> str | None:
        # L178: Увеличивает защитную окружность на переданный запас.
        radius = ROBOT_RADIUS + padding
        # L179: Проверяет обе x-границы хорды с радиусом и запасом дуги, чтобы не пропустить отклонение около стены.
        if (min(self.x, x) - radius <= 0 or max(self.x, x) + radius >= self.map.width
                # L180: Так же проверяет минимальную и максимальную y-координаты обоих концов перемещения.
                or min(self.y, y) - radius <= 0 or max(self.y, y) + radius >= self.map.height):
            # L181: Возвращает код столкновения со стеной.
            return "COLLISION_WALL"
        # L182: Перебирает другие физические модели общего мира.
        for other in others:
            # L183: Исключает самого себя по объекту или идентификатору.
            if other is self or other.robot_id == self.robot_id:
                # L184: continue пропускает себя и переходит к следующему роботу.
                continue
            # L185: Проверяет весь отрезок до другого центра; положительный запас 1e-9 отклоняет почти касающийся опасный шаг.
            if segment_distance(other.x, other.y, self.x, self.y, x, y) < 2 * ROBOT_RADIUS + padding + 1e-9:
                # L186: Возвращает код столкновения с другим роботом.
                return "COLLISION_ROBOT"
        # L187: None означает, что препятствие не обнаружено.
        return None

    # L189: Вычисляет и применяет перемещение за dt; без сил и масс — это кинематика.
    def _integrate(self, linear: float, angular: float, dt: float, others) -> bool:
        # L190: Новый угол равен прежнему углу плюс угловая скорость, умноженная на время.
        next_yaw = self.yaw + angular * dt
        # L191: Почти нулевой поворот обрабатывается как прямолинейное движение.
        if abs(angular) < 1e-10:
            # L192: Проекция линейного перемещения на ось X через cos угла.
            x = self.x + linear * math.cos(self.yaw) * dt
            # L193: Проекция линейного перемещения на ось Y через sin угла.
            y = self.y + linear * math.sin(self.yaw) * dt
        # L194: При ненулевой угловой скорости траектория является дугой окружности.
        else:
            # L195: Интегрирует X по дуге; linear/angular задаёт радиус поворота.
            x = self.x + linear / angular * (math.sin(next_yaw) - math.sin(self.yaw))
            # L196: Интегрирует Y по той же дуге с учётом направления поворота.
            y = self.y - linear / angular * (math.cos(next_yaw) - math.cos(self.yaw))
        # L197: Проверяет траекторию и добавляет запас на отличие дуги от отрезка.
        collision = self._collision(x, y, others, abs(linear * angular) * dt * dt / 8)
        # L198: Обрабатывает обнаруженное препятствие до изменения координат.
        if collision:
            # L199: Останавливает робота с полученным кодом столкновения.
            return self._fail(collision, "Движение остановлено перед столкновением")
        # L200: При безопасном ходе применяет вычисленные координаты.
        self.x, self.y = x, y
        # L201: Нормализует новый угол, чтобы он не накапливал полные обороты.
        self.yaw = angle_difference(next_yaw, 0.0)
        # L202: Сохраняет реально применённые скорости для телеметрии.
        self.linear, self.angular = linear, angular
        # L203: Сообщает, что интегрирование завершилось без столкновения.
        return True

    # L205: Продвигает состояние одного робота на dt секунд при текущем времени now.
    def tick(self, dt: float, now: float, others=()) -> None:
        # L206: Отбрасывает нечисловой или неположительный шаг времени.
        if not math.isfinite(dt) or dt <= 0:
            # L207: Выходит, не изменяя физику при неверном dt.
            return
        # L208: Ограничивает большой шаг 0,1 с, чтобы не перескочить препятствия после задержки.
        dt = min(dt, 0.1)
        # L209: По умолчанию робот неподвижен; движение ниже задаст новые скорости.
        self.linear = self.angular = 0.0
        # L210: Ошибка или искусственное застревание запрещают движение.
        if self.error or self._injected_stuck:
            # L211: Выходит, оставив скорости нулевыми.
            return
        # L212: Без активного приказа нет причин двигаться.
        if not self.current_order:
            # L213: Завершает обновление ожидающего робота.
            return
        # L214: Проверяет автономный watchdog: истёк ли срок разрешения движения.
        if now >= self._lease_until:
            # L215: Для автоматического перехода сохраняет приказ и ребро, позволяя продолжить его.
            if self.current_order["type"] == "move":
                # L216: Отмечает автоматическое движение как приостановленное.
                self.status = "paused"
            # L217: Ручной приказ после истечения lease завершается, а не ждёт продолжения.
            else:
                # L218: Переводит в ожидание.
                self.status = "idle"
                # L219: Удаляет истёкший ручной приказ.
                self.current_order = None
                # L220: Проверяет, не остался ли робот вне допустимой области графа.
                if self.map.distance_to_graph(self.x, self.y) > GRAPH_TOLERANCE:
                    # L221: Сохраняет OFF_GRAPH после остановки ручного движения вдали от графа.
                    self._fail("OFF_GRAPH", "Робот вне графа остановлен: срок действия ручной команды истёк")
            # L222: После истечения lease физическое движение в этом такте не выполняется.
            return
        # L223: Делит время на достаточное число подшагов длиной не более 0,02 с.
        steps = max(1, math.ceil(dt / 0.02))
        # L224: Вычисляет точную длительность каждого подшага.
        step = dt / steps
        # L225: Повторяет физическое обновление для каждого подшага; _ означает ненужный индекс.
        for _ in range(steps):
            # L226: Выбирает свободное ручное перемещение вместо движения к узлу.
            if self.current_order["type"] == "manual":
                # L227: Передаёт две скорости из кортежа через * и проверяет успешность интегрирования.
                if not self._integrate(*self._manual, step, others):
                    # L228: При столкновении прекращает оставшиеся подшаги.
                    return
                # L229: Проверяет, оказался ли робот достаточно близко к узлу.
                if self.node_id:
                    # L230: Обновляет последний узел и убирает старое ребро при ручном возвращении к графу.
                    self.last_node_id, self.edge = self.node_id, None
                # L231: Завершает этот ручной подшаг, не выполняя автоматическую ветку.
                continue
            # L232: Получает координаты конечного узла активного перехода.
            destination = self.map.nodes[self.current_order["path"][1]]
            # L233: Вычисляет вектор от текущей позиции к цели.
            dx, dy = destination.x - self.x, destination.y - self.y
            # L234: hypot вычисляет длину вектора — оставшееся расстояние.
            distance = math.hypot(dx, dy)
            # L235: Очень малое расстояние считается прибытием, чтобы убрать погрешность float.
            if distance <= 1e-7:
                # L236: Перед точной доводкой к узлу проверяет весь короткий отрезок: присваивание координат тоже не должно пересечь корпус.
                collision = self._collision(destination.x, destination.y, others)
                # L237: Если доводка упирается в стену или робота, её нельзя применять.
                if collision:
                    # L238: Записывает причину отказа доводки, обнуляет скорости и сохраняет прежние координаты.
                    self._fail(collision, "Доводка к узлу остановлена перед столкновением")
                    # L239: Завершает этот такт после отказа, не отмечая приказ выполненным.
                    return
                # L240: Ставит координаты точно в конечный узел.
                self.x, self.y = destination.x, destination.y
                # L241: Фиксирует прибывший узел и снимает занятое ребро.
                self.last_node_id, self.edge = destination.id, None
                # L242: Записывает ID выполненного перехода для подтверждения диспетчеру.
                self.completed_command_id = self.current_order["command_id"]
                # L243: Завершённый приказ больше не активен.
                self.current_order = None
                # L244: Робот ждёт следующего разрешённого перехода.
                self.status = "idle"
                # L245: По прибытии обе скорости равны нулю.
                self.linear = self.angular = 0.0
                # L246: Выходит, чтобы не продолжать завершённый приказ.
                return
            # L247: atan2 вычисляет требуемое направление из компонентов вектора к цели.
            heading = math.atan2(dy, dx)
            # L248: Выбирает кратчайшую разницу между нужным и текущим углами.
            turn = angle_difference(heading, self.yaw)
            # L249: Пока направление отличается заметно, выполняется поворот на месте.
            if abs(turn) > 1e-8:
                # L250: Ограничивает угловую скорость, чтобы не превысить максимум и не проскочить угол.
                angular = max(-MAX_ANGULAR, min(MAX_ANGULAR, turn / step))
                # L251: Применяет только поворот: линейная скорость равна нулю.
                if not self._integrate(0.0, angular, step, others):
                    # L252: При неуспешном повороте завершает обновление.
                    return
            # L253: После выравнивания едет вперёд; скорость не позволяет проехать дальше цели.
            elif not self._integrate(min(MAX_LINEAR, distance / step), 0.0, step, others):
                # L254: Завершает обновление при столкновении во время прямого движения.
                return

    # L256: Собирает независимый словарь состояния для передачи диспетчеру.
    def telemetry(self, now: float) -> dict:
        # L257: Увеличивает seq, чтобы каждое новое состояние можно было отличить от повтора.
        self.seq += 1
        # L258: Начинает JSON-состояние идентификаторами робота, запуска и сообщения.
        return {"robot_id": self.robot_id, "boot_id": self.boot_id, "seq": self.seq,
                # L259: Добавляет время симуляции и pose — позицию с углом в радианах.
                "sim_time": now, "pose": {"x": self.x, "y": self.y, "yaw": self.yaw},
                # L260: velocity содержит линейную и угловую скорости корпуса.
                "velocity": {"linear": self.linear, "angular": self.angular,
                             # L261: Скорость левого колеса в рад/с: из скорости корпуса вычитается вклад поворота.
                             "left_wheel": (self.linear - self.angular * WHEEL_TRACK / 2) / WHEEL_RADIUS,
                             # L262: Скорость правого колеса: прибавляется вклад поворота; деление на радиус даёт рад/с.
                             "right_wheel": (self.linear + self.angular * WHEEL_TRACK / 2) / WHEEL_RADIUS},
                # L263: Добавляет текущий ближайший и последний подтверждённый узлы.
                "node_id": self.node_id, "last_node_id": self.last_node_id,
                # L264: Преобразует пару концов ребра в JSON-список; при отсутствии ребра передаёт null.
                "edge": list(self.edge) if self.edge else None,
                # L265: Передаёт статус и копию ошибки, чтобы не отдавать внутренний изменяемый словарь.
                "status": self.status, "error": self.error.copy() if self.error else None,
                # L266: Передаёт копию активного приказа или null.
                "current_order": dict(self.current_order) if self.current_order else None,
                # L267: ID последней принятой команды помогает сопоставить доставку и телеметрию.
                "last_command_id": self.last_command_id,
                # L268: Передаёт последнюю принятую ревизию команды.
                "last_command_seq": self._last_command_seq,
                # L269: completed_command_id подтверждает завершение конкретного перехода или действия.
                "completed_command_id": self.completed_command_id}

# L271: Общий физический мир содержит карту и несколько независимых моделей роботов.
class WorldCore:
    # L272: Конструктор мира принимает имя карты, число роботов и идентификатор сцены.
    def __init__(self, map_name: str = "normal", robot_count: int = 3,
                 # L273: Значение initial используется до первого reset диспетчера; завершает сигнатуру.
                 world_id: str = "initial"):
        # L274: Загружает одну из предусмотренных ТЗ схем.
        self.map = load_map(map_name)
        # L275: Проверяет целое число роботов от одного до количества парковок, исключая bool.
        if isinstance(robot_count, bool) or not isinstance(robot_count, int) or not 1 <= robot_count <= len(self.map.parking_nodes):
            # L276: Запрещает создать больше роботов, чем можно разместить на карте.
            raise ValueError("Число роботов должно быть от одного до числа парковок на карте")
        # L277: Проверяет формат идентификатора сцены.
        if not isinstance(world_id, str) or not world_id or len(world_id) > 128:
            # L278: Сообщает об ошибке некорректного world_id.
            raise ValueError("Идентификатор симуляции должен быть непустой строкой длиной до 128 символов")
        # L279: Сохраняет уникальную идентичность текущего мира.
        self.world_id = world_id
        # L280: До первой команды reset ревизия мира равна нулю.
        self._reset_seq = 0
        # L281: Начинает словарь robot1, robot2, … с отдельным RobotCore на каждого.
        self.robots = {f"robot{index + 1}": RobotCore(f"robot{index + 1}", self.map, node_id)
                       # L282: enumerate даёт номер парковки; срез берёт только нужное число стартовых точек.
                       for index, node_id in enumerate(self.map.parking_nodes[:robot_count])}
        # L283: Для каждого робота задаёт три выключенных искусственных отказа.
        self.faults = {robot_id: {"stuck": False, "link_lost": False, "throttle": False}
                       # L284: Продолжение генератора словаря отказов по ID всех роботов.
                       for robot_id in self.robots}
        # L285: Время следующего приёма команды сначала нулевое; нужно для throttle.
        self._next_receive = dict.fromkeys(self.robots, 0.0)
        # L286: Время следующей публикации состояния сначала нулевое.
        self._next_publish = dict.fromkeys(self.robots, 0.0)

    # L288: Принимает общую команду сцены или пересылает её конкретному роботу.
    def command(self, command: dict, now: float) -> bool:
        # L289: Требует объект-словарь после разбора JSON.
        if not isinstance(command, dict):
            # L290: Игнорирует иной формат без изменения мира.
            return False
        # L291: Reset обрабатывается до проверки world_id, поскольку он меняет мир.
        if command.get("type") == "reset":
            # L292: Читает возрастающий номер создания сцены.
            sequence = command.get("reset_seq")
            # L293: Требует положительное целое reset_seq и исключает bool.
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
                # L294: Отклоняет некорректную ревизию reset.
                return False
            # L295: Отдельно рассматривает повтор reset уже действующего мира.
            if command.get("world_id") == self.world_id:
                # L296: Подтверждает только тот же reset_seq без повторного создания роботов.
                return sequence == self._reset_seq
            # L297: Более старая ревизия не должна вернуть прежнюю сцену.
            if sequence <= self._reset_seq:
                # L298: Игнорирует запоздавший reset.
                return False
            # L299: Создание нового мира может завершиться ошибкой параметров.
            try:
                # L300: Готовит новый мир отдельно, не повреждая текущий при неуспешной проверке.
                replacement = WorldCore(command.get("map", "normal"),
                                        # L301: Передаёт число роботов и новый world_id из reset-команды.
                                        command.get("robots", 3), command.get("world_id"))
            # L302: Перехватывает неверные значения или типы параметров новой сцены.
            except (ValueError, TypeError):
                # L303: Сохраняет старый мир при неуспешном создании нового.
                return False
            # L304: Заменяет атрибуты текущего мира атрибутами успешно созданного.
            self.__dict__.update(replacement.__dict__)
            # L305: Запоминает принятую ревизию reset.
            self._reset_seq = sequence
            # L306: Подтверждает создание новой сцены.
            return True
        # L307: Все остальные команды обязаны относиться к текущему миру.
        if command.get("world_id") != self.world_id:
            # L308: Игнорирует запоздавшее сообщение предыдущей сцены.
            return False
        # L309: Читает ID робота-адресата.
        robot_id = command.get("robot_id")
        # L310: Проверяет строковый тип и наличие адресата в мире.
        if not isinstance(robot_id, str) or robot_id not in self.robots:
            # L311: Отклоняет команду неизвестному роботу.
            return False
        # L312: Одновременно получает модель робота и её настройки отказов.
        robot, faults = self.robots[robot_id], self.faults[robot_id]
        # L313: Изменение искусственного отказа обрабатывается даже при потере обычного канала.
        if command.get("type") == "fault":
            # L314: Перебирает только предусмотренные имена отказов.
            for key in faults:
                # L315: Каждое переданное значение отказа должно быть именно bool.
                if key in command and not isinstance(command[key], bool):
                    # L316: Отклоняет неверный формат настройки отказа.
                    return False
            # L317: Обновляет только поля, присутствующие в команде.
            faults.update({key: command[key] for key in faults if key in command})
            # L318: Передаёт состояние stuck физической модели, немедленно фиксируя ошибку при включении.
            robot.set_stuck(faults["stuck"])
            # L319: Подтверждает применение параметров отказа.
            return True
        # L320: Имитация разрыва канала блокирует обычные команды роботу.
        if faults["link_lost"]:
            # L321: Не доставляет команду при link_lost; robot lease остановит движение самостоятельно.
            return False
        # L322: При throttle обычные команды принимаются не чаще раза в секунду.
        if faults["throttle"]:
            # L323: Проверяет, наступило ли время следующего разрешённого приёма.
            if now < self._next_receive[robot_id]:
                # L324: Пропускает слишком частую команду в режиме замедления связи.
                return False
            # L325: Назначает следующий допустимый приём через одну секунду.
            self._next_receive[robot_id] = now + 1.0
        # L326: Доставляет проверенную команду модели, передавая общий список для столкновений.
        return robot.command(command, now, self.robots.values())

    # L328: Продвигает физику всех роботов на одном общем времени.
    def tick(self, dt: float, now: float) -> None:
        # L329: Перебирает экземпляры RobotCore в текущем мире.
        for robot in self.robots.values():
            # L330: Передаёт каждому роботу dt, now и весь парк для проверки столкновений.
            robot.tick(dt, now, self.robots.values())

    # L332: Добавляет данные сцены к телеметрии одного робота.
    def _state(self, robot: RobotCore, now: float) -> dict:
        # L333: Получает новое состояние модели с увеличенным seq.
        state = robot.telemetry(now)
        # L334: Помечает сообщение идентификатором текущего мира.
        state["world_id"] = self.world_id
        # L335: Добавляет ревизию создания мира.
        state["reset_seq"] = self._reset_seq
        # L336: Добавляет копию искусственных отказов этого робота.
        state["fault"] = self.faults[robot.robot_id].copy()
        # L337: Возвращает готовое состояние для публикации или снимка.
        return state

    # L339: Собирает состояния всех роботов без фильтра по искусственной связи.
    def snapshot(self, now: float) -> list[dict]:
        # L340: Генератор списка получает новое состояние каждого робота.
        return [self._state(robot, now) for robot in self.robots.values()]

    # L342: Возвращает только состояния, которые можно опубликовать в данный момент.
    def telemetry(self, now: float) -> list[dict]:
        # L343: Начинает пустой список выходящих сообщений.
        states = []
        # L344: Перебирает пары ID и физической модели робота.
        for robot_id, robot in self.robots.items():
            # L345: Получает настройки отказов текущего робота.
            faults = self.faults[robot_id]
            # L346: Пропускает потерянную связь и роботов, чьё время публикации ещё не наступило.
            if faults["link_lost"] or now < self._next_publish[robot_id]:
                # L347: Переходит к следующему роботу, не публикуя это состояние.
                continue
            # L348: Следующая публикация через 1 с при throttle или через 0,1 с в норме.
            self._next_publish[robot_id] = now + (1.0 if faults["throttle"] else 0.1)
            # L349: Добавляет новое состояние с метками мира в список публикации.
            states.append(self._state(robot, now))
        # L350: Возвращает отобранные сообщения для ROS-ноды симулятора.
        return states
