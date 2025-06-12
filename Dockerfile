# Base stage with common dependencies
FROM python:3.12.6-slim-bookworm AS base
WORKDIR /app

# Install the Azure CLI
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash



# Install system dependencies
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Test stage
FROM base AS test

# Copy application code
COPY . .
# Default command for test stage
CMD ["pytest"]

# Production stage
FROM base AS production
# Copy application code
COPY . .
# Make port 8000 available
EXPOSE 8000
# Define environment variable
ENV PYTHONUNBUFFERED=1
ENV AZURE_AUTH_METHOD=token

# Run the FastAPI application using Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]