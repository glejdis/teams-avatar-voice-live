# syntax=docker/dockerfile:1.6
# Hosted agent for teams-avatar-voice-live (Foundry hosted, Responses protocol).
# Target: linux/amd64, Foundry hosted agents listen on port 8088.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code. tools/ and personas/ are folders shipped with the
# image; PERSONA_FILE env var can point at any file under personas/.
COPY main.py instructions.py ./
COPY tools/    ./tools/
COPY personas/ ./personas/

EXPOSE 8088

CMD ["python", "main.py"]
