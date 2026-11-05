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
    # Zero por padrão: os testes de mecânica conferem preços e lucros exatos;
    # a taxa tem testes próprios em TestTaxa.
    taxa_operacao_pct=0.0,
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
        percentual_perda_aceita, modo, "X", taxa_operacao_pct,
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


class TestTaxa:
    def test_taxa_reduz_lucro_da_venda(self, db, monkeypatch):
        # Compra 100 a 95 e vende a 100: sem taxa, lucro = 100/95*100 - 100.
        precos = [100, 95, 100]
        sem = rodar(db, monkeypatch, precos, {1: "positivo"})
        venda_sem = [t for t in sem["trades"] if t["tipo"] == "venda"][0]
        assert sem["resumo"]["custos_totais"] == 0

        db.query(SocialPost).delete()
        db.commit()
        com = rodar(db, monkeypatch, precos, {1: "positivo"}, taxa_operacao_pct=0.1)
        venda_com = [t for t in com["trades"] if t["tipo"] == "venda"][0]

        # Compra: 0,10 de taxa sobre 100. Venda: 0,1% sobre ~105,16 brutos.
        bruto_venda = (100 - 0.1) / 95 * 100
        esperado = bruto_venda * 0.999 - 100
        assert venda_com["lucro"] == pytest.approx(esperado, abs=0.01)
        assert venda_com["lucro"] < venda_sem["lucro"]
        assert com["resumo"]["custos_totais"] == pytest.approx(0.1 + bruto_venda * 0.001, abs=0.01)

    def test_buy_and_hold_tambem_paga_a_taxa_de_entrada(self, db, monkeypatch):
        precos = [100, 100, 110]
        r = rodar(db, monkeypatch, precos, {0: "negativo"}, taxa_operacao_pct=1.0)
        # 1000 com 1% de taxa = 990 investidos; a 110 vale 1089 -> +8,9%.
        assert r["resumo"]["buy_and_hold"]["lucro_pct"] == pytest.approx(8.9)


def c(i: int, abertura, maxima, minima, fechamento) -> Candle:
    return Candle(timestamp=BASE + timedelta(hours=i), abertura=abertura,
                  fechamento=fechamento, maxima=maxima, minima=minima)


def grid(candles, **kw):
    """Roda o modo Flat direto no motor, com candles OHLC completos."""
    args = dict(capital_inicial=1000.0, valor_por_compra=100.0,
                percentual_lucro_venda=5.0, percentual_queda_compra=3.0,
                percentual_perda_aceita=4.0, modo="flat", taxa_operacao_pct=0.0)
    args.update(kw)
    return svc_simulacao._rodar_backtest("BTC", [], candles, {}, **args)


