FROM python:3.11-alpine

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY *.py ./

# Create a volume for the sqlite database
VOLUME ["/app/data"]

# Set environment variables for the database path
ENV DB_PATH="/app/data/translations.db"
ENV PORT=8390

# Expose the API port
EXPOSE 8390

# Command to run gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:8390", "--workers", "2", "--timeout", "120", "server:app"]
