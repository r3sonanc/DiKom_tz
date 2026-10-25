from glob import glob

from setuptools import find_packages, setup

setup(
    name="warehouse_sim",
    version="1.0.0",
    packages=find_packages(exclude=("tests", "tools", "versions")),
    package_data={"warehouse": ["static/*"]},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/warehouse_sim"]),
        ("share/warehouse_sim", ["package.xml"]),
        ("share/warehouse_sim/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=False,
    maintainer="Candidate",
    maintainer_email="candidate@example.com",
    description="2D ROS 2 warehouse simulation and cooperative fleet dispatcher",
    license="MIT",
    entry_points={
        "console_scripts": [
            "simulator = warehouse.ros_simulator:main",
            "dispatcher = warehouse.api:main",
        ]
    },
)
