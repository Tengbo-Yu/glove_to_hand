FROM codex/xrobotoolkit-runtime:jammy

# The base image is already present on the Unitree ARM64 host.  Keep all Python
# dependencies offline and pinned because the host itself runs Ubuntu 20.04
# (glibc 2.31), while wuji-sdk 2026.8.3 requires glibc 2.34 or newer.
COPY wheelhouse/ /opt/hand2-wheelhouse/
RUN PYTHONPATH=/opt/hand2-wheelhouse/pip-25.2-py3-none-any.whl \
    python3 -m pip install --no-index \
      /opt/hand2-wheelhouse/pip-25.2-py3-none-any.whl \
    && python3 -m pip install --no-index --no-deps \
      /opt/hand2-wheelhouse/*.whl \
    && python3 -m pip check \
    && python3 -c "import mujoco, nlopt, numpy, pinocchio, scipy, wuji_sdk, zmq"

COPY app/ /opt/glove_to_hand/
COPY model-assets/ \
  /opt/glove_to_hand/wuji-retargeting/wuji_retargeting/wuji-description/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/glove_to_hand/wuji-retargeting \
    LD_LIBRARY_PATH=
WORKDIR /opt/glove_to_hand

ENTRYPOINT ["python3", "-u"]
CMD ["hand_qpos_server.py", "--help"]
