# Учебная копия: исходник — launch/warehouse.launch.py; рабочее приложение использует исходник.
# L — номер строки в оригинале; пояснение стоит перед неизменённой строкой кода.
# L1: LaunchDescription хранит список действий совместного запуска процессов ROS.
from launch import LaunchDescription
# L2: Импортирует объявление аргументов и действие завершения всего launch.
from launch.actions import DeclareLaunchArgument, Shutdown
# L3: LaunchConfiguration читает фактическое значение аргумента при запуске.
from launch.substitutions import LaunchConfiguration
# L4: Node здесь — launch-действие запуска процесса ROS, а не класс rclpy.Node.
from launch_ros.actions import Node

# L6: ROS launch вызывает эту функцию, чтобы получить описание запуска.
def generate_launch_description():
    # L7: Начинает список объявляемых параметров запуска.
    args = [
        # L8: map выбирает схему, normal используется по умолчанию.
        DeclareLaunchArgument("map", default_value="normal"),
        # L9: robots задаёт размер парка, по умолчанию три робота.
        DeclareLaunchArgument("robots", default_value="3"),
        # L10: port задаёт порт HTTP-сервера, по умолчанию 8000.
        DeclareLaunchArgument("port", default_value="8000"),
    # L11: Завершает список объявлений аргументов.
    ]
    # L12: Готовит переменные окружения для обоих дочерних процессов.
    env = {
        # L13: Имя карты подставляется из launch-аргумента map.
        "WAREHOUSE_MAP": LaunchConfiguration("map"),
        # L14: Число роботов подставляется из аргумента robots.
        "WAREHOUSE_ROBOTS": LaunchConfiguration("robots"),
        # L15: Порт сервера подставляется из аргумента port.
        "WAREHOUSE_PORT": LaunchConfiguration("port"),
    # L16: Завершает словарь окружения.
    }
    # L17: Возвращает описание запуска ROS.
    return LaunchDescription(
        # L18: Сначала в описание входят объявления параметров.
        args
        # L19: Оператор + присоединяет действия запуска двух процессов.
        + [
            # L20: Запускает консольную команду simulator ROS-пакета warehouse_sim; вывод виден в терминале.
            Node(package="warehouse_sim", executable="simulator", output="screen",
                 # L21: Передаёт параметры через окружение; выход симулятора завершает всё приложение.
                 additional_env=env, on_exit=Shutdown(reason="Работа симулятора завершена")),
            # L22: Запускает отдельный процесс команды dispatcher того же ROS-пакета.
            Node(package="warehouse_sim", executable="dispatcher", output="screen",
                 # L23: Выход диспетчера также останавливает весь launch, чтобы не оставить половину системы.
                 additional_env=env, on_exit=Shutdown(reason="Работа диспетчера завершена")),
        # L24: Завершает список процессов.
        ]
    # L25: Завершает создание и возврат LaunchDescription.
    )
