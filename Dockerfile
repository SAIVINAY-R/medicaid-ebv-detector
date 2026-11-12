FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

# Copy repository contents into image
COPY . /app

# Expose default HTTP port
EXPOSE 8080

# Cloud container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request, os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", 8080)}/health')" || exit 1

# Start the Medicaid Fraud Intelligence web service
CMD ["python3", "serve.py"]
