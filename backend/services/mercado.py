"""Integração com a Binance e derivação de sentimento a partir do preço.

A regra "candle de alta = sentimento positivo" aparecia copiada em quatro
rotas diferentes. Aqui ela existe uma única vez, o que garante que ajustar o
limiar mude o comportamento do sistema inteiro de forma consistente.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from config import (
    BINANCE_API_URL,
    BINANCE_MAX_LIMIT,
    BINANCE_TIMEOUT,
    LIMIAR_VARIACAO_CANDLE,
)
from models import MarketPoint
from services.sentimento import (
    NEGATIVO,
    NEUTRO,
    POSITIVO,
    sentimento_para_indice,
)
from utils.tempo import de_timestamp_ms, para_timestamp_ms

logger = logging.getLogger("sentcrypto.mercado")


class ErroBinance(RuntimeError):
    """Falha ao consultar a API da Binance."""


@dataclass
class Candle:
    """Um candle horário já interpretado."""

    timestamp: datetime          # UTC naive, início do candle
    abertura: float
    fechamento: float

    @property
    def variacao(self) -> float:
        """Variação no período, em fração (0.01 = 1%)."""
        if not self.abertura:
            return 0.0
        return (self.fechamento - self.abertura) / self.abertura

    @property
    def variacao_pct(self) -> float:
        return self.variacao * 100

    @property
    def sentimento(self) -> str:
        """Sentimento derivado do movimento do preço."""
        if self.variacao > LIMIAR_VARIACAO_CANDLE:
            return POSITIVO
        if self.variacao < -LIMIAR_VARIACAO_CANDLE:
            return NEGATIVO
        return NEUTRO

    @property
    def indice_sentimento(self) -> float:
        return sentimento_para_indice(self.sentimento)


def simbolo_para(moeda: str) -> str:
    """Monta o par negociado na Binance (ex.: BTC -> BTCUSDT)."""
    return f"{moeda.upper().strip()}USDT"


def buscar_klines(
    simbolo: str,
    intervalo: str = "1h",
    limite: int = 24,
    inicio: datetime | None = None,
    fim: datetime | None = None,
) -> list[Candle]:
    """Busca candles na Binance e devolve objetos :class:`Candle`.

    Levanta :class:`ErroBinance` em qualquer falha de rede ou resposta inválida.
    """
    params: dict = {
        "symbol": simbolo,
        "interval": intervalo,
        # A Binance rejeita limites acima de 1000.
        "limit": max(1, min(int(limite), BINANCE_MAX_LIMIT)),
    }
    if inicio is not None:
        params["startTime"] = para_timestamp_ms(inicio)
    if fim is not None:
        params["endTime"] = para_timestamp_ms(fim)

    try:
        resp = requests.get(BINANCE_API_URL, params=params, timeout=BINANCE_TIMEOUT)
        resp.raise_for_status()
        dados = resp.json()
    except requests.RequestException as e:
        raise ErroBinance(f"Erro ao consultar a Binance: {e}") from e
    except ValueError as e:
        raise ErroBinance(f"Resposta inválida da Binance: {e}") from e

    if not isinstance(dados, list):
        # A Binance devolve {"code": ..., "msg": ...} para símbolo inexistente.
        msg = dados.get("msg") if isinstance(dados, dict) else "formato inesperado"
        raise ErroBinance(f"Binance recusou a consulta a {simbolo}: {msg}")

    candles: list[Candle] = []
    for k in dados:
        try:
            candles.append(
                Candle(
                    timestamp=de_timestamp_ms(k[0]),
                    abertura=float(k[1]),
                    fechamento=float(k[4]),
                )
            )
        except (IndexError, TypeError, ValueError):
            logger.warning("Candle malformado ignorado: %r", k)

    return candles


def buscar_klines_intervalo(
    simbolo: str,
    inicio: datetime,
    fim: datetime,
    intervalo: str = "1h",
    max_requisicoes: int = 10,
) -> list[Candle]:
    """Busca todos os candles entre duas datas, paginando quando necessário.

    A Binance devolve no máximo 1000 candles por requisição. Pedir só os
    "últimos N" não serve para analisar dados históricos: se os posts são de
    março e a consulta traz a última semana, nenhuma hora cruza e a correlação
    fica vazia. Aqui a janela consultada acompanha o período dos dados.

    ``max_requisicoes`` limita o custo total (10 x 1000 = ~416 dias em 1h).
    """
    if inicio >= fim:
        return []

    candles: list[Candle] = []
    cursor = inicio

    for _ in range(max_requisicoes):
        lote = buscar_klines(
            simbolo,
            intervalo=intervalo,
            limite=BINANCE_MAX_LIMIT,
            inicio=cursor,
            fim=fim,
        )
        if not lote:
            break

        candles.extend(lote)

        ultimo = lote[-1].timestamp
        if ultimo >= fim or len(lote) < BINANCE_MAX_LIMIT:
            break
        # Avança para depois do último candle recebido, senão a próxima
        # requisição devolveria o mesmo lote indefinidamente.
        cursor = ultimo + timedelta(hours=1)
    else:
        logger.warning(
            "Limite de %d requisições atingido ao buscar %s; "
            "o período pode estar incompleto.",
            max_requisicoes, simbolo,
        )

    # Requisições paginadas podem sobrepor uma hora na fronteira.
    unicos = {c.timestamp: c for c in candles}
    return [unicos[k] for k in sorted(unicos)]


def salvar_candles(db: Session, moeda: str, candles: list[Candle]) -> int:
    """Grava candles no banco sem duplicar, devolvendo quantos são novos.

    Usa ``INSERT ... ON CONFLICT DO UPDATE`` (upsert) apoiado na constraint
    única (moeda, timestamp): uma única ida ao banco em vez de um SELECT por
    candle, e sem risco de duplicata em execuções concorrentes.
    """
    if not candles:
        return 0

    moeda_u = moeda.upper()
    linhas = [
        {
            "moeda": moeda_u,
            "timestamp": c.timestamp,
            "preco": round(c.fechamento, 2),
            "indice_sentimento": c.indice_sentimento,
        }
        for c in candles
    ]

    existentes_antes = (
        db.query(MarketPoint).filter(MarketPoint.moeda == moeda_u).count()
    )

    stmt = sqlite_insert(MarketPoint).values(linhas)
    stmt = stmt.on_conflict_do_update(
        index_elements=["moeda", "timestamp"],
        set_={
            "preco": stmt.excluded.preco,
            "indice_sentimento": stmt.excluded.indice_sentimento,
        },
    )
    db.execute(stmt)
    db.commit()

    existentes_depois = (
        db.query(MarketPoint).filter(MarketPoint.moeda == moeda_u).count()
    )
    return existentes_depois - existentes_antes


def mapa_precos_por_hora(candles: list[Candle]) -> dict[str, dict]:
    """Indexa candles por hora ISO, para cruzar com os posts sociais."""
    return {
        c.timestamp.isoformat(): {
            "preco_abertura": round(c.abertura, 2),
            "preco_fechamento": round(c.fechamento, 2),
            "variacao_pct": round(c.variacao_pct, 4),
        }
        for c in candles
    }
