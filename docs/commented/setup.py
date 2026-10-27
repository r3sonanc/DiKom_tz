# Учебная копия: исходник — setup.py; рабочее приложение использует исходник.
# L — номер строки в оригинале; пояснение стоит перед неизменённой строкой кода.
# L1: glob находит файлы по маске; здесь нужен список launch-файлов.
from glob import glob

# L3: setuptools собирает пакет; find_packages обнаруживает Python-модули приложения.
from setuptools import find_packages, setup

# L5: Начинает описание установки Python- и ROS-пакета.
setup(
    # L6: Имя дистрибутива совпадает с именем ROS-пакета warehouse_sim.
    name="warehouse_sim",
    # L7: Версия метаданных пакета, а не отдельная копия исходников.
    version="1.0.0",
    # L8: Включает только warehouse и его вложенные Python-пакеты.
    packages=find_packages(include=("warehouse", "warehouse.*")),
    # L9: Включает HTML/CSS/JS и вложенные SVG из static/imges в устанавливаемый Python-пакет.
    package_data={"warehouse": ["static/*", "static/imges/*.svg"]},
    # L10: Начинает список файлов, устанавливаемых в общие каталоги ROS.
    data_files=[
        # L11: Маркер регистрирует имя пакета в индексе ament, чтобы ros2 мог его найти.
        ("share/ament_index/resource_index/packages", ["resource/warehouse_sim"]),
        # L12: Устанавливает package.xml с зависимостями и метаданными ROS-пакета.
        ("share/warehouse_sim", ["package.xml"]),
        # L13: Устанавливает найденные файлы запуска в каталог share/warehouse_sim/launch.
        ("share/warehouse_sim/launch", glob("launch/*.launch.py")),
    # L14: Завершает список общих файлов ROS.
    ],
    # L15: Требует setuptools для упаковки; HTTP-зависимости устанавливает requirements.txt.
    install_requires=["setuptools"],
    # L16: Файлы должны быть доступны обычным каталогом, а не только ZIP-архивом пакета.
    zip_safe=False,
    # L17: Имя сопровождающего в метаданных дистрибутива.
    maintainer="Candidate",
    # L18: Контакт сопровождающего, шаблонное значение проекта.
    maintainer_email="candidate@example.com",
    # L19: Краткое описание назначения пакета для инструментов сборки.
    description="2D ROS 2 warehouse simulation and cooperative fleet dispatcher",
    # L20: Указывает заявленную лицензию в метаданных пакета.
    license="MIT",
    # L21: Начинает описание исполняемых точек входа.
    entry_points={
        # L22: console_scripts создаёт команды-обёртки вызова функций Python.
        "console_scripts": [
            # L23: Команда simulator вызывает main из warehouse.ros_simulator.
            "simulator = warehouse.ros_simulator:main",
            # L24: Команда dispatcher вызывает main из warehouse.api.
            "dispatcher = warehouse.api:main",
        # L25: Завершает список консольных команд.
        ]
    # L26: Завершает словарь точек входа.
    },
# L27: Завершает вызов setup с полным описанием установки.
)
