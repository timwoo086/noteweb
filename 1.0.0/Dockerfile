FROM python:3.12-slim

WORKDIR /app

# 用国内镜像加速安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PORT=5000 \
    DATA_DIR=/app/data
RUN mkdir -p /app/data/covers

EXPOSE 5000

CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "2", "--timeout", "60", "app:app"]
