FROM python:3.12-alpine3.21@sha256:d4f9227f21409479c7fe92288f2e40b1a56ae591c8ad3b5446dfeaef90f67857
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    S300_CONFIG=/config/settings.json
WORKDIR /app
COPY s300_control/*.py /app/src/s300_control/
COPY s300_control/static /app/src/s300_control/static/
USER 1000:1000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-m", "s300_control.healthcheck"]
CMD ["python", "-m", "s300_control"]
