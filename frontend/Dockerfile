# 前端镜像：Streamlit 单进程，通过 HTTP 访问 app 服务
# 构建：docker build -t support-ui:latest ./frontend
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/frontend

# 依赖层单独缓存：只改页面代码时不必重装 streamlit 全家桶
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8501

# 端口与监听地址在 .streamlit/config.toml 中固定（8501 / 0.0.0.0）
CMD ["streamlit", "run", "app.py"]
