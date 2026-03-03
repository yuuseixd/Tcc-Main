from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from datetime import datetime, timezone
from db import Base


def _utcnow():
    return datetime.now(timezone.utc)


class MarketPoint(Base):
    __tablename__ = "market_points"

    id = Column(Integer, primary_key=True, index=True)
    moeda = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    preco = Column(Float)
    indice_sentimento = Column(Float)
    criado_em = Column(DateTime, default=_utcnow)


class SocialPost(Base):
    __tablename__ = "social_posts"

    id = Column(Integer, primary_key=True, index=True)
    moeda = Column(String, index=True)          # ex: BTC
    fonte = Column(String, index=True)          # ex: X, Instagram, Reddit
    texto = Column(Text)                        # texto bruto do post
    sentimento = Column(String)                 # positivo / negativo / neutro
    score = Column(Float)                       # confiança do BERT
    timestamp_post = Column(DateTime, index=True)   # quando o post foi publicado
    timestamp_coleta = Column(DateTime, default=_utcnow)
