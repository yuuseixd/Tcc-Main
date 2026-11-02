FROM python:3.11-slim

# git: o fork do twikit é instalado direto do GitHub (ver requirements.txt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build CPU do torch antes do resto: a padrão do PyPI traz CUDA (~2 GB).
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Bancos, cookies do X e cache do modelo ficam em volumes (docker-compose.yml).
ENV DATABASE_URL=sqlite:////data/dados.db \
    CONTAS_DATABASE_URL=sqlite:////data/contas.db \
    X_COOKIES_PATH=/data/x_cookies.json \
    HF_HOME=/models

EXPOSE 8000

# setup_db é idempotente: cria/migra o banco a cada subida.
CMD ["sh", "-c", "python setup_db.py && uvicorn app:app --host 0.0.0.0 --port 8000"]
