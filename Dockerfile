# syntax=docker/dockerfile:1.4
# Base image
FROM node:18-bookworm-slim AS frontend-builder

WORKDIR /app/Frontend
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci
COPY Frontend/ ./
RUN npm run build

FROM python:3.10-slim-bookworm AS runtime

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV JOERN_HOME=/opt/joern/joern-cli
ENV PATH="${JOERN_HOME}:${PATH}"

# Install system dependencies
# - OpenJDK 17 for Joern
# - GCC for compiling C tests
# - Graphviz for generating graphs
# - curl, unzip, wget for downloading tools
# - lcov for coverage reports
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jdk \
        build-essential \
        zlib1g-dev \
        libssl-dev \
        pkg-config \
        cmake \
        gdb \
        valgrind \
        graphviz \
        curl \
        unzip \
        wget \
        git \
        lcov \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Joern by mounting the zip from build context (BuildKit).
# The zip is NEVER copied into any image layer — only the extracted joern-cli/ remains.
RUN --mount=type=bind,source=joern-cli.zip,target=/tmp/joern-cli.zip \
    mkdir -p /opt/joern \
    && cd /opt/joern \
    && unzip -q /tmp/joern-cli.zip \
    && chmod +x joern-cli/joern joern-cli/joern-parse

# Set working directory
WORKDIR /app

# Install Backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# Copy only the files actually needed at runtime (excludes joern-cli.zip and dev-only files)
COPY app/ ./app/
COPY resources/ ./resources/
COPY .env ./
COPY docker-entrypoint.prod.sh docker-entrypoint.sh ./
COPY --from=frontend-builder /app/Frontend/dist ./Frontend/dist

# Make entrypoint executable
RUN chmod +x docker-entrypoint.prod.sh

# Expose ports
# 8000: Backend (FastAPI + Static Frontend)
EXPOSE 8000

# Start services
ENTRYPOINT ["./docker-entrypoint.prod.sh"]
