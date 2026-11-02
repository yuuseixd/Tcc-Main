"""Saúde da API, status das integrações e cookies do X."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from collectors.cookie_auth import COOKIES_PATH, cookies_validos
from collectors.x_collector import status_ultima_coleta
from config import MOEDAS_SUPORTADAS, reddit_autenticado
from routers.deps import exigir_admin
from schemas import LoginXRequest
from services import sentimento as svc_sentimento

logger = logging.getLogger("sentcrypto")

router = APIRouter()


@router.get("/", tags=["health"])
def health_check(request: Request):
    """Estado geral da API."""
    return {
        "status": "ok",
        "versao": request.app.version,
        "bert_carregado": svc_sentimento.modelo_carregado(),
        "twitter_cookies": cookies_validos(),
        "twitter_coleta": status_ultima_coleta(),
        "reddit_oauth": reddit_autenticado(),
        "moedas_suportadas": MOEDAS_SUPORTADAS,
    }


@router.get("/status/reddit", tags=["health"])
def reddit_status():
    """Diz se a coleta do Reddit usará OAuth ou o modo anônimo."""
    autenticado = reddit_autenticado()
    return {
        "autenticado": autenticado,
        "modo": "oauth" if autenticado else "anonimo",
        "mensagem": (
            "Credenciais configuradas. A coleta usará OAuth."
            if autenticado
            else "Sem credenciais: a coleta usará o modo anônimo, que o Reddit "
                 "limita por IP e costuma recusar com HTTP 403. Configure "
                 "REDDIT_CLIENT_ID e REDDIT_CLIENT_SECRET no .env."
        ),
    }


@router.get("/status/twitter", tags=["health"])
def twitter_status():
    """Diz se os cookies do X estão configurados."""
    valido = cookies_validos()
    return {
        "cookies_validos": valido,
        "ultima_coleta": status_ultima_coleta(),
        "mensagem": (
            "Cookies do X válidos. Pronto para coletar."
            if valido
            else "Cookies do X não encontrados. Configure via /login/x."
        ),
    }


@router.post("/login/x", tags=["auth"], dependencies=[Depends(exigir_admin)])
def login_x(body: LoginXRequest):
    """Salva os cookies de sessão do X usados pelo coletor."""
    cookies = {"auth_token": body.auth_token.strip(), "ct0": body.ct0.strip()}

    try:
        COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)
        # O arquivo guarda uma sessão ativa: restringe a leitura ao dono.
        try:
            COOKIES_PATH.chmod(0o600)
        except OSError:
            pass  # Windows pode não suportar; não é motivo para falhar.
    except OSError as e:
        raise HTTPException(
            status_code=500, detail=f"Não foi possível salvar os cookies: {e}"
        ) from e

    logger.info("Cookies do X atualizados.")
    return {"sucesso": True, "mensagem": "Cookies do X salvos com sucesso!"}
