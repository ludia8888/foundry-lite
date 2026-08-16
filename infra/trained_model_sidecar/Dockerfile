FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

ARG FOUNDRY_LITE_RUNTIME_SOURCE_SHA=unknown

LABEL dev.foundry-lite.runtime-source-sha="${FOUNDRY_LITE_RUNTIME_SOURCE_SHA}"

RUN groupadd --gid 65532 model \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin model \
    && install -d -o 65532 -g 65532 -m 0555 /model-output

COPY --chown=65532:65532 \
    libs/foundry_lite/infrastructure/runners/trained_model_runner.py \
    /opt/foundry-lite/model/trained_model_runner.py

USER 65532:65532
WORKDIR /model
