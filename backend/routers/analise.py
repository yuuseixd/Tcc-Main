"""Análise avulsa de texto com o BERT."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from routers.deps import exigir_bert, usuario_atual
from schemas import TextoParaAnalise
from services import sentimento as svc_sentimento

router = APIRouter()


@router.post("/analisar-texto", tags=["sentimento"], dependencies=[Depends(usuario_atual)])
async def analisar_texto(body: TextoParaAnalise):
    """Classifica um texto livre com o BERT, sem salvar no banco."""
    exigir_bert()

    try:
        analise = await run_in_threadpool(svc_sentimento.analisar_texto, body.texto)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {
        "texto": body.texto[:200],
        "sentimento": analise["sentimento"],
        "indice": analise["indice"],
        "score_bert": analise["score"],
        "label_bert": analise["label_bert"],
        "crypto_relevante": svc_sentimento.texto_e_crypto_relevante(body.texto),
    }
