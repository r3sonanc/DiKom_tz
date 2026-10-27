FROM ros:jazzy-ros-base AS base

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-venv python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /workspace
COPY . /workspace/src/warehouse_sim/
RUN source /opt/ros/jazzy/setup.bash \
    && /opt/venv/bin/python -m colcon build --merge-install --packages-select warehouse_sim \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps /workspace/src/warehouse_sim
ENV PYTHONPATH="/workspace/install/lib/python3.12/site-packages" \
    AMENT_PREFIX_PATH="/workspace/install:/opt/ros/jazzy" \
    WAREHOUSE_MAP="normal" WAREHOUSE_ROBOTS="3"
EXPOSE 8000
STOPSIGNAL SIGINT
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

FROM base AS test
RUN pip install --no-cache-dir -r /workspace/src/warehouse_sim/requirements-dev.txt
WORKDIR /workspace/src/warehouse_sim
HEALTHCHECK NONE
CMD ["python", "-m", "pytest", "-q"]

FROM base AS runtime
CMD ["bash", "-c", "source /workspace/install/setup.bash && exec ros2 launch warehouse_sim warehouse.launch.py map:=${WAREHOUSE_MAP} robots:=${WAREHOUSE_ROBOTS}"]
