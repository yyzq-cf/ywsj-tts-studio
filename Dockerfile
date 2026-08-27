FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 构建时注入版本号(Git commit hash), 可通过 --build-arg APP_VERSION=xxx 覆盖
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads output

EXPOSE 5100

CMD ["python", "-u", "app.py"]
