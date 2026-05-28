FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . --no-build-isolation || true

# Copy source
COPY src/ ./src/
# Use template as default config; runtime config.yaml is volume-mounted by docker-compose
COPY config.template.yaml ./config.yaml

# Install properly now that src/ is present
RUN pip install --no-cache-dir -e .

# Data directory (will be mounted as a volume)
RUN mkdir -p data

EXPOSE 8001

CMD ["uvicorn", "src.jobboard.web.main:app", "--host", "0.0.0.0", "--port", "8001"]
