"""Simulação de investimento (backtest)."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from db import get_db
from routers.deps import usuario_atual, validar_moeda
from schemas import SimulacaoInvestimentoRequest
from services import simulacao as svc_simulacao
from utils.tempo import parse_data_fim, parse_data_inicio

router = APIRouter()


@router.post("/simulacao/investimento", tags=["simulacao"], dependencies=[Depends(usuario_atual)])
async def simular_investimento(
    body: SimulacaoInvestimentoRequest, db: Session = Depends(get_db)
):
    """Backtest de compra/venda simulada — modo FOMO (sentimento) ou Flat (só preço).

    FOMO roda sobre ``social_posts`` já salvos (não dispara coleta ao vivo);
    Flat ignora posts e perfis. Ver ``services/simulacao.py`` para a regra
    de decisão completa.
    """
    moeda_u = validar_moeda(body.moeda)

    inicio = parse_data_inicio(body.data_inicio)
    fim = parse_data_fim(body.data_fim)
    if not inicio or not fim or inicio >= fim:
        raise HTTPException(
            status_code=400,
            detail="Período inválido: informe data_inicio anterior a data_fim.",
        )
    if (fim - inicio).days > svc_simulacao.MAX_DIAS_SIMULACAO:
        raise HTTPException(
            status_code=400,
            detail=f"Período máximo da simulação é de "
            f"{svc_simulacao.MAX_DIAS_SIMULACAO} dias.",
        )
    if body.valor_por_compra > body.capital_inicial:
        raise HTTPException(
            status_code=400,
            detail="Valor por compra não pode ser maior que o capital inicial.",
        )

    return await run_in_threadpool(
        svc_simulacao.simular_investimento,
        db, moeda_u, body.perfis or [], inicio, fim,
        body.capital_inicial, body.valor_por_compra,
        body.percentual_lucro_venda, body.percentual_queda_compra,
        body.percentual_perda_aceita, body.modo, body.fonte,
        body.taxa_operacao_pct,
    )
