FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install faster-whisper
WORKDIR /app
COPY split.py .
ENTRYPOINT ["python", "split.py"]
