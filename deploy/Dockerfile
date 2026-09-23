FROM python:3.11-slim

# Install Node.js for WeChat adapter & Microsoft To Do module
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    build-essential \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python & Node dependencies
COPY requirements.lock package.json package-lock.json ./
RUN pip install --no-cache-dir -r requirements.lock \
    && npm ci --omit=dev

# Copy application source
COPY . .

RUN addgroup --system omni-assistant \
    && adduser --system --ingroup omni-assistant --home /app omni-assistant \
    && chown -R omni-assistant:omni-assistant /app

USER omni-assistant

CMD ["python3", "main.py"]
