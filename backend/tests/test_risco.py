"""Testes do risco das moedas (volatilidade e queda máxima medidas nos preços).

Executar (a partir da pasta backend/):

    python -m pytest tests/test_risco.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import app as app_modulo
from services import mercado as svc_mercado
from services import risco as svc_risco
from services.mercado import Candle


@pytest.fixture(autouse=True)
def cache_limpo():
    svc_risco.limpar_cache()
    yield
    svc_risco.limpar_cache()


def candles(precos):
    base = datetime(2026, 1, 1)
    return [
        Candle(timestamp=base + timedelta(days=i), abertura=p, fechamento=p)
        for i, p in enumerate(precos)
    ]


def oscilando(passo=0.01, dias=60):
    """Preço que sobe e desce o mesmo percentual a cada dia."""
    precos, p = [], 100.0
    for i in range(dias):
        p *= (1 + passo) if i % 2 == 0 else 1 / (1 + passo)
        precos.append(p)
    return precos


class TestMetricas:
    def test_preco_parado_nao_tem_risco(self):
        m = svc_risco.metricas_de_precos([100.0] * 60)
        assert m["volatilidade_anual_pct"] == 0.0
        assert m["queda_maxima_pct"] == 0.0
        assert m["risco"] == "baixo"

    def test_queda_maxima_do_pico_ao_fundo(self):
        # Sobe a 200 e cai a 100 (-50%), depois fica parado.
        precos = [100.0] * 15 + [200.0] * 15 + [100.0] * 30
        assert svc_risco.metricas_de_precos(precos)["queda_maxima_pct"] == -50.0

    def test_so_subindo_nao_tem_queda(self):
        precos = [100.0 + i for i in range(60)]
        assert svc_risco.metricas_de_precos(precos)["queda_maxima_pct"] == 0.0

    def test_volatilidade_de_um_por_cento_ao_dia_fica_perto_de_19_por_cento_ao_ano(self):
        # 1% ao dia * raiz(365) = 19,1% (o desvio-padrão amostral dá um pouco mais).
        v = svc_risco.metricas_de_precos(oscilando(0.01))["volatilidade_anual_pct"]
        assert 19.0 < v < 20.5

    def test_oscilacao_maior_da_volatilidade_maior(self):
        calmo = svc_risco.metricas_de_precos(oscilando(0.005))["volatilidade_anual_pct"]
        agitado = svc_risco.metricas_de_precos(oscilando(0.03))["volatilidade_anual_pct"]
        assert agitado > calmo * 5

    def test_serie_curta_demais_nao_e_classificada(self):
        assert svc_risco.metricas_de_precos([100.0] * 10) is None
        assert svc_risco.metricas_de_precos([]) is None

    def test_precos_invalidos_sao_ignorados(self):
        precos = [None, 0, -5] + [100.0] * 40
        assert svc_risco.metricas_de_precos(precos)["dias"] == 40


class TestClasses:
    @pytest.mark.parametrize("vol,esperado", [
        (0.0, "baixo"), (50.0, "baixo"), (50.1, "médio"),
        (70.0, "médio"), (70.1, "alto"), (150.0, "alto"),
    ])
    def test_cortes_de_volatilidade(self, vol, esperado):
        assert svc_risco.classificar(vol) == esperado


class TestRiscoDasMoedas:
    def test_mede_cada_moeda_e_informa_a_metodologia(self, monkeypatch):
        monkeypatch.setattr(svc_mercado, "buscar_klines", lambda *a, **k: candles(oscilando(0.01)))
        r = svc_risco.risco_das_moedas(("BTC", "ETH"))
        assert set(r["moedas"]) == {"BTC", "ETH"}
        assert r["moedas"]["BTC"]["risco"] == "baixo"
        assert r["metodologia"]["janela_dias"] == 365
        assert r["metodologia"]["cortes_volatilidade_pct"] == {"baixo_ate": 50.0, "medio_ate": 70.0}

    def test_usa_cache_dentro_do_prazo_e_refaz_depois(self, monkeypatch):
        chamadas = []
        monkeypatch.setattr(
            svc_mercado, "buscar_klines",
            lambda *a, **k: chamadas.append(1) or candles(oscilando(0.01)),
        )
        agora = [0.0]
        relogio = lambda: agora[0]
        svc_risco.risco_das_moedas(("BTC",), relogio)
        svc_risco.risco_das_moedas(("BTC",), relogio)
        assert len(chamadas) == 1

        agora[0] = svc_risco.TTL_CACHE_S + 1
        svc_risco.risco_das_moedas(("BTC",), relogio)
        assert len(chamadas) == 2

    def test_falha_em_uma_moeda_nao_derruba_as_outras(self, monkeypatch):
        def klines(simbolo, **_):
            if simbolo.startswith("ETH"):
                raise svc_mercado.ErroBinance("sem rede")
            return candles(oscilando(0.01))

        monkeypatch.setattr(svc_mercado, "buscar_klines", klines)
        r = svc_risco.risco_das_moedas(("BTC", "ETH"))
        assert set(r["moedas"]) == {"BTC"}

    def test_se_a_atualizacao_falha_mantem_o_ultimo_resultado_bom(self, monkeypatch):
        monkeypatch.setattr(svc_mercado, "buscar_klines", lambda *a, **k: candles(oscilando(0.01)))
        agora = [0.0]
        relogio = lambda: agora[0]
        svc_risco.risco_das_moedas(("BTC",), relogio)

        def falha(*a, **k):
            raise svc_mercado.ErroBinance("fora do ar")

        monkeypatch.setattr(svc_mercado, "buscar_klines", falha)
        agora[0] = svc_risco.TTL_CACHE_S + 1
        assert "BTC" in svc_risco.risco_das_moedas(("BTC",), relogio)["moedas"]


def test_rota_e_publica(monkeypatch):
    monkeypatch.setattr(svc_mercado, "buscar_klines", lambda *a, **k: candles(oscilando(0.01)))
    r = TestClient(app_modulo.app).get("/risco-moedas")
    assert r.status_code == 200
    assert "BTC" in r.json()["moedas"]
