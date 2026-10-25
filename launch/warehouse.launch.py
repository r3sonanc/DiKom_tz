"""Run physics and the web dispatcher as independent ROS processes."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [
        DeclareLaunchArgument("map", default_value="normal"),
        DeclareLaunchArgument("robots", default_value="3"),
        DeclareLaunchArgument("port", default_value="8000"),
    ]
    env = {
        "WAREHOUSE_MAP": LaunchConfiguration("map"),
        "WAREHOUSE_ROBOTS": LaunchConfiguration("robots"),
        "WAREHOUSE_PORT": LaunchConfiguration("port"),
    }
    return LaunchDescription(
        args
        + [
            Node(package="warehouse_sim", executable="simulator", output="screen",
                 additional_env=env, on_exit=Shutdown(reason="Simulator exited")),
            Node(package="warehouse_sim", executable="dispatcher", output="screen",
                 additional_env=env, on_exit=Shutdown(reason="Dispatcher exited")),
        ]
    )
