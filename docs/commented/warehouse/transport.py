# Этот модуль связывает диспетчер с ROS2: публикует команды и принимает состояния роботов.
# L1: json кодирует команды и декодирует состояния ROS.
import json
# L2: threading запускает ROS-обмен в отдельном потоке.
import threading
# L3: OrderedDict сохраняет порядок ревизий для удаления самой старой записи.
from collections import OrderedDict

# L5: Транспорт отделяет диспетчер от конкретных ROS publisher/subscriber.
class RosTransport:
    # L6: Конструктор подготавливает структуры, не запуская ROS сразу.
    def __init__(self):
        # L7: Lock защищает общий буфер телеметрии от одновременного доступа двух потоков.
        self._lock = threading.Lock()
        # L8: Буфер хранит только последнее состояние каждого робота.
        self._latest = {}
        # L9: Ограниченная таблица связывает команду с уже присвоенной ревизией.
        self._revisions = OrderedDict()
        # L10: Общий счётчик ревизий команд начинается с нуля.
        self._sequence = 0
        # L11: Нода будет создана в start, пока её нет.
        self._node = None
        # L12: Фоновый поток также создаётся только при запуске транспорта.
        self._thread = None
        # L13: Помечает транспорт как открываемый или работающий.
        self._closing = False

    # L15: Создаёт отдельный ROS-контекст, коммуникационные объекты и поток обработки.
    def start(self):
        # L16: Отложенный импорт позволяет читать модуль без установленного ROS до вызова start.
        import rclpy
        # L17: Context изолирует жизненный цикл ROS-ресурсов этого транспорта.
        from rclpy.context import Context
        # L18: Duration задаёт срок годности команды на уровне DDS.
        from rclpy.duration import Duration
        # L19: SingleThreadedExecutor обрабатывает callbacks одной ноды в своём потоке.
        from rclpy.executors import SingleThreadedExecutor
        # L20: Node представляет именованного участника ROS-обмена.
        from rclpy.node import Node
        # L21: Импортирует профиль QoS и варианты надёжности/сохранения сообщений.
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        # L22: String несёт JSON-команду или JSON-состояние в поле data.
        from std_msgs.msg import String

        # L24: Помечает транспорт как открываемый или работающий.
        self._closing = False
        # L25: Создаёт независимый контекст ROS для транспорта.
        self._context = Context()
        # L26: Инициализирует контекст; args=[] не передаёт ROS лишние аргументы HTTP-сервера.
        rclpy.init(args=[], context=self._context)
        # L27: Создаёт ноду warehouse_dispatcher в этом контексте.
        self._node = Node("warehouse_dispatcher", context=self._context)
        # L28: Начинает описание QoS исходящих команд.
        commands = QoSProfile(
            # L29: DDS-очередь ограничена сотней последних команд.
            depth=100,
            # L30: RELIABLE включает подтверждаемую доставку DDS.
            reliability=ReliabilityPolicy.RELIABLE,
            # L31: VOLATILE исключает передачу старых команд новым поздним подписчикам.
            durability=DurabilityPolicy.VOLATILE,
            # L32: Команда старше 0,5 с перестаёт быть пригодной для доставки DDS.
            lifespan=Duration(seconds=0.5),
        # L33: Завершает профиль команд.
        )
        # L34: Состояния допускают потери, хранятся только последние десять сообщений DDS.
        states = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
        # L35: Publisher отправляет команды String в топик /warehouse/commands.
        self._publisher = self._node.create_publisher(String, "/warehouse/commands", commands)
        # L36: Subscriber слушает /warehouse/states и вызывает _receive для каждого сообщения.
        self._node.create_subscription(String, "/warehouse/states", self._receive, states)
        # L37: Создаёт executor, который будет обслуживать подписку в данном контексте.
        self._executor = SingleThreadedExecutor(context=self._context)
        # L38: Регистрирует ноду в executor для обработки её событий.
        self._executor.add_node(self._node)
        # L39: Создаёт daemon-поток; его функция spin обслуживает ROS callbacks.
        self._thread = threading.Thread(target=self._executor.spin, daemon=True, name="ros-bridge")
        # L40: Запускает фоновый поток, не блокируя асинхронный HTTP-сервер.
        self._thread.start()

    # L42: Проверяет поток ROS, контекст и отсутствие закрытия транспорта.
    def is_healthy(self):
        # L43: Отсутствие узла, потока или начатое закрытие означает нездоровый транспорт.
        if self._closing or self._node is None or self._thread is None:
            # L44: Отклоняет сообщение или сообщает о нездоровом транспорте без продолжения.
            return False
        # L45: Выполняет операцию, результат или ошибка которой должны быть обработаны.
        try:
            # L46: Транспорт здоров только при живом потоке обработки и активном контексте ROS.
            return self._thread.is_alive() and self._context.ok()
        # L47: Считает недоступный либо уже закрытый контекст нездоровым.
        except (RuntimeError, AttributeError):
            # L48: Отклоняет сообщение или сообщает о нездоровом транспорте без продолжения.
            return False

    # L50: Callback подписчика получает новое сообщение телеметрии.
    def _receive(self, message):
        # L51: Защищает обработку от некорректного JSON и структуры данных.
        try:
            # L52: Преобразует строковое поле data в Python-словарь состояния.
            state = json.loads(message.data)
            # L53: Извлекает обязательный ID робота; отсутствие даст KeyError.
            robot_id = state["robot_id"]
            # L54: Принимает сообщения только от пяти возможных роботов проекта.
            if robot_id not in {"robot1", "robot2", "robot3", "robot4", "robot5"}:
                # L55: Игнорирует неизвестный ID.
                return
            # L56: with захватывает блокировку и автоматически освобождает её после блока.
            with self._lock:
                # L57: Заменяет прошлое состояние этого робота свежим вместо накапливания очереди.
                self._latest[robot_id] = state
        # L58: Ловит ошибку JSON, отсутствующее поле либо неверный тип.
        except (ValueError, KeyError, TypeError):
            # L59: Проверяет, что нода существует и её журнал доступен.
            if self._node:
                # L60: Записывает предупреждение и продолжает обработку следующих сообщений.
                self._node.get_logger().warning("Некорректная телеметрия робота отклонена")

    # L62: Управляющий поток забирает накопленные последние состояния.
    def receive(self):
        # L63: Блокировка не позволяет callback менять буфер одновременно с чтением.
        with self._lock:
            # L64: Делает список текущих состояний, чтобы вернуть его после снятия блокировки.
            states = list(self._latest.values())
            # L65: Очищает буфер: эти сообщения уже переданы диспетчеру.
            self._latest.clear()
        # L66: Возвращает список принятых состояний.
        return states

    # L68: Добавляет порядковую ревизию и отправляет команду в ROS.
    def send(self, command):
        # L69: Импортирует тип ROS String только в пути настоящей отправки.
        from std_msgs.msg import String

        # L71: Копирует словарь приказа, не изменяя переданный объект диспетчера.
        command = dict(command)
        # L72: Читает уникальный идентификатор команды, если он есть.
        command_id = command.get("command_id")
        # L73: Служебные reset/fault без command_id не участвуют в этой таблице ревизий.
        if command_id:
            # L74: Ключ включает мир, адресата и ID, чтобы одинаковые ID разных сцен не смешивались.
            key = (command.get("world_id"), command.get("robot_id"), command_id)
            # L75: Для новой команды нужно присвоить следующую ревизию.
            if key not in self._revisions:
                # L76: Увеличивает монотонный номер новой команды.
                self._sequence += 1
                # L77: Запоминает номер; повтор той же команды получит его же.
                self._revisions[key] = self._sequence
            # L78: Перемещает недавно использованную запись в конец OrderedDict.
            self._revisions.move_to_end(key)
            # L79: Добавляет command_seq в публикуемое тело команды.
            command["command_seq"] = self._revisions[key]
            # L80: Таблица ограничена 1024 записями, чтобы не расти бесконечно.
            if len(self._revisions) > 1024:
                # L81: Удаляет наименее недавно использованную запись из начала таблицы.
                self._revisions.popitem(last=False)
        # L82: Кодирует строгий JSON и публикует String в командный топик.
        self._publisher.publish(String(data=json.dumps(command, allow_nan=False)))

    # L84: Корректно завершает ROS-транспорт при остановке API.
    def close(self):
        # L85: Сразу помечает транспорт нездоровым до остановки потока ROS.
        self._closing = True
        # L86: Освобождение требуется только если поток был создан.
        if self._thread:
            # L87: Просит executor прекратить обработку callbacks, ожидая не более двух секунд.
            self._executor.shutdown(timeout_sec=2)
            # L88: Ждёт завершения самого потока также до двух секунд.
            self._thread.join(timeout=2)
            # L89: Уничтожает ROS-ноду, publisher и подписку.
            self._node.destroy_node()
            # L90: Завершает контекст, если тот ещё действует.
            self._context.try_shutdown()
