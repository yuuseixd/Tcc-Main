"""Correlação sentimento x preço e comparação entre redes."""

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from db import get_db
from routers.deps import parse_periodo, usuario_atual, validar_fonte, validar_moeda
from services import comparacao as svc_comparacao
from services import correlacao as svc_correlacao
from services import posts as svc_posts

router = APIRouter()


@router.get("/correlacao", tags=["correlacao"])
async def correlacao_sentimento_preco(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    data_inicio: str = Query(None, description="Data ISO (ex: 2026-03-01)"),
    data_fim: str = Query(None, description="Data ISO (ex: 2026-03-03)"),
    db: Session = Depends(get_db),
):
    """Compara o sentimento social com a variação de preço, hora a hora."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)
    inicio, fim = parse_periodo(data_inicio, data_fim)
    # A função consulta a Binance (I/O bloqueante) — fora do event loop.
    dados = await run_in_threadpool(
        svc_correlacao.calcular_correlacao, db, moeda_u, fonte_n, inicio, fim
    )
    dados["volume"] = svc_posts.volume_analisado(db, moeda_u, fonte_n, inicio, fim)
    return dados


@router.get("/comparacao-fontes", tags=["correlacao"], dependencies=[Depends(usuario_atual)])
async def comparacao_fontes(
    moeda: str = Query("BTC"),
    db: Session = Depends(get_db),
):
    """Compara X e Reddit para a mesma moeda: correlação, concordância e série horária."""
    moeda_u = validar_moeda(moeda)
    return await run_in_threadpool(svc_comparacao.comparar_fontes, db, moeda_u)
