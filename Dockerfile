FROM python:3.10-slim

WORKDIR /app

# Install CLI-only dependencies — no PyQt6, no winotify, no selenium.
# The --load-session path bypasses core_auth entirely, so selenium is not needed.
COPY requirements-cli.txt .
RUN pip install --no-cache-dir -r requirements-cli.txt

# Copy only headless runtime files (minimal attack surface)
COPY core_api.py .
COPY core_auth.py .
COPY cloud_worker.py .
COPY cloud_cli.py .
COPY i18n.py .
COPY locales/ ./locales/

# Default entry point: cloud phantom
ENTRYPOINT ["python", "cloud_cli.py"]
CMD ["--help"]
