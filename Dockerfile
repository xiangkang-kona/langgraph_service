# 生产镜像：python:3.11-slim + 依赖安装
# 构建：docker build -t support-app:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖再拷代码：依赖层可缓存，改代码不必重装依赖
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY run.py .

EXPOSE 8000

# run.py 内部处理 Windows 事件循环策略（Linux 下为普通 asyncio 循环）
CMD ["python", "run.py"]
