"""Análise de sentimento com BERT e as regras de classificação do projeto.

O modelo usado (``nlptown/bert-base-multilingual-uncased-sentiment``) devolve
uma nota de 1 a 5 estrelas. Este módulo traduz essa nota para o vocabulário do
projeto (positivo / neutro / negativo) e para um índice numérico 0–1 usado nos
gráficos.
"""

import logging
import re

from config import (
    BERT_MODEL_NAME,
    CARREGAR_BERT,
    INDICE_NEGATIVO,
    INDICE_NEUTRO,
    INDICE_POSITIVO,
)

logger = logging.getLogger("sentcrypto.sentimento")

POSITIVO = "positivo"
NEGATIVO = "negativo"
NEUTRO = "neutro"
NULO = "nulo"  # texto que não fala de cripto — não entra nas métricas

# Limite de tokens do modelo. Textos maiores são truncados.
MAX_TOKENS = 512

_pipeline = None


# ── Carregamento do modelo ──────────────────────────────────────────────────


def carregar_modelo() -> bool:
    """Carrega o pipeline do BERT uma única vez. Devolve True se disponível."""
    global _pipeline

    if _pipeline is not None:
        return True
    if not CARREGAR_BERT:
        logger.warning("Carregamento do BERT desabilitado por configuração.")
        return False

    try:
        from transformers import pipeline

        logger.info("Carregando modelo BERT (%s)...", BERT_MODEL_NAME)
        _pipeline = pipeline(
            "sentiment-analysis",
            model=BERT_MODEL_NAME,
            tokenizer=BERT_MODEL_NAME,
        )
        logger.info("BERT carregado com sucesso.")
        return True
    except Exception as e:
        logger.error("Erro ao carregar BERT: %s", e)
        _pipeline = None
        return False


def modelo_carregado() -> bool:
    return _pipeline is not None


# ── Tradução das saídas do modelo ───────────────────────────────────────────


def mapear_estrela_para_sentimento(label: str) -> str:
    """Converte o label do modelo (ex.: ``'4 stars'``) em positivo/neutro/negativo.

    A extração usa o primeiro dígito do label em vez de ``'1' in label``, que
    daria falso positivo em labels como ``'1 star'`` vs ``'11'`` e é frágil se
    o modelo mudar o formato.
    """
    if not label:
        return NEUTRO

    match = re.search(r"\d+", str(label))
    if not match:
        # Alguns modelos devolvem POSITIVE/NEGATIVE em vez de estrelas.
        texto = str(label).upper()
        if "POS" in texto:
            return POSITIVO
        if "NEG" in texto:
            return NEGATIVO
        return NEUTRO

    estrelas = int(match.group())
    if estrelas <= 2:
        return NEGATIVO
    if estrelas == 3:
        return NEUTRO
    return POSITIVO


def sentimento_para_indice(sentimento: str) -> float:
    """Converte o rótulo de sentimento no índice numérico usado nos gráficos."""
    if sentimento == NEGATIVO:
        return INDICE_NEGATIVO
    if sentimento == POSITIVO:
        return INDICE_POSITIVO
    return INDICE_NEUTRO


# ── Filtro de relevância cripto ─────────────────────────────────────────────

CRYPTO_KEYWORDS = {
    # Moedas
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "dogecoin", "doge",
    "xrp", "ripple", "cardano", "ada", "avalanche", "avax", "chainlink", "link",
    "bnb", "binance", "polygon", "matic", "polkadot", "dot", "litecoin", "ltc",
    "shiba", "shib", "tron", "trx", "tether", "usdt", "usdc",
    # Termos gerais
    "crypto", "cryptocurrency", "criptomoeda", "criptomoedas", "cripto",
    "blockchain", "defi", "nft", "web3", "altcoin", "altcoins",
    "token", "tokens", "staking", "mining", "minerar", "mineração",
    "wallet", "carteira", "exchange", "corretora",
    "bull", "bear", "bullish", "bearish", "pump", "dump",
    "hodl", "moon", "mooning", "whale", "whales",
    "marketcap", "trading", "trade", "trader",
    "satoshi", "nakamoto", "halving", "hashrate",
    "dex", "cex", "yield", "airdrop", "ico", "ido",
    "metaverse", "metaverso", "dao",
}

# Expressões com espaço não podem ser testadas por palavra isolada.
CRYPTO_FRASES = {
    "market cap", "smart contract", "contrato inteligente", "layer 2", "gas fee",
}

# Divide em palavras preservando $BTC e hashtags como #bitcoin.
_TOKENIZADOR = re.compile(r"[a-zà-ú0-9]+", re.IGNORECASE)


def texto_e_crypto_relevante(texto: str) -> bool:
    """Diz se o texto fala de criptomoedas.

    Usa comparação por palavra inteira. A checagem ingênua por substring fazia
    ``"link"`` casar dentro de ``"linkedin"``, ``"dot"`` dentro de ``"dot com"``
    e ``"gas"`` dentro de ``"Las Vegas"``, classificando textos aleatórios como
    relevantes e contaminando o índice de sentimento.
    """
    if not texto:
        return False

    texto_lower = texto.lower()

    if any(frase in texto_lower for frase in CRYPTO_FRASES):
        return True

    palavras = set(_TOKENIZADOR.findall(texto_lower))
    return bool(palavras & CRYPTO_KEYWORDS)


# ── Inferência ──────────────────────────────────────────────────────────────


def analisar_texto(texto: str) -> dict:
    """Roda o BERT num texto e devolve rótulo, índice e confiança.

    Levanta ``RuntimeError`` se o modelo não estiver carregado.
    """
    if _pipeline is None:
        raise RuntimeError("Modelo BERT não carregado.")

    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Texto vazio.")

    resultado = _pipeline(texto, truncation=True, max_length=MAX_TOKENS)[0]
    sentimento = mapear_estrela_para_sentimento(resultado["label"])

    return {
        "sentimento": sentimento,
        "indice": sentimento_para_indice(sentimento),
        "score": round(float(resultado["score"]), 4),
        "label_bert": resultado["label"],
    }


def classificar(texto: str) -> dict:
    """Classifica um texto aplicando antes o filtro de relevância cripto.

    Textos fora do domínio recebem ``sentimento='nulo'`` e não consomem
    inferência do modelo.
    """
    texto = (texto or "").strip()
    if not texto:
        raise ValueError("Texto vazio.")

    if not texto_e_crypto_relevante(texto):
        return {
            "sentimento": NULO,
            "indice": None,
            "score": None,
            "label_bert": None,
        }

    return analisar_texto(texto)
