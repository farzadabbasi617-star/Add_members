# ================================================================
# 🐳 Dockerfile — استقرار تولیدی روی Render یا هر سرور دیگر
# ================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# وابستگی‌ها (لایه کش داکر)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# سورس پروژه
COPY . .

EXPOSE 10000

CMD ["python", "bot.py"]
