"""Modelos ORM do SentCrypto.

Convenção de datas: todas as colunas ``DateTime`` guardam **UTC sem tzinfo**.
A conversão é feita por ``utils.tempo`` antes de qualquer escrita.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from db import Base
from utils.tempo import agora_utc


class MarketPoint(Base):
    """Um candle horário da Binance, com o sentimento derivado da variação."""

    __tablename__ = "market_points"
    __table_args__ = (
        # Impede que sincronizações repetidas dupliquem o mesmo candle.
        UniqueConstraint("moeda", "timestamp", name="uq_market_moeda_timestamp"),
        Index("ix_market_moeda_timestamp", "moeda", "timestamp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    moeda = Column(String(20), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    preco = Column(Float)
    indice_sentimento = Column(Float)
    criado_em = Column(DateTime, default=agora_utc)


class SocialPost(Base):
    """Um post coletado de rede social, já classificado pelo BERT.

    ``sentimento`` assume: positivo / negativo / neutro / nulo.
    O valor ``nulo`` marca textos que não falam de cripto — eles ficam salvos
    para auditoria, mas são excluídos de todas as métricas.
    """

    __tablename__ = "social_posts"
    __table_args__ = (
        # Chave natural do post na origem (tweet_id, permalink do Reddit).
        # Evita reprocessar e recontar o mesmo post a cada coleta.
        # NULLs são considerados distintos pelo SQLite, então textos avulsos
        # sem identificador de origem continuam podendo ser inseridos.
        UniqueConstraint("fonte", "external_id", name="uq_social_fonte_external"),
        Index("ix_social_consulta", "moeda", "fonte", "timestamp_post"),
    )

    id = Column(Integer, primary_key=True, index=True)
    moeda = Column(String(20), nullable=False, index=True)   # ex: BTC
    fonte = Column(String(30), nullable=False, index=True)   # ex: X, Reddit
    external_id = Column(String(120), index=True)            # id do post na origem
    url = Column(String(500))                                # link do post original
    autor = Column(String(120))                              # @perfil / subreddit
    texto = Column(Text, nullable=False)                     # texto bruto
    sentimento = Column(String(20), index=True)              # positivo/negativo/neutro/nulo
    score = Column(Float)                                    # confiança do BERT
    timestamp_post = Column(DateTime, nullable=False, index=True)
    timestamp_coleta = Column(DateTime, default=agora_utc)
