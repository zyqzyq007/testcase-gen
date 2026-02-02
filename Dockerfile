# Base image
FROM python:3.10-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV JOERN_HOME=/opt/joern/joern-cli
ENV PATH="${JOERN_HOME}:${PATH}"

# Install system dependencies
# - OpenJDK 17 for Joern
# - Node.js & npm for Frontend
# - GCC for compiling C tests
# - Graphviz for generating graphs
# - curl, unzip, wget for downloading tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jdk \
    nodejs \
    npm \
    gcc \
    graphviz \
    curl \
    unzip \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Joern
WORKDIR /opt/joern
RUN wget -q https://github.com/joernio/joern/releases/latest/download/joern-cli.zip \
    && unzip -q joern-cli.zip \
    && rm joern-cli.zip \
    && chmod +x joern-cli/joern joern-cli/joern-parse

# Set working directory
WORKDIR /app

# Install Backend dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Frontend dependencies
COPY Frontend/package.json Frontend/package-lock.json ./Frontend/
RUN cd Frontend && npm install

# Copy project files
COPY . .

# Make entrypoint executable
RUN chmod +x docker-entrypoint.sh

# Expose ports
# 8000: Backend (FastAPI)
# 5173: Frontend (Vite)
EXPOSE 8000 5173

# Start services
ENTRYPOINT ["./docker-entrypoint.sh"]
