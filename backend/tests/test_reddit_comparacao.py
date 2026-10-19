"""Testes da simulação com fonte Reddit e da comparação X x Reddit.

Mesmo padrão de test_simulacao.py: SQLite em memória, BERT desligado e
candles da Binance substituídos por uma lista sintética.

Executar (a partir da pasta backend/):

    python -m pytest tests/test_reddit_comparacao.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import SocialPost
from schemas import SimulacaoInvestimentoRequest
from services import comparacao as svc_comparacao
from services import mercado as svc_mercado
from services import simulacao as svc_simulacao
from services.mercado import Candle

BASE = datetime(2026, 3, 3, 0, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def post(fonte: str, hora: datetime, sentimento: str, autor: str) -> SocialPost:
    return SocialPost(
        moeda="BTC",
        fonte=fonte,
        external_id=f"{fonte}-{hora.isoformat()}-{autor}",
        url="https://exemplo.com/p",
        autor=autor,
        texto="bitcoin",
        sentimento=sentimento,
        score=0.9,
        timestamp_post=hora,
    )


def mockar_candles(monkeypatch, precos: list[float]) -> None:
    candles = []
    for i, preco in enumerate(precos):
        abertura = precos[i - 1] if i > 0 else preco
        candles.append(
            Candle(timestamp=BASE + timedelta(hours=i), abertura=abertura,
                   fechamento=preco)
        )
    monkeypatch.setattr(
        svc_mercado, "buscar_klines_intervalo", lambda *a, **k: candles
    )


def simular(db, perfis, fonte, horas=3):
    return svc_simulacao.simular_investimento(
        db, "BTC", perfis, BASE, BASE + timedelta(hours=horas),
        1000.0, 100.0, 5.0, 3.0, 50.0, "fomo", fonte,
    )


class TestSimulacaoReddit:
    def test_fomo_reddit_sem_subreddits_usa_todo_o_reddit(self, db, monkeypatch):
        db.add(post("Reddit", BASE + timedelta(hours=1), "positivo", "r/Bitcoin"))
        db.commit()
        mockar_candles(monkeypatch, [100, 95, 96])

        resultado = simular(db, [], "Reddit")

        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert resultado["vazio"] is False
        assert len(compras) == 1
        assert resultado["parametros"]["fonte"] == "Reddit"

    def test_filtra_por_subreddit(self, db, monkeypatch):
        db.add(post("Reddit", BASE + timedelta(hours=1), "positivo", "r/Bitcoin"))
        db.commit()
        mockar_candles(monkeypatch, [100, 95, 96])

        assert simular(db, ["r/Bitcoin"], "Reddit")["vazio"] is False
        assert simular(db, ["Bitcoin"], "Reddit")["vazio"] is False
        assert simular(db, ["ethtrader"], "Reddit")["vazio"] is True

    def test_reddit_nao_enxerga_posts_do_x(self, db, monkeypatch):
        db.add(post("X", BASE + timedelta(hours=1), "positivo", "@whale_alert"))
        db.commit()
        mockar_candles(monkeypatch, [100, 95, 96])

        resultado = simular(db, [], "Reddit")

        assert resultado["vazio"] is True
        assert "Reddit" in resultado["mensagem"]

    def test_x_continua_exigindo_perfis(self, db, monkeypatch):
        mockar_candles(monkeypatch, [100, 95, 96])

        resultado = simular(db, [], "X")

        assert resultado["vazio"] is True
        assert "perfil do X" in resultado["mensagem"]


class TestSchemaSimulacao:
    base = {"data_inicio": "2026-03-01", "data_fim": "2026-03-08"}

    def test_fomo_x_sem_perfis_e_invalido(self):
        with pytest.raises(ValidationError):
            SimulacaoInvestimentoRequest(modo="fomo", fonte="X", **self.base)

    def test_fomo_reddit_sem_perfis_e_valido(self):
        req = SimulacaoInvestimentoRequest(modo="fomo", fonte="Reddit", **self.base)
        assert req.fonte == "Reddit"

    def test_fonte_e_normalizada_e_padrao_e_x(self):
        req = SimulacaoInvestimentoRequest(
            modo="fomo", fonte="reddit", **self.base
        )
        assert req.fonte == "Reddit"
        padrao = SimulacaoInvestimentoRequest(
            modo="flat", **self.base
        )
        assert padrao.fonte == "X"

    def test_fonte_desconhecida_e_invalida(self):
        with pytest.raises(ValidationError):
            SimulacaoInvestimentoRequest(
                modo="flat", fonte="Telegram", **self.base
            )


class TestPearson:
    def test_correlacao_perfeita_positiva_e_negativa(self):
        assert svc_comparacao.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0
        assert svc_comparacao.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == -1.0

    def test_sem_variancia_ou_poucos_pares_devolve_none(self):
        assert svc_comparacao.pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None
        assert svc_comparacao.pearson([1, 2], [1, 2]) is None
        assert svc_comparacao.pearson([], []) is None


def popular(db, horas, sentimento_x, sentimento_reddit):
    """Um post por hora em cada fonte; os sentimentos vêm de funções da hora."""
    for i in horas:
        h = BASE + timedelta(hours=i)
        db.add(post("X", h, sentimento_x(i), "@whale_alert"))
        db.add(post("Reddit", h, sentimento_reddit(i), "r/Bitcoin"))
    db.commit()


def alternado(i):
    return "positivo" if i % 2 == 0 else "negativo"


def invertido(i):
    return "negativo" if i % 2 == 0 else "positivo"


class TestComparacaoFontes:
    def test_fontes_iguais_correlacionam_e_concordam(self, db, monkeypatch):
        popular(db, range(12), alternado, alternado)
        mockar_candles(monkeypatch, [100 + (i % 2) * 3 for i in range(40)])

        r = svc_comparacao.comparar_fontes(db, "BTC")

        comp = r["comparacao"]
        assert comp["horas_em_comum"] == 12
        assert comp["amostra_suficiente"] is True
        assert comp["correlacao_sentimento"] == 1.0
        assert comp["concordancia_pct"] == 100.0
        assert "aviso" not in comp
        assert len(r["serie"]) == 12
        assert r["serie"][0]["X"] == 1.0 and r["serie"][0]["Reddit"] == 1.0

    def test_fontes_opostas_tem_correlacao_negativa(self, db, monkeypatch):
        popular(db, range(12), alternado, invertido)
        mockar_candles(monkeypatch, [100 + i for i in range(40)])

        comp = svc_comparacao.comparar_fontes(db, "BTC")["comparacao"]

        assert comp["correlacao_sentimento"] == -1.0
        assert comp["concordancia_pct"] == 0.0

    def test_poucas_horas_em_comum_nao_calcula_correlacao(self, db, monkeypatch):
        popular(db, range(4), alternado, alternado)
        mockar_candles(monkeypatch, [100 + i for i in range(40)])

        comp = svc_comparacao.comparar_fontes(db, "BTC")["comparacao"]

        assert comp["horas_em_comum"] == 4
        assert comp["correlacao_sentimento"] is None
        assert comp["amostra_suficiente"] is False
        assert "mínimo" in comp["aviso"]

    def test_devolve_a_contagem_de_posts_por_sentimento(self, db, monkeypatch):
        popular(db, range(12), alternado, invertido)
        mockar_candles(monkeypatch, [100 + i for i in range(40)])

        fontes = svc_comparacao.comparar_fontes(db, "BTC")["fontes"]

        assert fontes["X"]["posts"] == {"positivos": 6, "negativos": 6, "neutros": 0}
        assert fontes["Reddit"]["posts"] == {"positivos": 6, "negativos": 6, "neutros": 0}

    def test_fonte_sem_dados_nao_quebra(self, db, monkeypatch):
        for i in range(5):
            db.add(post("X", BASE + timedelta(hours=i), alternado(i), "@a"))
        db.commit()
        mockar_candles(monkeypatch, [100 + i for i in range(40)])

        r = svc_comparacao.comparar_fontes(db, "BTC")

        assert r["fontes"]["Reddit"]["resumo"]["total_posts"] == 0
        assert r["fontes"]["X"]["resumo"]["total_posts"] == 5
        assert r["comparacao"]["horas_em_comum"] == 0
        assert r["comparacao"]["concordancia_pct"] is None
        assert all(linha["Reddit"] is None for linha in r["serie"])

    def test_serie_une_horas_de_fontes_diferentes(self, db, monkeypatch):
        db.add(post("X", BASE, "positivo", "@a"))
        db.add(post("Reddit", BASE + timedelta(hours=1), "negativo", "r/Bitcoin"))
        db.commit()
        mockar_candles(monkeypatch, [100 + i for i in range(40)])

        serie = svc_comparacao.comparar_fontes(db, "BTC")["serie"]

        assert [(s["X"], s["Reddit"]) for s in serie] == [(1.0, None), (None, -1.0)]
