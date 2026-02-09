FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY polymarket_tracker.py .

CMD ["python", "polymarket_tracker.py"]
