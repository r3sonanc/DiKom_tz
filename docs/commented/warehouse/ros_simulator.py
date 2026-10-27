# Этот модуль подключает физическую симуляцию к ROS2.
# L1: json преобразует словари команд и телеметрии в текст и обратно.
import json
# L2: os читает начальную конфигурацию из переменных окружения.
import os
# L3: time предоставляет монотонные часы для физики и lease.
import time

# L5: rclpy — Python-библиотека для участия программы в ROS2.
import rclpy
# L6: Duration задаёт длительность хранения команды в DDS.
from rclpy.duration import Duration
# L7: Исключение штатного внешнего завершения executor ROS.
from rclpy.executors import ExternalShutdownException
# L8: Node — базовый класс ROS-ноды, участника обмена сообщениями.
from rclpy.node import Node
# L9: Импортирует параметры QoS: историю, надёжность и сохранение сообщений.
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
# L10: String — стандартный тип ROS-сообщения с текстовым полем data.
from std_msgs.msg import String

# L12: Импортирует общий физический мир с моделями роботов.
from warehouse.robot import WorldCore

# L14: SimulatorNode наследует Node, добавляя ROS-интерфейс к физическому миру.
class SimulatorNode(Node):
    # L15: __init__ создаёт состояние, publisher, subscriber и таймер ноды.
    def __init__(self):
        # L16: super() вызывает конструктор ROS-ноды с именем warehouse_simulator.
        super().__init__("warehouse_simulator")
        # L17: Создаёт мир по карте из окружения, запасное значение normal.
        self.world = WorldCore(os.environ.get("WAREHOUSE_MAP", "normal"),
                               # L18: Читает число роботов и преобразует строку окружения в целое.
                               int(os.environ.get("WAREHOUSE_ROBOTS", "3")),
                               # L19: Начальный world_id берётся из окружения или равен initial до reset.
                               os.environ.get("WAREHOUSE_WORLD_ID", "initial"))
        # L20: Задаёт профиль доставки команд симулятору.
        commands_qos = QoSProfile(
            # L21: KEEP_LAST хранит только последние сообщения, depth ограничивает очередь сотней.
            history=HistoryPolicy.KEEP_LAST, depth=100,
            # L22: RELIABLE запрашивает надёжную доставку на уровне DDS.
            reliability=ReliabilityPolicy.RELIABLE,
            # L23: VOLATILE не хранит старые команды для подписчиков, подключившихся позже.
            durability=DurabilityPolicy.VOLATILE,
            # L24: Срок жизни ROS-сообщения 0,5 с ограничивает доставку устаревших команд.
            lifespan=Duration(seconds=0.5),
        # L25: Завершает профиль QoS команд.
        )
        # L26: Создаёт отдельный профиль состояния, где важнее свежесть данных.
        states_qos = QoSProfile(
            # L27: Очередь телеметрии хранит последние десять сообщений.
            history=HistoryPolicy.KEEP_LAST, depth=10,
            # L28: BEST_EFFORT допускает потерю отдельных состояний вместо ожидания старых.
            reliability=ReliabilityPolicy.BEST_EFFORT,
            # L29: Состояния не восстанавливаются автоматически для поздних подписчиков.
            durability=DurabilityPolicy.VOLATILE,
        # L30: Завершает профиль QoS состояний.
        )
        # L31: Publisher публикует телеметрию String в топик /warehouse/states.
        self._publisher = self.create_publisher(String, "/warehouse/states", states_qos)
        # L32: Начинает создание subscriber — подписчика на команды.
        self._subscription = self.create_subscription(
            # L33: Подписка /warehouse/commands вызывает _command при каждом сообщении.
            String, "/warehouse/commands", self._command, commands_qos)
        # L34: Запоминает монотонное время предыдущего физического такта.
        self._last_tick = time.monotonic()
        # L35: Таймер вызывает _tick примерно каждые 0,02 с, то есть 50 раз в секунду.
        self._timer = self.create_timer(0.02, self._tick)
        # L36: Сообщает о частотах физики и публикации обычной телеметрии.
        self.get_logger().info("Симулятор готов: физика — 50 Гц, телеметрия — 10 Гц")

    # L38: Callback подписки получает одно ROS-сообщение команды.
    def _command(self, message: String) -> None:
        # L39: Ограничивает размер текста команды 16 384 символами.
        if len(message.data) > 16384:
            # L40: Записывает предупреждение о слишком большой команде.
            self.get_logger().warning("Слишком большая команда отклонена")
            # L41: Не разбирает и не выполняет чрезмерное сообщение.
            return
        # L42: Начинает защищённый разбор и обработку входных данных.
        try:
            # L43: Разбирает JSON из текстового поля message.data в Python-объект.
            payload = json.loads(message.data)
            # L44: Передаёт объект миру вместе со временем получения команды.
            self.world.command(payload, time.monotonic())
        # L45: Ловит неверный JSON, структуру, значения и переполнение параметров.
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            # L46: Записывает причину отклонения команды в журнал ROS.
            self.get_logger().warning(f"Некорректная команда отклонена: {exc}")

    # L48: Callback таймера продвигает физику и публикует доступную телеметрию.
    def _tick(self) -> None:
        # L49: Получает текущее монотонное время.
        now = time.monotonic()
        # L50: Одновременно вычисляет dt и обновляет время последнего такта.
        dt, self._last_tick = now - self._last_tick, now
        # L51: Продвигает физику всех роботов на прошедшее время.
        self.world.tick(dt, now)
        # L52: Мир выбирает состояния, чей интервал публикации уже наступил.
        for state in self.world.telemetry(now):
            # L53: Превращает состояние в строгий JSON без NaN и публикует его как ROS String.
            self._publisher.publish(String(data=json.dumps(state, allow_nan=False)))

# L55: Точка входа исполняемой команды simulator.
def main(args=None) -> None:
    # L56: Инициализирует ROS-контекст процесса с переданными аргументами.
    rclpy.init(args=args)
    # L57: До создания ноды переменная пуста, что помогает безопасно завершиться при ошибке.
    node = None
    # L58: Защищает основной цикл для штатной остановки.
    try:
        # L59: Создаёт экземпляр ноды симулятора.
        node = SimulatorNode()
        # L60: spin обслуживает таймеры и callbacks ROS до остановки процесса.
        rclpy.spin(node)
    # L61: Перехватывает Ctrl+C и внешний shutdown ROS.
    except (KeyboardInterrupt, ExternalShutdownException):
        # L62: Для штатной остановки дополнительная обработка не требуется.
        pass
    # L63: finally освобождает ресурсы независимо от причины выхода.
    finally:
        # L64: Проверяет, успела ли нода создаться.
        if node is not None:
            # L65: Уничтожает ноду и связанные ROS-сущности.
            node.destroy_node()
        # L66: Проверяет, не завершён ли уже контекст ROS.
        if rclpy.ok():
            # L67: Корректно останавливает ещё действующий контекст.
            rclpy.shutdown()

# L69: Проверяет прямой запуск файла, чтобы main не сработал при импорте.
if __name__ == "__main__":
    # L70: Запускает симулятор при прямом выполнении файла.
    main()
