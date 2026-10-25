FROM python:3.12-slim

RUN groupadd -r -g 10001 portal && useradd -r -u 10001 -g portal -d /app portal
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY wsgi.py .
COPY BUILD_INFO ./BUILD_INFO
# The hub's compose files travel inside the image, so the hub can fetch its own current ones (see deploy/switch-to-auto-updates.sh).
COPY deploy/ ./deploy/

RUN mkdir -p /backups /mlat && chown portal:portal /app /backups /mlat
USER portal
EXPOSE 8430

CMD ["gunicorn", "wsgi:app", "--bind", "0.0.0.0:8430", "--workers", "2", "--threads", "4", \
     "--access-logfile", "-", "--error-logfile", "-", "--logger-class", "app.logredact.RedactingLogger"]
