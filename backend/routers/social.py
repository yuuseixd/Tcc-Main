"""Histórico dos posts sociais agregado por hora."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from config import DIAS_JANELA_PADRAO
from db import get_db
from models import SocialPost
from routers.deps import (
    parse_hora_obrigatoria,
    posts_da_hora,
    usuario_atual,
    validar_fonte,
    validar_moeda,
)
from services import correlacao as svc_correlacao
from services import sentimento as svc_sentimento
from utils.tempo import para_iso_utc, parse_data_fim, parse_data_inicio

router = APIRouter()


@router.get("/historico-social", tags=["historico"])
def historico_social(
    moeda: str = Query("BTC"),
    fonte: str = Query("Reddit"),
    limite: int = Query(500, ge=1, le=2000),
    data_inicio: str = Query(None),
    data_fim: str = Query(None),
    db: Session = Depends(get_db),
):
    """Sentimento dos posts sociais agregado por hora."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)

    query = db.query(SocialPost).filter(
        SocialPost.moeda == moeda_u,
        SocialPost.fonte == fonte_n,
        # Posts fora do domínio cripto nunca entram nas métricas; filtrar no
        # banco evita trazer linhas que seriam descartadas em memória.
        SocialPost.sentimento != svc_sentimento.NULO,
    )

    inicio = parse_data_inicio(data_inicio)
    fim = parse_data_fim(data_fim)
    if inicio:
        query = query.filter(SocialPost.timestamp_post >= inicio)
    if fim:
        query = query.filter(SocialPost.timestamp_post <= fim)

    posts = query.order_by(SocialPost.timestamp_post.desc()).limit(limite).all()

    agrupado = svc_correlacao.agregar_posts_por_hora(posts)

    # Perfis costumam ter alguns tweets antigos fixados ou republicados. Sem
    # filtro de data, essas poucas horas de anos atrás esticavam o eixo do
    # gráfico por todo o período e comprimiam os dados recentes até ficarem
    # ilegíveis. A visão padrão foca na janela densa; o filtro de data
    # continua dando acesso ao histórico completo.
    if not inicio and not fim:
        agrupado = _janela_recente(agrupado)

    # O preço da mesma hora vem junto na resposta. Antes o frontend fazia uma
    # segunda chamada pedindo "as últimas 24h" e cruzava por "HH:MM": ao ver
    # dados de semanas atrás nenhuma hora batia e a série de preço saía vazia.
    precos = svc_correlacao.precos_para_horas(moeda_u, list(agrupado.keys()))

    pontos = [
        {
            "timestamp": para_iso_utc(grupo.timestamp),
            "preco": precos.get(chave, {}).get("preco_fechamento"),
            "variacao_preco": precos.get(chave, {}).get("variacao_pct"),
            "indice_sentimento": grupo.sentimento_medio,
            "sentiment_score": grupo.sentiment_score,
            "total_posts": grupo.total,
            "positivos": grupo.positivos,
            "negativos": grupo.negativos,
            "neutros": grupo.neutros,
        }
        # Ordena cronologicamente para o gráfico não sair embaralhado.
        for chave, grupo in sorted(agrupado.items())
    ]

    return {"moeda": moeda_u, "fonte": fonte_n, "pontos": pontos}


@router.get("/posts-por-hora", tags=["historico"], dependencies=[Depends(usuario_atual)])
def posts_por_hora(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    hora: str = Query(..., description="ISO da hora (ex: 2026-03-03T14:00:00Z)"),
    db: Session = Depends(get_db),
):
    """Todos os posts de uma hora específica, com o resumo da hora."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)
    hora_inicio = parse_hora_obrigatoria(hora)

    posts = posts_da_hora(db, moeda_u, fonte_n, hora_inicio)

    relevantes = [p for p in posts if p.sentimento != svc_sentimento.NULO]
    positivos = sum(1 for p in relevantes if p.sentimento == svc_sentimento.POSITIVO)
    negativos = sum(1 for p in relevantes if p.sentimento == svc_sentimento.NEGATIVO)

    return {
        "moeda": moeda_u,
        "fonte": fonte_n,
        "hora": para_iso_utc(hora_inicio),
        "total": len(posts),
        "positivos": positivos,
        "negativos": negativos,
        "neutros": len(relevantes) - positivos - negativos,
        "nulos": len(posts) - len(relevantes),
        "posts": [
            {
                "id": p.id,
                "texto": p.texto,
                "autor": p.autor,
                "url": p.url,
                "sentimento": p.sentimento,
                "score": round(p.score, 4) if p.score is not None else None,
                "timestamp_post": para_iso_utc(p.timestamp_post),
            }
            for p in posts
        ],
    }


def _janela_recente(agrupado: dict, dias: int = DIAS_JANELA_PADRAO) -> dict:
    """Mantém apenas as horas dentro de ``dias`` a partir do post mais recente.

    A referência é o dado mais novo do banco, não a data de hoje: assim uma
    base coletada semanas atrás continua aparecendo por inteiro.
    """
    if not agrupado:
        return agrupado

    from datetime import timedelta

    mais_recente = max(g.timestamp for g in agrupado.values())
    corte = mais_recente - timedelta(days=dias)
    return {k: g for k, g in agrupado.items() if g.timestamp >= corte}
