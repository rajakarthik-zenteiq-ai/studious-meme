FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server file
COPY server.py .

# If you have a utils directory, copy it; otherwise, remove this line
# COPY utils/ ./utils/

# Run server
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8101"]