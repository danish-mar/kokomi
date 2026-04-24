# Use the official uv image for dependency management
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

# Set the working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies without the project itself
RUN uv sync --frozen --no-install-project

# Final stage
FROM python:3.11-slim-bookworm

# Set working directory
WORKDIR /app

# Copy the installed dependencies from the builder
COPY --from=builder /app/.venv /app/.venv

# Set environment variables to use the virtualenv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Copy the application code
COPY . .

# Create data directory and ensure permissions
RUN mkdir -p data && chmod 777 data

# Expose the application port
EXPOSE 8000

# Command to run the application
# Note: we use uvicorn directly to ensure it picks up the environment correctly
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
