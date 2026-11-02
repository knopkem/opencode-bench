ARG PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright:v1.64.1-noble
FROM ${PLAYWRIGHT_IMAGE}

ARG OPENCODE_VERSION=2.2.5
ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/agent
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git ripgrep procps unzip \
 && rm -rf /var/lib/apt/lists/*

# OpenCode 2, matching the host schema used by the mounted config. The v1
# installer deliberately refuses 2.x, so use the v2 endpoint.
RUN mkdir -p /home/agent \
 && curl -fsSL https://opencode.ai/v2/install \
      | HOME=/home/agent bash -s -- --version "${OPENCODE_VERSION}" --no-modify-path \
 && /home/agent/.opencode/bin/opencode --version
ENV PATH=/home/agent/.opencode/bin:$PATH

# chrome-devtools MCP expects Google Chrome. The Playwright image ships
# Chromium; expose it under the usual name and disable the sandbox because the
# agent container runs as root.
RUN CHROME="$(find /ms-playwright -path '*/chrome-linux*/chrome' -type f | head -1)" \
 && test -n "$CHROME" \
 && printf '#!/bin/sh\nexec %s --no-sandbox "$@"\n' "$CHROME" > /usr/local/bin/google-chrome \
 && chmod +x /usr/local/bin/google-chrome \
 && ln -sf /usr/local/bin/google-chrome /usr/local/bin/chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/google-chrome

WORKDIR /opt/spritebench

# Seed dependencies, including @playwright/test. Browsers come from the base
# image; --ignore-scripts avoids re-downloading them.
COPY tasks/spriteforge-lite/seed/package.json tasks/spriteforge-lite/seed/package-lock.json ./
RUN npm ci --ignore-scripts

COPY docker/serve.mjs /opt/spritebench/serve.mjs
COPY docker/entrypoint.sh /usr/local/bin/spritebench-entrypoint
RUN chmod +x /usr/local/bin/spritebench-entrypoint

RUN mkdir -p /home/agent /home/agent/.config /work

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/spritebench-entrypoint"]
CMD ["bash"]
