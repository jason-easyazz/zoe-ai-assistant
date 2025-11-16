FROM dustynv/tensorrt_llm:0.12-r36.4.0

WORKDIR /app

# Install Python dependencies from standard PyPI
RUN pip3 install --no-cache-dir --index-url https://pypi.org/simple \
    fastapi \
    uvicorn \
    pydantic \
    httpx || \
    pip3 install --no-cache-dir \
    fastapi \
    uvicorn \
    pydantic \
    httpx

# Copy service files
COPY . /app/

# Expose port
EXPOSE 8011

# Run the service
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8011"]

