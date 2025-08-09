FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories with correct paths
RUN mkdir -p /app/static /app/templates /app/data /app/logs

# Add some basic files to prevent errors
RUN echo "/* Zoe static CSS */" > /app/static/style.css
RUN echo "<!DOCTYPE html><html><head><title>Zoe</title></head><body>Zoe Backend</body></html>" > /app/templates/base.html

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
