"""Testes dos testes estatísticos e da análise preditiva da correlação.

Executar (a partir da pasta backend/):

    python -m pytest tests/test_estatistica.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from services import correlacao as svc_correlacao
from services import estatistica as est
from services.correlacao import HoraAgregada


class TestBinomial:
    def test_valores_de_referencia_do_scipy(self):
        # Com p = 0,5 a distribuição é simétrica: p-valor = 2·P(X <= min(k, n-k)).
        assert est.p_valor_binomial(7, 10) == pytest.approx(0.3438, abs=1e-4)
        assert est.p_valor_binomial(9, 10) == pytest.approx(0.0215, abs=1e-4)
        assert est.p_valor_binomial(35, 82) == pytest.approx(0.2242, abs=1e-4)

    def test_resultado_central_tem_p_valor_um(self):
        assert est.p_valor_binomial(5, 10) == 1.0

    def test_sem_amostra(self):
        assert est.p_valor_binomial(0, 0) is None


class TestWilson:
    def test_intervalo_contem_a_proporcao_e_fica_em_0_100(self):
        lo, hi = est.intervalo_wilson(35, 82)
        assert lo < 35 / 82 * 100 < hi
        assert (lo, hi) == pytest.approx((32.6, 53.4), abs=0.2)

    def test_extremos_nao_saem_do_intervalo(self):
        lo, hi = est.intervalo_wilson(0, 5)
        assert lo == 0.0 and 0 < hi < 100
        lo, hi = est.intervalo_wilson(5, 5)
        assert 0 < lo < 100 and hi == 100.0

    def test_sem_amostra(self):
        assert est.intervalo_wilson(0, 0) is None


class TestPearson:
    def test_relacao_perfeita_e_significativa(self):
        xs = list(range(30))
        assert est.pearson(xs, [2 * x for x in xs]) == 1.0
        assert est.p_valor_pearson(xs, [2 * x for x in xs]) < 0.01

    def test_ruido_nao_e_significativo(self):
        xs = [1, -1] * 15
        ys = [1, 1, -1, -1] * 7 + [1, 1]
        assert est.p_valor_pearson(xs, ys) > 0.05

    def test_reprodutivel(self):
        xs, ys = [1, 3, 2, 5, 4, 6], [2, 1, 4, 3, 6, 5]
        assert est.p_valor_pearson(xs, ys) == est.p_valor_pearson(xs, ys)


def obs(direcao, retorno, momentum=None, score=None):
    return {
        "direcao": direcao,
        "retorno": retorno,
        "momentum": momentum,
        "score": direcao * 0.5 if score is None else score,
    }


class TestAvaliarHorizonte:
    def test_neutros_e_estaveis_ficam_fora(self):
        r = est.avaliar_horizonte(
            [obs(1, 1.0), obs(0, 1.0), obs(1, 0.01), obs(-1, -1.0)],
            limiar_retorno_pct=0.05,
        )
        assert r["total_comparavel"] == 2
        assert r["acertos"] == 2

    def test_baselines_usam_as_mesmas_horas(self):
        # Preço subiu em 3 de 4 horas: "sempre sobe" acerta 75%.
        r = est.avaliar_horizonte(
            [obs(1, 1.0, 1.0), obs(1, 1.0, -1.0), obs(-1, 1.0, 1.0), obs(-1, -1.0, -1.0)],
            limiar_retorno_pct=0.05,
        )
        assert r["taxa_acerto_pct"] == 75.0
        assert r["baseline_sempre_sobe_pct"] == 75.0
        assert r["baseline_momentum_pct"] == 75.0

    def test_amostra_pequena_nao_e_significativa(self):
        r = est.avaliar_horizonte([obs(1, 1.0)] * 4, limiar_retorno_pct=0.05)
        assert r["taxa_acerto_pct"] == 100.0
        assert r["significativo"] is False


class TestConclusao:
    def _h(self, taxa, p, base=50.0, rotulo="+1h"):
        return {
            "rotulo": rotulo, "total_comparavel": 100, "taxa_acerto_pct": taxa,
            "significativo": p < est.ALFA, "baseline_sempre_sobe_pct": base,
        }

    def test_sem_evidencia(self):
        assert "não há evidência" in est.conclusao([self._h(55, 0.3)])

    def test_sinal_acima_do_acaso_e_da_baseline(self):
        assert "acima do acaso" in est.conclusao([self._h(70, 0.001)])

    def test_acima_do_acaso_mas_abaixo_da_baseline_nao_conta(self):
        # Mercado subiu 80% das horas: 70% de acerto é pior que "sempre sobe".
        assert "não há evidência" in est.conclusao([self._h(70, 0.001, base=80)])

    def test_sinal_contrario(self):
        assert "MENOS que o acaso" in est.conclusao([self._h(30, 0.001)])

    def test_vazio(self):
        assert "Sem horas" in est.conclusao([])


class TestAnalisePreditiva:
    def test_horizontes_usam_fechamento_da_hora_dos_posts(self):
        t0 = datetime(2026, 1, 1, 10)
        hora = HoraAgregada(timestamp=t0, positivos=3)

        def candle(abertura, fechamento):
            return {
                "preco_abertura": abertura,
                "preco_fechamento": fechamento,
                "variacao_pct": (fechamento - abertura) / abertura * 100,
            }

        precos = {
            (t0 - timedelta(hours=1)).isoformat(): candle(100, 99),
            t0.isoformat(): candle(99, 100),
            (t0 + timedelta(hours=1)).isoformat(): candle(100, 110),
        }
        resultado = svc_correlacao.analise_preditiva({t0.isoformat(): hora}, precos)

        por_h = {r["horizonte_h"]: r for r in resultado}
        assert [r["rotulo"] for r in resultado] == ["mesma hora", "+1h", "+4h", "+24h"]
        # Mesma hora: candle 99 -> 100 subiu; momentum = candle anterior (caiu).
        assert por_h[0]["acertos"] == 1
        assert por_h[0]["baseline_momentum_pct"] == 0.0
        # +1h: de 100 (fechamento de t0) para 110.
        assert por_h[1]["acertos"] == 1
        # Sem candle em +4h/+24h: nada a avaliar.
        assert por_h[4]["horas_com_retorno"] == 0
        assert por_h[24]["total_comparavel"] == 0
