FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

ARG PYARROW_VERSION=24.0.0

RUN python -m pip install --no-cache-dir "pyarrow==${PYARROW_VERSION}" \
    && groupadd --gid 65532 sandbox \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin sandbox \
    && install -d -o 65532 -g 65532 -m 0555 /sandbox-output

COPY --chown=65532:65532 \
    libs/foundry_lite/infrastructure/runners/python_transform_runner.py \
    /opt/foundry-lite/runner/python_transform_runner.py
# The ontology-function runner shares this image because it shares the threat model: both run
# user code under the same non-root, no-network, read-only-root policy.
COPY --chown=65532:65532 \
    libs/foundry_lite/infrastructure/runners/python_function_runner.py \
    /opt/foundry-lite/runner/python_function_runner.py
COPY --chown=65532:65532 \
    libs/foundry_lite/infrastructure/runners/python_function_osdk.py \
    /opt/foundry-lite/runner/python_function_osdk.py
COPY --chown=65532:65532 \
    libs/foundry_lite/transforms_sdk \
    /opt/foundry-lite/foundry_lite/transforms_sdk

USER 65532:65532
WORKDIR /sandbox