class TestExecucao:
    """Sinal no fechamento, ordem na abertura seguinte; meta e stop no pavio."""

    def test_compra_na_abertura_do_candle_seguinte(self):
        r = grid([c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95),
                  c(2, 96, 97, 95.5, 96)])
        compra = [t for t in r["trades"] if t["tipo"] == "compra"][0]
        assert compra["preco"] == 96.0            # abertura do candle 2, não 95
        assert compra["timestamp"].startswith("2026-03-03T02")

    def test_sinal_no_ultimo_candle_nao_executa(self):
        r = grid([c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95)])
        assert r["resumo"]["total_compras"] == 0

    def test_pavio_no_stop_vende_no_stop(self):
        # Compra a 95; o pavio vai a 90 (stop 91,2) mas o candle fecha a 94.
        r = grid([c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95),
                  c(2, 95, 96, 90, 94)])
        venda = [t for t in r["trades"] if t["tipo"] == "venda"][0]
        assert venda["motivo"] == "perda"
        assert venda["preco"] == pytest.approx(91.2)

    def test_pavio_na_meta_vende_na_meta(self):
        r = grid([c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95),
                  c(2, 95, 96, 95, 95.5), c(3, 96, 101, 95.5, 97)])
        venda = [t for t in r["trades"] if t["tipo"] == "venda"][0]
        assert venda["motivo"] == "lucro"
        assert venda["preco"] == pytest.approx(99.75)

    def test_gap_abaixo_do_stop_vende_na_abertura(self):
        r = grid([c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95),
                  c(2, 95, 95.5, 94, 95), c(3, 88, 89, 87, 88)])
        venda = [t for t in r["trades"] if t["tipo"] == "venda"][0]
        assert venda["preco"] == 88.0 and venda["motivo"] == "perda"

    def test_meta_e_stop_no_mesmo_candle_conta_stop(self):
        r = grid([c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95),
                  c(2, 95, 101, 90, 96)])
        assert [t["motivo"] for t in r["trades"] if t["tipo"] == "venda"] == ["perda"]

    def test_slippage_piora_compra_e_venda(self):
        velas = [c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95),
                 c(2, 95, 96, 95, 95.5), c(3, 96, 101, 95.5, 97)]
        r = grid(velas, slippage_pct=1.0)
        compra, venda = r["trades"]
        assert compra["preco"] == pytest.approx(95.95)       # 95 + 1%
        # Meta calculada sobre a entrada executada; venda sai 1% abaixo.
        assert venda["preco"] == pytest.approx(95.95 * 1.05 * 0.99, abs=0.01)


class TestMetricas:
    def test_queda_maxima(self):
        assert svc_simulacao._queda_maxima_pct([100, 120, 90, 130, 117]) == 25.0
        assert svc_simulacao._queda_maxima_pct([100, 101, 102]) == 0.0

    def test_fator_de_lucro_e_medias(self):
        # Uma operação na meta e outra no stop.
        velas = [c(0, 100, 100, 100, 100), c(1, 100, 100, 95, 95),
                 c(2, 95, 101, 95, 100), c(3, 100, 100, 96, 96),
                 c(4, 96, 96, 96, 96), c(5, 96, 96, 90, 91)]
        s = grid(velas)["resumo"]
        assert s["operacoes_fechadas"] == 2
        ganho = 100 / 95 * 99.75 - 100
        perda = 100 / 96 * 92.16 - 100
        assert s["fator_lucro"] == pytest.approx(ganho / -perda, abs=0.01)
        assert s["maior_ganho"] == pytest.approx(ganho, abs=0.01)
        assert s["maior_perda"] == pytest.approx(perda, abs=0.01)
        assert s["duracao_media_h"] == 0.5        # uma saiu na hora, outra 1 h depois
        assert 0 < s["exposicao_pct"] < 100

    def test_sem_operacao_metricas_vazias(self):
        s = grid([c(0, 100, 100, 100, 100), c(1, 100, 101, 99, 100)])["resumo"]
        assert s["fator_lucro"] is None and s["media_por_operacao"] is None
        assert s["queda_maxima_pct"] == 0.0


def padrao(tipo, i_rompimento, alvo, invalidacao, horizonte=10):
    return {"tipo": tipo, "status": "confirmado", "i_rompimento": i_rompimento,
            "alvo": alvo, "invalidacao": invalidacao, "horizonte": horizonte}


def simular_padroes(monkeypatch, velas, padroes, tipo="fundo_duplo", inicio_i=0, **kw):
    monkeypatch.setattr(svc_simulacao.svc_padroes, "detectar", lambda *a, **k: padroes)
    args = dict(capital_inicial=1000.0, valor_por_operacao=100.0, taxa_operacao_pct=0.0)
    args.update(kw)
    return svc_simulacao.simular_padroes(
        "BTC", tipo, "4h", BASE + timedelta(hours=inicio_i), BASE + timedelta(hours=99),
        candles=velas, **args)


