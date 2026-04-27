# --- Stage 1: Build Frontend ---
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
# Build the frontend - the result goes to /app/frontend/dist
RUN npm run build

# --- Stage 2: Final Service ---
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy frontend build from Stage 1
# We place it in a location that Django's collectstatic can pick up
COPY --from=frontend-builder /app/frontend/dist /app/frontend_dist

# Set up start script
RUN chmod +x start.sh

# Expose the port Railway expects
EXPOSE 8080

# The start command is handled in railway.toml or Railway settings
# Running via start.sh to orchestrate Django + Celery
CMD ["./start.sh"]
