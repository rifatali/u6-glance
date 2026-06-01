FROM python:3.11-slim

# Fonts that departures.py expects (DejaVu + URW Nimbus). Without these PIL
# falls back to its default bitmap font and everything looks tiny.
RUN apt-get update && apt-get install -y --no-install-recommends \
      fonts-dejavu-core \
      fonts-urw-base35 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "departures.py"]
