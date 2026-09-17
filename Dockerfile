FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Run as a non-root user, standard k8s security good practice.
RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

ENV PORT=8080
EXPOSE 8080

# gunicorn is used instead of the Flask dev server for a production-shaped
# container image; readiness/liveness probes hit /health and /ready.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "app.main:app"]