class TestModoPadroes:
    VELAS = [c(0, 100, 100, 100, 100), c(1, 100, 102, 99, 101),
             c(2, 102, 104, 101, 103), c(3, 103, 111, 102, 110), c(4, 110, 111, 108, 109)]

    def test_entra_apos_rompimento_e_sai_no_alvo(self, monkeypatch):
        r = simular_padroes(monkeypatch, self.VELAS, [padrao("fundo_duplo", 1, 108, 95)])
        entrada, saida = r["trades"]
        assert entrada["preco"] == 102.0 and entrada["lado"] == "comprado"
        assert saida["motivo"] == "lucro" and saida["preco"] == 108.0
        assert saida["padrao"] == "Fundo duplo"
        assert r["parametros"]["padrao"] == "fundo_duplo"

    def test_stop_na_invalidacao(self, monkeypatch):
        velas = self.VELAS[:2] + [c(2, 101, 101, 94, 96), c(3, 96, 97, 95, 96)]
        r = simular_padroes(monkeypatch, velas, [padrao("fundo_duplo", 1, 120, 95)])
        assert r["trades"][1]["motivo"] == "perda" and r["trades"][1]["preco"] == 95.0

    def test_sai_por_prazo_no_fechamento(self, monkeypatch):
        r = simular_padroes(monkeypatch, self.VELAS,
                            [padrao("fundo_duplo", 1, 200, 50, horizonte=2)])
        saida = r["trades"][1]
        assert saida["motivo"] == "prazo" and saida["preco"] == 110.0  # candle 3
        assert r["resumo"]["saidas_por_prazo"] == 1

    def test_padrao_de_baixa_vende_a_descoberto(self, monkeypatch):
        velas = [c(0, 100, 100, 100, 100), c(1, 100, 100, 97, 98),
                 c(2, 98, 98, 89, 90), c(3, 90, 91, 89, 90)]
        r = simular_padroes(monkeypatch, velas, [padrao("topo_duplo", 1, 90, 105)],
                            tipo="topo_duplo")
        entrada, saida = r["trades"]
        assert entrada["lado"] == "vendido" and entrada["preco"] == 98.0
        assert saida["motivo"] == "lucro" and saida["preco"] == 90.0
        # Ganha o quanto o preço caiu: 100/98 x 8.
        assert saida["lucro"] == pytest.approx(100 / 98 * 8, abs=0.01)

    def test_rompimento_antes_do_periodo_fica_de_fora(self, monkeypatch):
        r = simular_padroes(monkeypatch, self.VELAS, [padrao("fundo_duplo", 1, 108, 95)],
                            inicio_i=2)
        assert r["vazio"] is True and "Nenhum rompimento" in r["mensagem"]

    def test_sem_caixa_nao_opera(self, monkeypatch):
        ps = [padrao("fundo_duplo", 1, 200, 50), padrao("fundo_duplo", 1, 200, 50)]
        r = simular_padroes(monkeypatch, self.VELAS, ps, capital_inicial=150.0)
        assert r["resumo"]["total_entradas"] == 1 and r["resumo"]["sem_caixa"] == 1

    def test_detector_real_roda_sem_erro(self):
        # Série com dois fundos e rompimento, sem mock: garante o encaixe.
        import math
        velas = []
        for i in range(400):
            p = 100 + 10 * math.sin(i / 15) + i * 0.02
            velas.append(c(i, p, p * 1.01, p * 0.99, p))
        r = svc_simulacao.simular_padroes(
            "BTC", "fundo_duplo", "4h", BASE + timedelta(hours=100),
            BASE + timedelta(hours=400), 1000.0, 100.0, candles=velas)
        assert "resumo" in r


class TestRequisicaoPadroes:
    def test_exige_padrao_valido(self):
        from pydantic import ValidationError
        from schemas import SimulacaoInvestimentoRequest as Req
        with pytest.raises(ValidationError):
            Req(modo="padroes", padrao="inexistente", data_inicio="2026-01-01",
                data_fim="2026-02-01")
        ok = Req(modo="padroes", padrao="fundo_duplo", intervalo="12h",
                 data_inicio="2026-01-01", data_fim="2026-02-01")
        assert ok.slippage_pct == 0.05
