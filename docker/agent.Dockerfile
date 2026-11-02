ARG PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright:v1.64.1-noble
FROM ${PLAYWRIGHT_IMAGE}

ARG OPENCODE_VERSION=1.19.5
ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/agent
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git ripgrep procps \
 && rm -rf /var/lib/apt/lists/*

RUN npm_config_ignore_scripts=false npm install -g "opencode-ai@${OPENCODE_VERSION}"

WORKDIR /opt/spritebench

# Seed dependencies, including @playwright/test. Browsers come from the base
# image; --ignore-scripts avoids re-downloading them.
COPY tasks/spriteforge-lite/seed/package.json tasks/spriteforge-lite/seed/package-lock.json ./
RUN npm ci --ignore-scripts

COPY docker/serve.mjs /opt/spritebench/serve.mjs
COPY docker/entrypoint.sh /usr/local/bin/spritebench-entrypoint
RUN chmod +x /usr/local/bin/spritebench-entrypoint

RUN mkdir -p /home/agent /work

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/spritebench-entrypoint"]
CMD ["bash"]
