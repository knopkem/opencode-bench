# syntax=docker/dockerfile:1.4
# Build with ./bench.py build-eval --tests-dir PRIVATE_SUITE_DIR.
# The named `tests` build context deliberately comes from the private evaluator
# repository so the public agent image never contains evaluation sources.
ARG AGENT_IMAGE=opencode-bench-agent:latest
FROM ${AGENT_IMAGE}

ARG EVALUATOR_SUITE_VERSION=local
ARG EVALUATOR_SOURCE_REVISION=local
ARG EXPECTED_P0=5
ARG EXPECTED_P1=9
ARG EXPECTED_P2=6

ENV EVALUATOR_SUITE=spriteforge-lite \
    EVALUATOR_SUITE_VERSION=${EVALUATOR_SUITE_VERSION} \
    EVALUATOR_SOURCE_REVISION=${EVALUATOR_SOURCE_REVISION} \
    EXPECTED_P0=${EXPECTED_P0} \
    EXPECTED_P1=${EXPECTED_P1} \
    EXPECTED_P2=${EXPECTED_P2}

LABEL org.opencode-bench.suite="spriteforge-lite" \
      org.opencode-bench.evaluator-suite-version="${EVALUATOR_SUITE_VERSION}" \
      org.opencode-bench.evaluator-source-revision="${EVALUATOR_SOURCE_REVISION}" \
      org.opencode-bench.expected-p0="${EXPECTED_P0}" \
      org.opencode-bench.expected-p1="${EXPECTED_P1}" \
      org.opencode-bench.expected-p2="${EXPECTED_P2}"

COPY --from=tests / /opt/opencode-bench/tests/
RUN node -e "require('fs').writeFileSync('/opt/opencode-bench/evaluator.json', JSON.stringify({ suite: process.env.EVALUATOR_SUITE, version: process.env.EVALUATOR_SUITE_VERSION, source_revision: process.env.EVALUATOR_SOURCE_REVISION, expected: { P0: Number(process.env.EXPECTED_P0), P1: Number(process.env.EXPECTED_P1), P2: Number(process.env.EXPECTED_P2) } }, null, 2) + '\n')"
