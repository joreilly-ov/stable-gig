FROM python:3.11-slim

# Create a non-root user so the container doesn't run as root
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install dependencies in a separate layer for Docker cache efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .
COPY app/ app/
COPY static/ static/

# Switch to non-root user
USER appuser

# Cloud Run injects $PORT (default 8080); fall back to 8080 locally
ENV PORT=8080
EXPOSE 8080

# Health-check — Cloud Run will mark the container unhealthy if this fails
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/')"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 2"]
