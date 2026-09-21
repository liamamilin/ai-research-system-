# Stage 1: Build frontend
FROM node:22-alpine AS frontend
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --silent
COPY ui/ .
RUN npm run build

# Stage 2: Build final image
FROM python:3.11-alpine
WORKDIR /app

# System deps
RUN apk add --no-cache git

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Frontend from builder
COPY --from=frontend /app/ui/dist /app/ui/dist

# Default config
COPY config/web.yaml.example config/web.yaml

# Expose
EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')" || exit 1

# Start
ENV AI_RESEARCH_ENV=production
CMD ["python", "run_web.py", "serve"]
