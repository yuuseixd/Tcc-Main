"""Leitura de padrões gráficos (price action) sobre os candles da Binance."""

import json
import time
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from config import BASE_DIR
from routers.deps import validar_moeda
from services import mercado as svc_mercado
from services import padroes as svc_padroes
from utils.tempo import agora_utc

router = APIRouter()

# 4h é o mínimo: abaixo disso os padrões viram ruído de curtíssimo prazo.
HORAS_POR_CANDLE = {"4h": 4, "12h": 12, "1d": 24, "1w": 168}
# Candles analisados e exibidos: o bastante para padrões grandes aparecerem
# inteiros (no diário, ~4 anos; no semanal, todo o histórico da Binance).
CANDLES_ANALISADOS = 1500
ARQUIVO_BACKTEST = BASE_DIR / "backtest_padroes.json"
CACHE_TTL_S = 300

# {(moeda, intervalo): (momento, resposta)} — a Binance leva ~2 s por consulta
# e os candles fechados não mudam em minutos.
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def limpar_cache() -> None:
    _CACHE.clear()


def _validar_intervalo(intervalo: str) -> None:
    if intervalo not in HORAS_POR_CANDLE:
        raise HTTPException(status_code=400,
                            detail=f"Intervalo inválido. Use: {', '.join(HORAS_POR_CANDLE)}.")


def _backtest(moeda: str, intervalo: str) -> dict | None:
    """Resultado de ``tools/backtest_padroes.py`` para o intervalo, se existir."""
    try:
        dados = json.loads(ARQUIVO_BACKTEST.read_text(encoding="utf-8"))
        bloco = dados["intervalos"][intervalo]
    except (OSError, ValueError, KeyError):
        return None
    return {
        "gerado_em": dados.get("gerado_em"),
        "moedas": dados.get("moedas", []),
        "periodo_moeda": bloco.get("periodo", {}).get(moeda),
        "tipos": {
            tipo: {"geral": r["geral"], "moeda": r["por_moeda"].get(moeda)}
            for tipo, r in bloco["tipos"].items()
        },
    }


def _analisar(moeda: str, intervalo: str) -> dict:
    fim = agora_utc()
    inicio = fim - timedelta(hours=HORAS_POR_CANDLE[intervalo] * CANDLES_ANALISADOS)
    candles = svc_mercado.buscar_klines_intervalo(
        svc_mercado.simbolo_para(moeda), inicio, fim, intervalo=intervalo,
        max_requisicoes=3,
    )
    if not candles:
        raise HTTPException(status_code=502, detail="A Binance não devolveu candles.")

    return {
        "moeda": moeda,
        "intervalo": intervalo,
        "candles": [
            {"t": c.timestamp.isoformat() + "Z", "c": c.fechamento,
             "h": c.maxima, "l": c.minima}
            for c in candles
        ],
        **svc_padroes.analisar(candles),
    }


@router.get("/padroes", tags=["padroes"])
async def padroes_graficos(
    moeda: str = Query("BTC"),
    intervalo: str = Query("4h", description="4h, 12h, 1d ou 1w"),
):
    """Padrões encontrados nos últimos candles, os ativos agora e o backtest."""
    moeda_u = validar_moeda(moeda)
    _validar_intervalo(intervalo)

    chave = (moeda_u, intervalo)
    em_cache = _CACHE.get(chave)
    if em_cache and time.time() - em_cache[0] < CACHE_TTL_S:
        resposta = em_cache[1]
    else:
        try:
            resposta = await run_in_threadpool(_analisar, moeda_u, intervalo)
        except svc_mercado.ErroBinance as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        _CACHE[chave] = (time.time(), resposta)

    # O backtest é lido a cada chamada: regenerá-lo não exige reiniciar a API.
    return {**resposta, "catalogo": svc_padroes.catalogo(),
            "backtest": _backtest(moeda_u, intervalo)}


@router.get("/padroes/ranking", tags=["padroes"])
def ranking_padroes(
    moeda: str = Query("BTC"),
    intervalo: str = Query("4h", description="4h, 12h, 1d ou 1w"),
):
    """Desempenho de cada padrão no backtest do intervalo (geral e da moeda)."""
    moeda_u = validar_moeda(moeda)
    _validar_intervalo(intervalo)
    bt = _backtest(moeda_u, intervalo)
    if bt is None:
        raise HTTPException(
            status_code=404,
            detail="Backtest ainda não gerado. Rode: python tools/backtest_padroes.py",
        )
    return {"moeda": moeda_u, "intervalo": intervalo,
            "catalogo": svc_padroes.catalogo(), **bt}
