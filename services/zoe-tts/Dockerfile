FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    espeak \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install fastapi uvicorn pyttsx3

COPY app.py .

EXPOSE 9002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9002"]
