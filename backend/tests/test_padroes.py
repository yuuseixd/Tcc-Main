"""Testes da leitura de padrões gráficos (OCOI, OCO, topos/fundos, retângulos,
triângulos e flâmulas).

As séries são sintéticas e lineares por trechos: dá para saber exatamente
onde estão os pivôs, a linha de rompimento e o alvo. Os padrões de baixa são
testados com o espelho (300 - preço) da série do padrão de alta
correspondente — o que também confere a inversão de sinal do código.

Executar (a partir da pasta backend/):

    python -m pytest tests/test_padroes.py -v
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from routers import padroes as rotas_padroes
from services import mercado as svc_mercado
from services import padroes as P
from services.mercado import Candle

BASE = datetime(2026, 1, 1)
LIMIAR = [0.01]
ESPELHO = 300


def serie(inicio: float, trechos: list[tuple[int, float]]) -> list[Candle]:
    """Candles com fechamento em linha reta entre os pontos dados.

    ``trechos``: (candles até chegar, preço de chegada). Máxima e mínima
    iguais ao fechamento, para as contas do teste serem exatas.
    """
    precos = [inicio]
    for passos, alvo in trechos:
        de = precos[-1]
        precos += [de + (alvo - de) * k / passos for k in range(1, passos + 1)]
    return [
        Candle(timestamp=BASE + timedelta(hours=4 * i), abertura=p, fechamento=p,
               maxima=p, minima=p)
        for i, p in enumerate(precos)
    ]


def espelho(candles: list[Candle]) -> list[Candle]:
    """Mesma série de cabeça para baixo: topos viram fundos e vice-versa."""
    return [Candle(c.timestamp, ESPELHO - c.abertura, ESPELHO - c.fechamento,
                   maxima=ESPELHO - c.minima, minima=ESPELHO - c.maxima)
            for c in candles]


def do_tipo(candles, tipo: str) -> list[dict]:
    return [p for p in P.detectar(candles, LIMIAR) if p["tipo"] == tipo]


def pivo(padrao: dict, nome: str) -> dict:
    return next(p for p in padrao["pivos"] if p["nome"] == nome)


# Queda até o ombro esquerdo (100), topo (120), cabeça (90), topo (120) e
# ombro direito (100). Pescoço plano em 120; profundidade da cabeça 30.
OCOI = [(10, 100), (5, 120), (5, 90), (5, 120), (5, 100)]


def ocoi_com(*depois: tuple[int, float], inicio: float = 150, formato=OCOI):
    return serie(inicio, formato + list(depois))


# Séries de gabarito de cada padrão de alta (rompem e atingem o alvo).
FUNDO_DUPLO = serie(150, [(10, 100), (5, 120), (5, 101), (5, 125), (10, 150)])
FUNDO_TRIPLO = serie(150, [(10, 100), (5, 120), (5, 101), (5, 119), (5, 100.5),
                           (5, 125), (10, 150)])
RETANGULO = serie(60, [(20, 120), (5, 100), (5, 119.5), (5, 100.5), (5, 125), (10, 150)])
TRIANGULO = serie(99, [(10, 120), (5, 100), (5, 120), (5, 108), (5, 125), (10, 150)])
FLAMULA = serie(100, [(5, 150), (3, 140), (3, 147), (3, 143), (3, 152), (10, 210)])


class TestZigzag:
    def test_alterna_topos_e_fundos(self):
        pivos = P.zigzag(ocoi_com((5, 125)), 0.01)
        assert [(p.i, p.preco, p.tipo) for p in pivos] == [
            (0, 150, "topo"), (10, 100, "fundo"), (15, 120, "topo"),
            (20, 90, "fundo"), (25, 120, "topo"), (30, 100, "fundo"),
        ]

    def test_ignora_oscilacao_menor_que_o_limiar(self):
        candles = serie(100, [(5, 100.5), (5, 100.1), (5, 110), (5, 100)])
        assert [p.preco for p in P.zigzag(candles, 0.02)] == [100, 110]

    def test_ultimo_extremo_nao_confirmado_fica_de_fora(self):
        pivos = P.zigzag(serie(100, [(5, 120), (5, 119.5)]), 0.01)
        assert pivos == [P.Pivo(0, 100, "fundo")]

    def test_limiares_proporcionais_a_volatilidade(self):
        candles = [Candle(BASE, 100, 100, maxima=101, minima=99)] * 10
        assert P.limiares_automaticos(candles) == pytest.approx([0.04, 0.06, 0.08])

    def test_limiar_tem_piso(self):
        assert P.limiares_automaticos(serie(100, [(5, 100)])) == [P.LIMIAR_MINIMO]


class TestOcoi:
    def test_padrao_confirmado_que_atinge_o_alvo(self):
        [p] = P.detectar_ocoi(ocoi_com((5, 125), (10, 160)), LIMIAR)
        assert p["direcao"] == "alta"
        assert pivo(p, "cabeca")["i"] == 20 and pivo(p, "cabeca")["preco"] == 90
        assert p["linhas"] == [{"nome": "Linha de pescoço", "i1": 15, "p1": 120,
                                "i2": 25, "p2": 120}]
        assert p["status"] == "confirmado"
        assert p["i_rompimento"] == 35          # 1º fechamento acima de 120
        assert p["entrada"] == 125
        assert p["alvo"] == 150                 # 120 + (120 - 90)
        assert p["invalidacao"] == 100          # mínima do ombro direito
        assert p["alvo_pct"] == pytest.approx(20.0)
        assert p["stop_pct"] == pytest.approx(20.0)
        assert p["desfecho"] == "alvo"
        assert p["candles_ate_desfecho"] == 8   # 125 -> 150 a 3,5 por candle

    def test_padrao_que_bate_no_stop(self):
        [p] = P.detectar_ocoi(ocoi_com((5, 125), (10, 95)), LIMIAR)
        assert p["desfecho"] == "stop"
        # Retorno no candle do stop (1º fechamento a cair até 100: 98).
        assert p["retorno_pct"] == pytest.approx((98 / 125 - 1) * 100)

    def test_invalidado_antes_do_rompimento(self):
        [p] = P.detectar_ocoi(ocoi_com((3, 110), (3, 95)), LIMIAR)
        assert p["status"] == "invalidado"

    def test_em_formacao_ate_o_fim_da_serie(self):
        [p] = P.detectar_ocoi(ocoi_com((4, 112)), LIMIAR)
        assert p["status"] == "em_formacao"
        assert p["gatilho"] == 120
        assert p["alvo"] == 150
        assert P.padrao_atual([p]) is p

    def test_expira_sem_romper(self):
        laterais = [(3, 115), (3, 105)] * 5
        [p] = P.detectar_ocoi(ocoi_com(*laterais), LIMIAR)
        assert p["status"] == "expirado"

    def test_confirmado_em_andamento_e_ativo(self):
        [p] = P.detectar_ocoi(ocoi_com((5, 125), (3, 130)), LIMIAR)
        assert p["desfecho"] == "em_andamento"
        assert P.ativo(p)

    def test_padrao_encerrado_nao_e_ativo(self):
        [p] = P.detectar_ocoi(ocoi_com((5, 125), (10, 160)), LIMIAR)
        assert not P.ativo(p)
        assert P.padrao_atual([p]) is None

    def test_pescoco_inclinado(self):
        formato = [(10, 100), (5, 120), (5, 90), (5, 126), (5, 104)]
        [p] = P.detectar_ocoi(ocoi_com((5, 135), formato=formato), LIMIAR)
        # Pescoço sobe 0,6 por candle: em 35 vale 126 + 6 = 132.
        assert p["i_rompimento"] == 35
        assert p["gatilho"] == pytest.approx(132)
        # Profundidade medida no pescoço sobre a cabeça (123 - 90 = 33).
        assert p["alvo"] == pytest.approx(132 + 33)

    def test_varias_escalas_contam_o_padrao_uma_vez(self):
        candles = ocoi_com((5, 125), (10, 160))
        assert len(P.detectar_ocoi(candles, [0.01, 0.015, 0.02])) == 1


class TestOcoiRegrasQueRejeitam:
    def detecta(self, formato, inicio=150):
        return P.detectar_ocoi(ocoi_com((5, 125), inicio=inicio, formato=formato), LIMIAR)

    def test_cabeca_que_nao_e_o_ponto_mais_baixo(self):
        assert self.detecta([(10, 100), (5, 120), (5, 102), (5, 120), (5, 100)]) == []

    def test_cabeca_pouco_mais_funda_que_os_ombros(self):
        assert self.detecta([(10, 100), (5, 120), (5, 98), (5, 120), (5, 100)]) == []

    def test_ombros_muito_diferentes(self):
        assert self.detecta([(10, 100), (5, 120), (5, 90), (5, 120), (5, 116)]) == []

    def test_lados_com_duracoes_muito_diferentes(self):
        assert self.detecta([(10, 100), (5, 120), (5, 90), (40, 120), (5, 100)]) == []

    def test_sem_queda_antes_do_padrao(self):
        assert self.detecta(OCOI, inicio=110) == []


class TestOco:
    """OCO = OCOI espelhado: mesmo código, sinal invertido."""

    def test_espelho_do_ocoi(self):
        [p] = do_tipo(espelho(ocoi_com((5, 125), (10, 160))), "oco")
        assert p["direcao"] == "baixa"
        assert pivo(p, "cabeca")["preco"] == 210     # 300 - 90
        assert p["entrada"] == 175                   # 300 - 125
        assert p["alvo"] == 150                      # pescoço 180 - 30
        assert p["invalidacao"] == 200               # máxima do ombro direito
        assert p["alvo_pct"] == pytest.approx((1 - 150 / 175) * 100)
        assert p["stop_pct"] == pytest.approx((200 / 175 - 1) * 100)
        assert p["desfecho"] == "alvo"
        assert p["retorno_pct"] > 0                  # caiu: a favor da operação

    def test_oco_que_bate_no_stop(self):
        [p] = do_tipo(espelho(ocoi_com((5, 125), (10, 95))), "oco")
        assert p["desfecho"] == "stop"
        assert p["retorno_pct"] < 0

    def test_ocoi_nao_vira_oco(self):
        assert do_tipo(ocoi_com((5, 125), (10, 160)), "oco") == []


class TestTopoEFundo:
    def test_fundo_duplo(self):
        [p] = do_tipo(FUNDO_DUPLO, "fundo_duplo")
        assert [x["preco"] for x in p["pivos"]] == [100, 120, 101]
        assert p["gatilho"] == 120 and p["alvo"] == 140 and p["invalidacao"] == 100
        assert p["i_rompimento"] == 24
        assert p["desfecho"] == "alvo"

    def test_topo_duplo_e_o_espelho(self):
        [p] = do_tipo(espelho(FUNDO_DUPLO), "topo_duplo")
        assert p["direcao"] == "baixa"
        assert p["gatilho"] == 180 and p["alvo"] == 160 and p["invalidacao"] == 200
        assert p["desfecho"] == "alvo"

    def test_fundo_triplo_engole_os_duplos(self):
        padroes = P.detectar(FUNDO_TRIPLO, LIMIAR)
        [p] = [x for x in padroes if x["tipo"] == "fundo_triplo"]
        assert [x["preco"] for x in p["pivos"]] == [100, 120, 101, 119, 100.5]
        assert p["gatilho"] == 120 and p["alvo"] == 140
        assert p["desfecho"] == "alvo"
        assert not [x for x in padroes if x["tipo"] == "fundo_duplo"]

    def test_topo_triplo_e_o_espelho(self):
        [p] = do_tipo(espelho(FUNDO_TRIPLO), "topo_triplo")
        assert p["alvo"] == 160 and p["desfecho"] == "alvo"

    def test_fundos_em_niveis_diferentes_nao_formam_duplo(self):
        candles = serie(150, [(10, 100), (5, 120), (5, 108), (5, 125), (10, 150)])
        assert do_tipo(candles, "fundo_duplo") == []

    def test_fundo_duplo_exige_queda_antes(self):
        candles = serie(110, [(10, 100), (5, 120), (5, 101), (5, 125), (10, 150)])
        assert do_tipo(candles, "fundo_duplo") == []


class TestRetangulos:
    def test_retangulo_de_alta(self):
        [p] = do_tipo(RETANGULO, "retangulo_alta")
        assert {x["nome"] for x in p["linhas"]} == {"Resistência", "Suporte"}
        assert p["gatilho"] == 120 and p["invalidacao"] == 100
        assert p["alvo"] == 140                   # 120 + altura 20
        assert p["desfecho"] == "alvo"

    def test_retangulo_de_baixa_e_o_espelho(self):
        [p] = do_tipo(espelho(RETANGULO), "retangulo_baixa")
        assert p["gatilho"] == 180 and p["alvo"] == 160 and p["invalidacao"] == 200
        assert p["desfecho"] == "alvo"

    def test_retangulo_de_alta_exige_alta_antes(self):
        # Mesma lateralização, mas vindo de cima: não é retângulo de alta.
        assert do_tipo(espelho(RETANGULO), "retangulo_alta") == []

    def test_lateral_que_nao_rompe_e_invalidada(self):
        candles = serie(60, [(20, 120), (5, 100), (5, 119.5), (5, 100.5), (5, 110), (5, 95)])
        [p] = do_tipo(candles, "retangulo_alta")
        assert p["status"] == "invalidado"


class TestTriangulos:
    def test_triangulo_ascendente(self):
        [p] = do_tipo(TRIANGULO, "triangulo_ascendente")
        assert [x["preco"] for x in p["pivos"]] == [120, 100, 120, 108]
        assert p["gatilho"] == 120
        assert p["alvo"] == 140                   # 120 + (120 - 100)
        assert p["invalidacao"] == 108            # último fundo
        assert p["desfecho"] == "alvo"

    def test_triangulo_descendente_e_o_espelho(self):
        [p] = do_tipo(espelho(TRIANGULO), "triangulo_descendente")
        assert p["gatilho"] == 180 and p["alvo"] == 160 and p["invalidacao"] == 192
        assert p["desfecho"] == "alvo"

    def test_fundos_quase_planos_nao_sao_triangulo(self):
        # Fundo sobe só 1 (5% da altura): é lateralização, não triângulo.
        candles = serie(99, [(10, 120), (5, 100), (5, 120), (5, 101), (5, 125), (10, 150)])
        assert do_tipo(candles, "triangulo_ascendente") == []


class TestFlamulas:
    def test_flamula_de_alta(self):
        [p] = do_tipo(FLAMULA, "flamula_alta")
        assert p["altura"] == 50                  # mastro 100 -> 150
        assert p["i_rompimento"] == 15            # 146 > linha (145)
        assert p["gatilho"] == pytest.approx(145)
        assert p["alvo"] == pytest.approx(195)
        assert p["invalidacao"] == 143
        assert p["desfecho"] == "alvo"

    def test_flamula_de_baixa_e_o_espelho(self):
        [p] = do_tipo(espelho(FLAMULA), "flamula_baixa")
        assert p["alvo"] == pytest.approx(105) and p["desfecho"] == "alvo"

    def test_recuo_grande_demais_nao_e_flamula(self):
        candles = serie(100, [(5, 150), (3, 120), (3, 147), (3, 125), (3, 152), (10, 210)])
        assert do_tipo(candles, "flamula_alta") == []

    def test_consolidacao_que_nao_converge(self):
        candles = serie(100, [(5, 150), (3, 140), (3, 151), (3, 143), (3, 156), (10, 210)])
        assert do_tipo(candles, "flamula_alta") == []


class TestDesfecho:
    def test_alvo_e_stop_no_mesmo_candle_conta_stop(self):
        candles = [Candle(BASE, 100, 100, maxima=100, minima=100),
                   Candle(BASE, 100, 100, maxima=120, minima=80)]
        assert P._desfecho(candles, 0, 110, 90, 5) == ("stop", 1)
        assert P._desfecho(candles, 0, 90, 110, 5, P.BAIXA) == ("stop", 1)

    def test_operacao_de_baixa(self):
        candles = serie(100, [(5, 80)])
        assert P._desfecho(candles, 0, 90, 105, 10, P.BAIXA) == ("alvo", 3)

    def test_fim_dos_dados_antes_do_horizonte(self):
        candles = serie(100, [(3, 101)])
        assert P._desfecho(candles, 0, 200, 50, 10) == ("em_andamento", 3)

    def test_horizonte_sem_alvo_nem_stop(self):
        candles = serie(100, [(10, 101)])
        assert P._desfecho(candles, 0, 200, 50, 4) == ("sem_desfecho", 4)


class TestAvaliacao:
    def test_baseline_em_serie_que_so_sobe(self):
        candles = serie(100, [(60, 200)])
        assert P._taxa_baseline(candles, 1.0, 1.0, 5) == 1.0
        # Vendido numa série que só sobe: nunca acerta.
        assert P._taxa_baseline(candles, 1.0, 1.0, 5, P.BAIXA) == 0.0

    def test_resumo_compara_com_a_baseline(self):
        def padrao(desfecho):
            return {"status": "confirmado", "desfecho": desfecho, "baseline": 0.3,
                    "alvo_pct": 10.0, "stop_pct": 5.0, "candles_ate_desfecho": 4}

        padroes = [padrao("alvo")] * 25 + [padrao("stop")] * 15 + [
            {"status": "invalidado"}, {"status": "confirmado", "desfecho": "em_andamento"}]
        r = P.resumir(padroes)
        assert r["padroes_encontrados"] == 42
        assert r["total_confirmado"] == 41
        assert r["avaliados"] == 40              # em andamento fica de fora
        assert r["acertos"] == 25
        assert r["taxa_acerto_pct"] == pytest.approx(62.5)
        assert r["baseline_pct"] == pytest.approx(30.0)
        assert r["vantagem_pp"] == pytest.approx(32.5)
        assert r["p_valor"] < 0.001
        assert r["significativo"] is True
        assert r["candles_medios_ate_alvo"] == 4

    def test_resumo_vazio(self):
        r = P.resumir([])
        assert r["avaliados"] == 0
        assert r["taxa_acerto_pct"] is None
        assert r["vantagem_pp"] is None
        assert r["significativo"] is False

    def test_anotar_baseline_usa_a_direcao(self):
        candles = serie(100, [(80, 200)])
        compra = {"status": "confirmado", "desfecho": "alvo", "direcao": "alta",
                  "alvo_pct": 1.0, "stop_pct": 1.0, "horizonte": 5}
        venda = {**compra, "direcao": "baixa"}
        P.anotar_baseline(candles, [compra, venda])
        assert compra["baseline"] == 1.0 and venda["baseline"] == 0.0

    def test_analisar_lista_os_ativos(self):
        r = P.analisar(ocoi_com((5, 125), (3, 130)))
        ocoi = [k for k, p in enumerate(r["padroes"]) if p["tipo"] == "ocoi"]
        assert ocoi and ocoi[0] in r["ativos"]

    def test_catalogo_tem_os_12_padroes(self):
        cat = P.catalogo()
        assert len(cat) == 12
        assert {c["direcao"] for c in cat} == {"alta", "baixa"}
        assert sum(c["direcao"] == "alta" for c in cat) == 6


class TestEndpoint:
    @pytest.fixture
    def cliente(self, monkeypatch, tmp_path):
        monkeypatch.setattr(svc_mercado, "buscar_klines_intervalo",
                            lambda *a, **k: ocoi_com((5, 125), (3, 130)))
        monkeypatch.setattr(rotas_padroes, "ARQUIVO_BACKTEST", tmp_path / "bt.json")
        import app
        with TestClient(app.app) as c:
            yield c

    def salvar_backtest(self, tmp_path):
        resumo = {"avaliados": 10, "vantagem_pp": 5.0}
        (tmp_path / "bt.json").write_text(json.dumps({
            "gerado_em": "2026-11-04T00:00:00Z", "moedas": ["BTC"],
            "intervalos": {"4h": {
                "tipos": {"ocoi": {"geral": resumo, "por_moeda": {"BTC": {"avaliados": 3}}}},
                "periodo": {"BTC": "2024-01-01 a 2026-11-04"}}},
        }), encoding="utf-8")

    def test_resposta(self, cliente):
        r = cliente.get("/padroes?moeda=btc&intervalo=4h")
        assert r.status_code == 200
        dados = r.json()
        assert dados["moeda"] == "BTC"
        assert len(dados["candles"]) == 39
        assert dados["candles"][0] == {"t": "2026-01-01T00:00:00Z", "c": 150, "h": 150, "l": 150}
        assert len(dados["catalogo"]) == 12
        ocoi = [p for p in dados["padroes"] if p["tipo"] == "ocoi"]
        assert pivo(ocoi[0], "cabeca")["timestamp"] == "2026-01-04T08:00:00Z"
        assert dados["backtest"] is None

    def test_le_o_backtest_salvo(self, cliente, tmp_path):
        self.salvar_backtest(tmp_path)
        bt = cliente.get("/padroes?moeda=BTC&intervalo=4h").json()["backtest"]
        assert bt["tipos"]["ocoi"]["geral"]["avaliados"] == 10
        assert bt["tipos"]["ocoi"]["moeda"]["avaliados"] == 3
        assert bt["periodo_moeda"] == "2024-01-01 a 2026-11-04"

    def test_ranking(self, cliente, tmp_path):
        assert cliente.get("/padroes/ranking?intervalo=4h").status_code == 404
        self.salvar_backtest(tmp_path)
        r = cliente.get("/padroes/ranking?moeda=BTC&intervalo=4h").json()
        assert r["tipos"]["ocoi"]["geral"]["vantagem_pp"] == 5.0
        assert len(r["catalogo"]) == 12

    def test_intervalo_invalido(self, cliente):
        assert cliente.get("/padroes?intervalo=15m").status_code == 400
        # 4h é o menor intervalo aceito.
        assert cliente.get("/padroes?intervalo=1h").status_code == 400
        assert cliente.get("/padroes/ranking?intervalo=15m").status_code == 400

    def test_moeda_invalida(self, cliente):
        assert cliente.get("/padroes?moeda=XYZ").status_code == 400

    def test_binance_fora_do_ar(self, cliente, monkeypatch):
        def falha(*_a, **_k):
            raise svc_mercado.ErroBinance("timeout")

        monkeypatch.setattr(svc_mercado, "buscar_klines_intervalo", falha)
        assert cliente.get("/padroes?moeda=ETH").status_code == 502


class TestCandleDaBinance:
    def test_le_maxima_e_minima(self, monkeypatch):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return [[1767225600000, "100", "110", "95", "105", "1"]]

        monkeypatch.setattr(svc_mercado.requests, "get", lambda *a, **k: Resp())
        [c] = svc_mercado.buscar_klines("BTCUSDT")
        assert (c.abertura, c.maxima, c.minima, c.fechamento) == (100, 110, 95, 105)
