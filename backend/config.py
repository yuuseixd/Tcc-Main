"""Configuração central do SentCrypto.

Todos os parâmetros ajustáveis do sistema ficam aqui, lidos de variáveis de
ambiente (arquivo .env) com valores padrão sensatos para desenvolvimento.
Centralizar isso evita constantes espalhadas pelo código e permite trocar
comportamento sem editar a lógica.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent

load_dotenv(BASE_DIR / ".env")


def _env_float(nome: str, padrao: float) -> float:
    try:
        return float(os.getenv(nome, padrao))
    except (TypeError, ValueError):
        return padrao


def _env_int(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, padrao))
    except (TypeError, ValueError):
        return padrao


def _env_list(nome: str, padrao: list[str]) -> list[str]:
    bruto = os.getenv(nome, "")
    itens = [x.strip() for x in bruto.split(",") if x.strip()]
    return itens or padrao


# ── Banco de dados ──────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'dados.db'}")

# ── Diretórios ──────────────────────────────────────────────────────────────
RELATORIOS_DIR = BASE_DIR / "relatorios"

# ── CORS ────────────────────────────────────────────────────────────────────
CORS_ORIGINS = _env_list(
    "CORS_ORIGINS",
    ["http://localhost:3000", "http://127.0.0.1:3000"],
)

# ── Modelo de IA ────────────────────────────────────────────────────────────
BERT_MODEL_NAME = os.getenv(
    "BERT_MODEL_NAME", "nlptown/bert-base-multilingual-uncased-sentiment"
)
# Carregar o modelo custa tempo/memória. Em testes isso pode ser desligado.
CARREGAR_BERT = os.getenv("CARREGAR_BERT", "1") not in ("0", "false", "False")

# ── Binance ─────────────────────────────────────────────────────────────────
BINANCE_API_URL = os.getenv(
    "BINANCE_API_URL", "https://api.binance.com/api/v3/klines"
)
BINANCE_TIMEOUT = _env_int("BINANCE_TIMEOUT", 10)

# Limite máximo de candles que a Binance aceita numa única requisição.
BINANCE_MAX_LIMIT = 1000

# ── Regras de classificação ─────────────────────────────────────────────────
# Variação percentual (em fração) a partir da qual um candle é considerado
# de alta ou de baixa. 0.005 = 0,5%.
LIMIAR_VARIACAO_CANDLE = _env_float("LIMIAR_VARIACAO_CANDLE", 0.005)

# Variação percentual (em %) usada na correlação para dizer que o preço
# "subiu" ou "desceu" em vez de ficar estável.
LIMIAR_VARIACAO_PRECO_PCT = _env_float("LIMIAR_VARIACAO_PRECO_PCT", 0.05)

# Sentiment Score a partir do qual a hora é considerada positiva/negativa.
LIMIAR_SENTIMENT_SCORE = _env_float("LIMIAR_SENTIMENT_SCORE", 0.1)

# ── Índices de sentimento (escala 0–1) ──────────────────────────────────────
INDICE_NEGATIVO = _env_float("INDICE_NEGATIVO", 0.2)
INDICE_NEUTRO = _env_float("INDICE_NEUTRO", 0.5)
INDICE_POSITIVO = _env_float("INDICE_POSITIVO", 0.8)

# ── Coleta do X ─────────────────────────────────────────────────────────────
X_CACHE_TTL = _env_int("X_CACHE_TTL", 300)

# ── Visualização ────────────────────────────────────────────────────────────
# Sem filtro de data, o histórico social mostra apenas esta janela (em dias)
# contada a partir do post mais recente. Evita que tweets antigos fixados
# estiquem o eixo do gráfico por anos.
DIAS_JANELA_PADRAO = _env_int("DIAS_JANELA_PADRAO", 30)

# ── Segurança ───────────────────────────────────────────────────────────────
# Token opcional exigido pelas rotas administrativas (ex.: salvar cookies).
# Se vazio, as rotas ficam liberadas — aceitável apenas em localhost.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# ── Moedas suportadas ───────────────────────────────────────────────────────
MOEDAS_SUPORTADAS = _env_list(
    "MOEDAS_SUPORTADAS",
    ["BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LINK"],
)
