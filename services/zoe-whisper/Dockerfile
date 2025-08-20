FROM python:3.9

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Whisper without PyAudio
RUN pip install --upgrade pip
RUN pip install openai-whisper fastapi uvicorn python-multipart

COPY app.py .

EXPOSE 9001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9001"]
