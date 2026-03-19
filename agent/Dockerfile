FROM python:3.12-slim

WORKDIR /app

# Install minimal dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code (read-only in production)
COPY . /app/agent/

# Memory volume mount point
RUN mkdir -p /data

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "agent.brain"]
