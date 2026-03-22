# Mindex Container
# A dedicated runtime image for `mindex container` sessions.
#
# Goals:
# - Independent environment: dependencies are inside the image.
# - Independent Mindex: install Mindex into the image (no host Python required).
# - Codex CLI is available (Node + @openai/codex).
#
# Note: Auth material (ssh keys, git config, tokens) is intentionally NOT baked in.
#       If you need them, mount them explicitly via shared_folders in ~/.mindex/config.json.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/mindex-venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    git \
    openssh-client \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (official distro packages are often behind; use NodeSource).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install OpenAI Codex CLI.
RUN npm install -g @openai/codex

# Install Mindex (copied into the build context as ./mindex_src).
COPY mindex_src /opt/mindex_src
RUN python3 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip setuptools wheel \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir /opt/mindex_src

# Pre-create Mindex managed config in the image layer; first-run volumes will inherit it.
RUN "${VIRTUAL_ENV}/bin/python" -m mindex.configure configure --project-root /opt/mindex_src || true

WORKDIR /workspace

CMD ["bash"]
