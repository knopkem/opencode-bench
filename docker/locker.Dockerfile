ARG UBUNTU_IMAGE=ubuntu:24.04
FROM ${UBUNTU_IMAGE}

ARG OPENCODE_VERSION=2.2.5
ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/agent
ENV GOTOOLCHAIN=local

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git ripgrep procps unzip python3 golang-go \
 && rm -rf /var/lib/apt/lists/*

# OpenCode 2, matching the host schema used by the mounted config.
RUN mkdir -p /home/agent \
 && curl -fsSL https://opencode.ai/v2/install \
      | HOME=/home/agent bash -s -- --version "${OPENCODE_VERSION}" --no-modify-path \
 && /home/agent/.opencode/bin/opencode --version
ENV PATH=/home/agent/.opencode/bin:$PATH

RUN mkdir -p /home/agent/.local/share/opencode /home/agent/.cache /work

COPY docker/locker-entrypoint.sh /usr/local/bin/opencode-bench-entrypoint
RUN chmod +x /usr/local/bin/opencode-bench-entrypoint

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/opencode-bench-entrypoint"]
CMD ["bash"]
