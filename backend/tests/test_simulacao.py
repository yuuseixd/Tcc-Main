"""Testes do simulador de investimento (backtest compra/venda).

Segue o padrão de test_sentcrypto.py: SQLite em memória, BERT desligado e
classificação determinística via monkeypatch. Como o motor de decisão
também depende de candles da Binance, `services.mercado.buscar_klines_intervalo`
é substituído por uma lista sintética de `Candle` fixa — sem isso os testes
dependeriam de rede e não seriam determinísticos.

Executar (a partir da pasta backend/):

    python -m pytest tests/test_simulacao.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import SocialPost
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


def post(hora: datetime, sentimento: str, autor: str = "@whale_alert") -> SocialPost:
    """Post já classificado, pronto pra inserir direto no banco (sem BERT)."""
    return SocialPost(
        moeda="BTC",
        fonte="X",
        external_id=f"id-{hora.isoformat()}-{autor}",
        url="https://x.com/x",
        autor=autor,
        texto="bitcoin",
        sentimento=sentimento,
        score=0.9,
        timestamp_post=hora,
    )


def candles_horarios(precos: list[float], inicio: datetime = BASE) -> list[Candle]:
    """Uma lista de candles horários com fechamento = abertura do próximo."""
    candles = []
    for i, preco in enumerate(precos):
        abertura = precos[i - 1] if i > 0 else preco
        candles.append(
            Candle(timestamp=inicio + timedelta(hours=i), abertura=abertura,
                   fechamento=preco)
        )
    return candles


def rodar(
    db, monkeypatch, precos, sentimentos_por_hora, perfis=("whale_alert",),
    capital_inicial=1000.0, valor_por_compra=100.0,
    percentual_lucro_venda=5.0, percentual_queda_compra=3.0,
    # Bem largo por padrão para não interferir nos testes que não são sobre
    # stop-loss — nenhum deles derruba o preço mais de 50% após uma compra.
    percentual_perda_aceita=50.0,
    modo="fomo",
):
    """Insere os posts, mocka os candles e roda a simulação."""
    for i, sentimento in sentimentos_por_hora.items():
        db.add(post(BASE + timedelta(hours=i), sentimento))
    db.commit()

    candles = candles_horarios(precos)
    monkeypatch.setattr(
        svc_mercado, "buscar_klines_intervalo", lambda *a, **k: candles
    )

    return svc_simulacao.simular_investimento(
        db, "BTC", list(perfis), BASE, BASE + timedelta(hours=len(precos)),
        capital_inicial, valor_por_compra,
        percentual_lucro_venda, percentual_queda_compra,
        percentual_perda_aceita, modo,
    )


class TestCompra:
    def test_compra_dispara_com_queda_e_sentimento_positivo(self, db, monkeypatch):
        # Preço cai 5% na hora 1 (> 3% exigido) e o sentimento é positivo.
        precos = [100, 95, 96]
        resultado = rodar(db, monkeypatch, precos, {1: "positivo"})

        assert resultado["vazio"] is False
        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert len(compras) == 1
        assert compras[0]["preco"] == 95.0

    def test_sem_sentimento_positivo_nao_compra(self, db, monkeypatch):
        precos = [100, 95, 96]
        resultado = rodar(db, monkeypatch, precos, {1: "negativo"})

        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert compras == []

    def test_queda_insuficiente_nao_compra(self, db, monkeypatch):
        # Só 1% de queda, abaixo do limiar de 3%.
        precos = [100, 99, 99]
        resultado = rodar(db, monkeypatch, precos, {1: "positivo"})

        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert compras == []


class TestVenda:
    def test_venda_dispara_na_meta_de_lucro(self, db, monkeypatch):
        # Compra a 95 (queda de 5%); vende quando bate 5% de lucro (99.75+).
        precos = [100, 95, 100, 100]
        resultado = rodar(db, monkeypatch, precos, {1: "positivo"})

        vendas = [t for t in resultado["trades"] if t["tipo"] == "venda"]
        assert len(vendas) == 1
        assert vendas[0]["preco"] == 100.0
        assert vendas[0]["motivo"] == "lucro"
        assert resultado["resumo"]["lucro_realizado"] > 0
        assert resultado["resumo"]["taxa_acerto_pct"] == 100.0
        assert resultado["resumo"]["vendas_lucrativas"] == 1
        assert resultado["resumo"]["vendas_por_stop_loss"] == 0

    def test_posicao_sem_atingir_meta_fica_aberta(self, db, monkeypatch):
        precos = [100, 95, 96]
        resultado = rodar(db, monkeypatch, precos, {1: "positivo"})

        assert resultado["resumo"]["posicoes_abertas_final"] == 1
        assert resultado["resumo"]["total_vendas"] == 0
        assert len(resultado["posicoes_abertas"]) == 1
        # Sem vendas, taxa de acerto não é definida (nenhum resultado fechado).
        assert resultado["resumo"]["taxa_acerto_pct"] is None


class TestStopLoss:
    def test_vende_ao_atingir_perda_aceita(self, db, monkeypatch):
        # Compra a 95 (queda de 5%); com stop de 4%, dispara a 91.2 ou menos.
        precos = [100, 95, 91]
        resultado = rodar(
            db, monkeypatch, precos, {1: "positivo"},
            percentual_perda_aceita=4.0,
        )

        vendas = [t for t in resultado["trades"] if t["tipo"] == "venda"]
        assert len(vendas) == 1
        assert vendas[0]["motivo"] == "perda"
        assert vendas[0]["lucro"] < 0
        assert resultado["resumo"]["vendas_por_stop_loss"] == 1
        assert resultado["resumo"]["taxa_acerto_pct"] == 0.0

    def test_nao_vende_se_perda_ainda_dentro_do_limite(self, db, monkeypatch):
        # Compra a 95; cai só 2%, dentro do stop de 4% — permanece aberta.
        precos = [100, 95, 93.1]
        resultado = rodar(
            db, monkeypatch, precos, {1: "positivo"},
            percentual_perda_aceita=4.0,
        )

        vendas = [t for t in resultado["trades"] if t["tipo"] == "venda"]
        assert vendas == []
        assert resultado["resumo"]["posicoes_abertas_final"] == 1

    def test_cada_posicao_tem_seu_proprio_stop_independente(
        self, db, monkeypatch,
    ):
        # Duas pernas de compra em queda contínua (95 e depois 90). Cada
        # posição é avaliada pelo seu próprio preço de entrada: a primeira
        # (95) estoura o stop de 4% quando o preço chega a 90; a segunda
        # (90, comprada nesse mesmo instante) só estoura o dela quando o
        # preço cai mais, a 85 — cada uma vende na sua própria hora.
        precos = [100, 95, 90, 85]
        resultado = rodar(
            db, monkeypatch, precos, {1: "positivo", 2: "positivo"},
            percentual_lucro_venda=50.0,  # alto o bastante pra não disparar
            percentual_perda_aceita=4.0,
        )

        vendas = [t for t in resultado["trades"] if t["tipo"] == "venda"]
        assert len(vendas) == 2
        assert all(v["motivo"] == "perda" for v in vendas)
        assert vendas[0]["preco_entrada"] == 95.0
        assert vendas[0]["preco"] == 90.0
        assert vendas[1]["preco_entrada"] == 90.0
        assert vendas[1]["preco"] == 85.0
        assert resultado["resumo"]["total_compras"] == 2
        assert resultado["resumo"]["vendas_por_stop_loss"] == 2


class TestGridMultiplasPosicoes:
    def test_duas_pernas_de_queda_abrem_duas_posicoes(self, db, monkeypatch):
        # Pico 100 -> compra a 95 (reseta pico) -> nova perna cai pra 90
        # (>3% a partir de 95) -> compra de novo.
        precos = [100, 95, 90, 91]
        sentimentos = {1: "positivo", 2: "positivo"}
        resultado = rodar(db, monkeypatch, precos, sentimentos)

        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert len(compras) == 2
        assert resultado["resumo"]["posicoes_abertas_final"] == 2

    def test_mesma_perna_nao_compra_duas_vezes(self, db, monkeypatch):
        # Depois da compra a 95, o preço sobe: não há nova perna de queda,
        # então não deve comprar de novo mesmo com sentimento positivo.
        precos = [100, 95, 96, 97]
        sentimentos = {1: "positivo", 2: "positivo", 3: "positivo"}
        resultado = rodar(db, monkeypatch, precos, sentimentos)

        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert len(compras) == 1


class TestBenchmarkEVazio:
    def test_buy_and_hold_calculado_sobre_todo_o_periodo(self, db, monkeypatch):
        # Um post existe (pra sair do caso "vazio"), mas sentimento negativo
        # não dispara compra — isolando o cálculo do benchmark em si.
        precos = [100, 100, 110]
        resultado = rodar(db, monkeypatch, precos, {0: "negativo"})

        bh = resultado["resumo"]["buy_and_hold"]
        assert bh["preco_inicial"] == 100.0
        assert bh["preco_final"] == 110.0
        assert bh["lucro_pct"] == pytest.approx(10.0)

    def test_sem_posts_do_perfil_devolve_vazio(self, db, monkeypatch):
        precos = [100, 95, 96]
        # Posts existem, mas de um perfil diferente do pedido na simulação.
        resultado = rodar(
            db, monkeypatch, precos, {1: "positivo"}, perfis=("outro_perfil",),
        )

        assert resultado["vazio"] is True
        assert resultado["resumo"] is None
        assert "Nenhum post" in resultado["mensagem"]

    def test_perfil_case_insensitive_e_com_arroba(self, db, monkeypatch):
        precos = [100, 95, 96]
        resultado = rodar(
            db, monkeypatch, precos, {1: "positivo"}, perfis=("@Whale_Alert",),
        )

        assert resultado["vazio"] is False
        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert len(compras) == 1


class TestModoFlat:
    """Flat ignora sentimento e perfis: compra só pela variação de preço."""

    def test_compra_sem_nenhum_post_no_periodo(self, db, monkeypatch):
        # Nenhum post inserido (sentimentos_por_hora vazio) — no modo FOMO
        # isso devolveria "vazio"; no Flat, a queda de preço basta.
        precos = [100, 95, 96]
        resultado = rodar(db, monkeypatch, precos, {}, modo="flat")

        assert resultado["vazio"] is False
        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert len(compras) == 1
        assert compras[0]["preco"] == 95.0

    def test_sentimento_negativo_nao_impede_a_compra(self, db, monkeypatch):
        # No FOMO, sentimento negativo bloquearia a compra; no Flat, o
        # sentimento é ignorado por completo.
        precos = [100, 95, 96]
        resultado = rodar(
            db, monkeypatch, precos, {1: "negativo"}, modo="flat",
        )

        compras = [t for t in resultado["trades"] if t["tipo"] == "compra"]
        assert len(compras) == 1

    def test_venda_por_lucro_e_stop_loss_continuam_valendo(self, db, monkeypatch):
        precos = [100, 95, 100, 100]
        resultado = rodar(db, monkeypatch, precos, {}, modo="flat")

        vendas = [t for t in resultado["trades"] if t["tipo"] == "venda"]
        assert len(vendas) == 1
        assert vendas[0]["motivo"] == "lucro"

    def test_modo_aparece_nos_parametros_devolvidos(self, db, monkeypatch):
        precos = [100, 95, 96]
        resultado = rodar(db, monkeypatch, precos, {}, modo="flat")

        assert resultado["parametros"]["modo"] == "flat"
