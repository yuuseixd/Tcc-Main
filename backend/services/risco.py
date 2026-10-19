"""Risco de cada moeda, medido nos preços reais da Binance.

Em vez de uma classificação escrita à mão, o risco vem de duas medidas sobre os
últimos 365 dias de fechamentos diários:

* **Volatilidade anualizada**: desvio-padrão dos retornos logarítmicos diários
  vezes a raiz de 365, em %. Mede o tamanho típico das oscilações.
* **Queda máxima**: a maior queda entre um pico e o fundo seguinte, em %. Mede
  o pior tombo que quem comprou no topo teria enfrentado.

A classe (baixo, médio, alto) usa só a volatilidade, com cortes redondos de 50%
e 70% ao ano. Os cortes são uma escolha do projeto, não uma norma; o que é
medido são os números. A classificação é **relativa entre criptomoedas**: mesmo
a de menor volatilidade oscila muito mais do que a renda fixa.

Inspiração normativa: a Resolução CVM 30 (art. 5º, parágrafo único, I) manda
considerar "os riscos associados ao produto e seus ativos subjacentes" ao
classificar produtos, e a ANBIMA liga cada perfil de investidor a uma pontuação
máxima de risco do produto. Aqui o perfil tolera até uma classe de risco.
"""

import logging
import math
import statistics
import threading
import time

from config import MOEDAS_SUPORTADAS
from services import mercado

logger = logging.getLogger("sentcrypto.risco")

JANELA_DIAS = 365
CORTE_BAIXO = 50.0   # volatilidade anual (%) até aqui: risco baixo
CORTE_MEDIO = 70.0   # até aqui: médio; acima: alto
TTL_CACHE_S = 6 * 3600
MIN_DIAS = 30        # com menos preços que isso, não classificamos

_cache: dict = {"quando": 0.0, "dados": None}
_trava = threading.Lock()


def classificar(volatilidade_anual_pct: float) -> str:
    if volatilidade_anual_pct <= CORTE_BAIXO:
        return "baixo"
    if volatilidade_anual_pct <= CORTE_MEDIO:
        return "médio"
    return "alto"


def metricas_de_precos(fechamentos: list[float]) -> dict | None:
    """Volatilidade anualizada e queda máxima de uma série de fechamentos diários."""
    precos = [p for p in fechamentos if p and p > 0]
    if len(precos) < MIN_DIAS:
        return None

    retornos = [math.log(precos[i] / precos[i - 1]) for i in range(1, len(precos))]
    volatilidade = statistics.stdev(retornos) * math.sqrt(365) * 100

    pico, queda = precos[0], 0.0
    for p in precos:
        pico = max(pico, p)
        queda = min(queda, p / pico - 1)

    return {
        "volatilidade_anual_pct": round(volatilidade, 1),
        "queda_maxima_pct": round(queda * 100, 1),
        "dias": len(precos),
        "risco": classificar(volatilidade),
    }


def _medir(moedas: tuple[str, ...]) -> dict:
    resultado: dict[str, dict] = {}
    for moeda in moedas:
        try:
            candles = mercado.buscar_klines(
                mercado.simbolo_para(moeda), intervalo="1d", limite=JANELA_DIAS
            )
        except mercado.ErroBinance as e:
            logger.warning("Sem preços para medir o risco de %s: %s", moeda, e)
            continue
        medidas = metricas_de_precos([c.fechamento for c in candles])
        if medidas:
            resultado[moeda] = medidas
    return resultado


def risco_das_moedas(moedas: tuple[str, ...] | None = None, relogio=time.monotonic) -> dict:
    """Risco medido de cada moeda, com cache de algumas horas (a Binance limita
    requisições e o número muda devagar)."""
    moedas = tuple(moedas or MOEDAS_SUPORTADAS)
    with _trava:
        agora = relogio()
        valido = (
            _cache["dados"] is not None
            and agora - _cache["quando"] < TTL_CACHE_S
            and set(moedas) <= set(_cache["dados"]["moedas"])
        )
        if not valido:
            medidas = _medir(moedas)
            if medidas or _cache["dados"] is None:
                _cache["dados"] = {
                    "moedas": medidas,
                    "metodologia": {
                        "janela_dias": JANELA_DIAS,
                        "cortes_volatilidade_pct": {"baixo_ate": CORTE_BAIXO, "medio_ate": CORTE_MEDIO},
                        "observacao": (
                            "Classificação relativa entre criptomoedas, calculada com os "
                            "preços diários da Binance. Todas as criptomoedas oscilam muito "
                            "mais do que a renda fixa. Não é recomendação de investimento."
                        ),
                    },
                }
                _cache["quando"] = agora
        return _cache["dados"]


def limpar_cache() -> None:
    _cache["quando"], _cache["dados"] = 0.0, None
