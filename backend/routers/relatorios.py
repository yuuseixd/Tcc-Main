"""Geração dos relatórios PDF."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from db import get_db
from routers.deps import (
    parse_hora_obrigatoria,
    parse_periodo,
    posts_da_hora,
    usuario_atual,
    validar_fonte,
    validar_moeda,
)
from services import correlacao as svc_correlacao
from services import posts as svc_posts
from services import relatorios as svc_relatorios

router = APIRouter()


@router.post("/gerar-relatorio", tags=["relatorio"], dependencies=[Depends(usuario_atual)])
async def gerar_relatorio(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    hora: str = Query(..., description="ISO da hora"),
    indice: float = Query(None, ge=0, le=1),
    db: Session = Depends(get_db),
):
    """Gera o PDF com os posts de uma hora e devolve o link para download."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)
    hora_inicio = parse_hora_obrigatoria(hora)

    posts = posts_da_hora(db, moeda_u, fonte_n, hora_inicio)
    if not posts:
        raise HTTPException(
            status_code=404,
            detail="Nenhum post encontrado nessa hora para gerar o relatório.",
        )

    caminho = await run_in_threadpool(
        svc_relatorios.gerar_relatorio_posts,
        moeda_u, fonte_n, hora_inicio, posts, indice,
    )

    return {
        "mensagem": "Relatório gerado com sucesso!",
        "arquivo": caminho.name,
        "caminho_completo": str(caminho.resolve()),
        "url": f"/relatorios/{caminho.name}",
        "total_posts": len(posts),
    }


@router.post("/gerar-relatorio-correlacao", tags=["relatorio"], dependencies=[Depends(usuario_atual)])
async def gerar_relatorio_correlacao(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    data_inicio: str = Query(None, description="Sem datas: relatório completo"),
    data_fim: str = Query(None),
    db: Session = Depends(get_db),
):
    """Gera o PDF de correlação sentimento x preço (do período ou completo)."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)
    inicio, fim = parse_periodo(data_inicio, data_fim)

    dados = await run_in_threadpool(
        svc_correlacao.calcular_correlacao, db, moeda_u, fonte_n, inicio, fim
    )
    if not dados["pontos"]:
        raise HTTPException(
            status_code=404,
            detail=(
                "Nenhum post analisado nesse período."
                if inicio or fim
                else "Nenhum post analisado ainda. Faça uma coleta antes."
            ),
        )
    dados["volume"] = svc_posts.volume_analisado(db, moeda_u, fonte_n, inicio, fim)

    caminho = await run_in_threadpool(
        svc_relatorios.gerar_relatorio_correlacao, dados
    )

    return {
        "mensagem": "Relatório de correlação gerado com sucesso!",
        "arquivo": caminho.name,
        "caminho_completo": str(caminho.resolve()),
        "url": f"/relatorios/{caminho.name}",
        "total_horas": len(dados["pontos"]),
        "taxa_acerto": dados["resumo"]["taxa_acerto_pct"],
    }
