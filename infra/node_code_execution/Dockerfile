# Sandbox image for TypeScript ontology functions.
#
# A second image rather than adding Node to the Python one: the transform sandbox is on a pinned
# python:slim digest and pulling a Node toolchain into it would widen the attack surface of every
# Python transform for the benefit of a runtime they never use. The two images share the policy
# applied at run time -- non-root, no network, read-only root, all capabilities dropped -- because
# they carry the same threat model, not because they carry the same packages.
FROM node:22-bookworm-slim@sha256:2fa754a9ba4d7adbd2a51d182eaabbe355c82b673624035a38c0d42b08724854

ARG TYPESCRIPT_VERSION=5.9.2
ARG FOUNDRY_LITE_RUNTIME_SOURCE_SHA=unknown

LABEL dev.foundry-lite.runtime-source-sha="${FOUNDRY_LITE_RUNTIME_SOURCE_SHA}"

# typescript is the only dependency: the runner transpiles in the sandbox so that source in and
# built output executed stays one step, with no host round trip and no network fetch.
RUN npm install --global --no-fund --no-audit "typescript@${TYPESCRIPT_VERSION}" \
    && install -d -o 65532 -g 65532 -m 0555 /sandbox-output \
    && groupadd --gid 65532 sandbox 2>/dev/null || true \
    && useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin sandbox 2>/dev/null || true

COPY --chown=65532:65532 \
    libs/foundry_lite/infrastructure/runners/typescript_function_runner.mjs \
    /opt/foundry-lite/runner/typescript_function_runner.mjs

# The runner resolves `typescript` through the global install rather than a local node_modules,
# so the read-only root filesystem never needs a writable package directory.
ENV NODE_PATH=/usr/local/lib/node_modules

USER 65532:65532
WORKDIR /sandbox
