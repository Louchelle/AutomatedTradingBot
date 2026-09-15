FROM python:3.11-slim

# Install system dependencies for Tkinter and virtual framebuffer
RUN apt-get update && apt-get install -y \\
    python3-tk \\
    xvfb \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set environment variable to point to the virtual display
ENV DISPLAY=:99

# Start Xvfb in the background and run the Python app
CMD Xvfb :99 -screen 0 1024x768x16 & python main.py