FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for face_recognition / dlib / OpenCV
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        cmake \
        build-essential \
        git \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
        libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project — PYTHONPATH is set to /app in compose
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]