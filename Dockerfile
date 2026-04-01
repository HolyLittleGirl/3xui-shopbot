FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Moscow
RUN apt-get update && apt-get install -y tzdata ipset iptables && \
    ln -fs /usr/share/zoneinfo/Europe/Moscow /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata && \
    rm -rf /var/lib/apt/lists/* || true
RUN python3 -m venv .venv
ENV PATH="/app/.venv/bin:$PATH"
COPY . /app/project/
WORKDIR /app/project
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir requests flask
CMD ["python3", "-m", "shop_bot"]