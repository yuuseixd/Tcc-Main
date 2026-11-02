"""Preço e sentimento de mercado (Binance) e histórico salvo."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from db import get_db
from models import MarketPoint
from routers.deps import buscar_klines_ou_502, usuario_atual, validar_moeda
from services import mercado as svc_mercado
from services import risco as svc_risco
from utils.tempo import agora_utc, para_iso_utc, parse_data_fim, parse_data_inicio

logger = logging.getLogger("sentcrypto")

router = APIRouter()


@router.get("/sentimento", tags=["sentimento"])
def sentimento_atual(moeda: str = Query("BTC")):
    """Sentimento derivado do último candle horário da Binance."""
    moeda_u = validar_moeda(moeda)
    candles = buscar_klines_ou_502(
        svc_mercado.simbolo_para(moeda_u), intervalo="1h", limite=2
    )

    if not candles:
        raise HTTPException(status_code=404, detail="Nenhum candle retornado.")

    ultimo = candles[-1]
    return {
        "moeda": moeda_u,
        "sentimento_atual": ultimo.sentimento,
        "indice_sentimento": round(ultimo.indice_sentimento, 2),
        "preco": round(ultimo.fechamento, 2),
        "variacao_percentual": round(ultimo.variacao_pct, 4),
        "timestamp_candle": para_iso_utc(ultimo.timestamp),
        "ultimo_update": para_iso_utc(agora_utc()),
    }


@router.get("/risco-moedas", tags=["sentimento"])
async def risco_moedas():
    """Volatilidade e queda máxima de cada moeda nos últimos 365 dias (Binance)."""
    return await run_in_threadpool(svc_risco.risco_das_moedas)


@router.get("/historico-sentimento", tags=["historico"])
def historico_sentimento(
    moeda: str = Query("BTC"),
    limite: int = Query(24, ge=1, le=1000),
    data_inicio: str = Query(None, description="Data ISO (ex: 2026-03-01)"),
    data_fim: str = Query(None, description="Data ISO (ex: 2026-03-03)"),
):
    """Histórico de preço + sentimento consultado ao vivo na Binance."""
    moeda_u = validar_moeda(moeda)

    candles = buscar_klines_ou_502(
        svc_mercado.simbolo_para(moeda_u),
        intervalo="1h",
        limite=limite,
        inicio=parse_data_inicio(data_inicio),
        fim=parse_data_fim(data_fim),
    )

    return {
        "moeda": moeda_u,
        "pontos": [
            {
                "timestamp": para_iso_utc(c.timestamp),
                "preco": round(c.fechamento, 2),
                "indice_sentimento": c.indice_sentimento,
            }
            for c in candles
        ],
    }


@router.get("/historico-db", tags=["historico"])
def historico_db(
    moeda: str = Query("BTC"),
    limite: int = Query(500, ge=1, le=2000),
    data_inicio: str = Query(None),
    data_fim: str = Query(None),
    db: Session = Depends(get_db),
):
    """Histórico salvo em ``market_points``.

    Se o banco estiver vazio para o filtro pedido, busca na Binance e persiste,
    de modo que a primeira visita já mostre dados.
    """
    moeda_u = validar_moeda(moeda)
    inicio = parse_data_inicio(data_inicio)
    fim = parse_data_fim(data_fim)

    def consultar():
        query = db.query(MarketPoint).filter(MarketPoint.moeda == moeda_u)
        if inicio:
            query = query.filter(MarketPoint.timestamp >= inicio)
        if fim:
            query = query.filter(MarketPoint.timestamp <= fim)
        registros = query.order_by(MarketPoint.timestamp.desc()).limit(limite).all()
        registros.reverse()
        return registros

    registros = consultar()

    if not registros:
        try:
            candles = svc_mercado.buscar_klines(
                svc_mercado.simbolo_para(moeda_u),
                intervalo="1h",
                limite=500 if (inicio or fim) else 24,
                inicio=inicio,
                fim=fim,
            )
            novos = svc_mercado.salvar_candles(db, moeda_u, candles)
            logger.info("Sincronizou %d novos pontos de %s.", novos, moeda_u)
            registros = consultar()
        except svc_mercado.ErroBinance as e:
            # Sem dados locais nem remotos: devolve vazio em vez de derrubar
            # o dashboard inteiro.
            logger.warning("Falha ao popular histórico de %s: %s", moeda_u, e)

    return {
        "moeda": moeda_u,
        "pontos": [
            {
                "timestamp": para_iso_utc(r.timestamp),
                "preco": round(r.preco, 2) if r.preco is not None else None,
                "indice_sentimento": (
                    round(r.indice_sentimento, 2)
                    if r.indice_sentimento is not None
                    else None
                ),
            }
            for r in registros
        ],
    }


@router.post("/sync-binance", tags=["historico"], dependencies=[Depends(usuario_atual)])
def sync_binance(
    moeda: str = Query("BTC"),
    dias: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Baixa os últimos N dias de candles e grava no banco (sem duplicar)."""
    moeda_u = validar_moeda(moeda)

    from datetime import timedelta

    inicio = agora_utc() - timedelta(days=dias)
    candles = buscar_klines_ou_502(
        svc_mercado.simbolo_para(moeda_u),
        intervalo="1h",
        limite=dias * 24,
        inicio=inicio,
    )

    novos = svc_mercado.salvar_candles(db, moeda_u, candles)

    return {
        "mensagem": f"Sincronização concluída: {novos} novos pontos salvos.",
        "moeda": moeda_u,
        "total_klines": len(candles),
        "novos": novos,
        "atualizados": len(candles) - novos,
    }
