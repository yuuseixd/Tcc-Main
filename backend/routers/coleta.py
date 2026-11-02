"""Coleta do X e do Reddit e feed ao vivo do X."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from collectors.reddit_collector import coletar_posts_reddit_json
from collectors.x_collector import coletar_feed_x, coletar_tweets_x
from db import get_db
from routers.deps import exigir_bert, usuario_atual, validar_moeda
from schemas import ColetaRedditRequest, ColetaXRequest, FeedXRequest
from services import posts as svc_posts
from services import sentimento as svc_sentimento
from utils.tempo import para_iso_utc, parse_data_fim, parse_data_inicio

logger = logging.getLogger("sentcrypto")

router = APIRouter()


@router.post("/coletar/reddit", tags=["coleta"], dependencies=[Depends(usuario_atual)])
async def coletar_reddit(
    body: ColetaRedditRequest, db: Session = Depends(get_db)
):
    """Coleta posts do Reddit, classifica com BERT e salva sem duplicar."""
    exigir_bert()
    moeda_u = validar_moeda(body.moeda)

    # Falhas por subreddit voltam para o usuário: uma coleta vazia por bloqueio
    # do Reddit é indistinguível de "não há posts" se o motivo não aparecer.
    avisos: list[str] = []
    brutos = await run_in_threadpool(
        coletar_posts_reddit_json,
        body.subreddits, moeda_u, body.limite_por_sub, body.ordenacao,
        0.8, avisos,
    )

    resultado = await run_in_threadpool(
        svc_posts.salvar_posts, db, moeda_u, "Reddit", brutos
    )

    resposta = {**resultado.como_dict(), "moeda": moeda_u,
                "subreddits": body.subreddits, "avisos": avisos}
    if avisos and not brutos:
        resposta["mensagem"] = f"Nenhum post coletado. {avisos[0]}"
    return resposta


@router.post("/coletar/x", tags=["coleta"], dependencies=[Depends(usuario_atual)])
async def coletar_x(body: ColetaXRequest, db: Session = Depends(get_db)):
    """Coleta tweets dos perfis, classifica com BERT e salva sem duplicar."""
    exigir_bert()
    moeda_u = validar_moeda(body.moeda)

    avisos: list[str] = []
    try:
        brutos = await run_in_threadpool(
            coletar_tweets_x, body.perfis, moeda_u, body.limite_por_perfil, avisos
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    resultado = await run_in_threadpool(
        svc_posts.salvar_posts, db, moeda_u, "X", brutos
    )
    return {**resultado.como_dict(), "moeda": moeda_u,
            "perfis_consultados": body.perfis, "avisos": avisos}


@router.post("/feed/x", tags=["feed"], dependencies=[Depends(usuario_atual)])
async def feed_x(body: FeedXRequest):
    """Timeline dos perfis com análise de sentimento, sem gravar no banco.

    ``data_inicio``/``data_fim`` filtram o que foi coletado — os coletores
    só trazem os tweets mais recentes (não há busca histórica por data sem
    API paga do X), então um período antigo pode simplesmente não ter
    nenhum tweet dentro dele.
    """
    avisos: list[str] = []
    try:
        tweets = await run_in_threadpool(
            coletar_feed_x, body.perfis, body.limite_por_perfil, avisos
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    inicio = parse_data_inicio(body.data_inicio)
    fim = parse_data_fim(body.data_fim)
    if inicio or fim:
        antes_do_filtro = len(tweets)
        tweets = [
            tw
            for tw in tweets
            if (not inicio or tw["timestamp_post"] >= inicio)
            and (not fim or tw["timestamp_post"] <= fim)
        ]
        if antes_do_filtro and not tweets:
            avisos.append(
                f"{antes_do_filtro} tweet(s) coletado(s), mas nenhum caiu "
                "dentro do período informado — os métodos de fallback do X "
                "costumam trazer um histórico com grandes lacunas de data."
            )

    resultado = await run_in_threadpool(_classificar_feed, tweets)

    return {
        "total": len(resultado),
        "perfis": body.perfis,
        "tweets": resultado,
        "avisos": avisos,
    }


def _classificar_feed(tweets: list[dict]) -> list[dict]:
    """Aplica o BERT em cada tweet do feed, sem interromper por falha isolada."""
    resultado = []

    for tw in tweets:
        texto = tw.get("texto") or ""
        relevante = svc_sentimento.texto_e_crypto_relevante(texto)
        analise = {"sentimento": None, "indice": None, "score": None}

        if svc_sentimento.modelo_carregado() and texto:
            try:
                analise = svc_sentimento.classificar(texto)
            except Exception as e:
                logger.warning("Falha ao classificar tweet do feed: %s", e)
        elif not relevante:
            analise = {"sentimento": svc_sentimento.NULO, "indice": None,
                       "score": None}

        resultado.append(
            {
                "texto": texto,
                "perfil": tw.get("perfil"),
                "nome_exibicao": tw.get("nome_exibicao") or tw.get("perfil"),
                "avatar": tw.get("avatar"),
                "timestamp": para_iso_utc(tw.get("timestamp_post")),
                "tweet_id": tw.get("tweet_id"),
                "url": tw.get("url"),
                "likes": tw.get("likes", 0),
                "retweets": tw.get("retweets", 0),
                "replies": tw.get("replies", 0),
                "sentimento": analise["sentimento"],
                "indice_sentimento": analise["indice"],
                "score_bert": analise["score"],
                "crypto_relevante": relevante,
            }
        )

    return resultado
