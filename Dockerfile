FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps

COPY . .

CMD ["uvicorn", "screenshot_server:app", "--host", "0.0.0.0", "--port", "8000"]