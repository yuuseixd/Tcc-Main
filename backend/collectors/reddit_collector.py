"""Coleta de posts do Reddit.

Dois modos, escolhidos automaticamente:

**OAuth (preferido).** Com ``REDDIT_CLIENT_ID`` e ``REDDIT_CLIENT_SECRET``
no ``.env``, autentica em ``oauth.reddit.com``. É o modo recomendado: o
limite é de ~100 requisições por minuto por aplicação, contra um limite por
IP no modo anônimo que na prática costuma responder HTTP 403.

**Anônimo (fallback).** Sem credenciais, usa os endpoints JSON públicos em
``www.reddit.com``. Funciona para testes rápidos, mas é instável para coleta
prolongada.

Para criar as credenciais: https://www.reddit.com/prefs/apps → "create app"
→ tipo **script** → o ``client_id`` fica sob o nome do app e o
``client_secret`` no campo "secret".
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List

import requests

from config import (
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    reddit_autenticado,
)
from utils.moedas import texto_menciona_moeda

logger = logging.getLogger("sentcrypto.reddit")

REDDIT_BASE = "https://www.reddit.com"
REDDIT_OAUTH_BASE = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

# Ordenações aceitas pelo endpoint JSON do Reddit.
ORDENACOES_VALIDAS = {"new", "hot", "top", "rising", "controversial"}

# Token OAuth em cache: (token, momento_de_expiracao).
_token_cache: tuple[str, float] | None = None

# Renova um pouco antes do vencimento real, para nenhuma requisição sair com
# um token que expira no caminho.
_MARGEM_RENOVACAO = 60


def _obter_token() -> str | None:
    """Devolve um token OAuth válido, renovando quando necessário.

    Usa o fluxo ``client_credentials``, que dá acesso somente-leitura ao
    conteúdo público — exatamente o que a coleta precisa, sem pedir acesso à
    conta de ninguém.
    """
    global _token_cache

    if not reddit_autenticado():
        return None

    if _token_cache:
        token, expira_em = _token_cache
        if time.time() < expira_em - _MARGEM_RENOVACAO:
            return token

    try:
        resp = requests.post(
            TOKEN_URL,
            auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": REDDIT_USER_AGENT},
            timeout=20,
        )
    except requests.RequestException as e:
        logger.warning("Falha de rede ao autenticar no Reddit: %s", e)
        return None

    if resp.status_code == 401:
        logger.error(
            "Reddit recusou as credenciais (401). Confira REDDIT_CLIENT_ID e "
            "REDDIT_CLIENT_SECRET no .env — o app precisa ser do tipo 'script'."
        )
        return None

    try:
        resp.raise_for_status()
        dados = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Resposta inválida ao autenticar no Reddit: %s", e)
        return None

    token = dados.get("access_token")
    if not token:
        logger.warning("Reddit não devolveu access_token: %s", dados)
        return None

    _token_cache = (token, time.time() + int(dados.get("expires_in", 3600)))
    logger.info("Autenticado no Reddit via OAuth.")
    return token


def _base_e_headers() -> tuple[str, dict]:
    """Escolhe o endpoint e os cabeçalhos conforme haja ou não credenciais."""
    token = _obter_token()
    if token:
        return REDDIT_OAUTH_BASE, {
            "User-Agent": REDDIT_USER_AGENT,
            "Authorization": f"Bearer {token}",
        }
    return REDDIT_BASE, {"User-Agent": REDDIT_USER_AGENT}


def limpar_token() -> None:
    """Descarta o token em cache — útil ao trocar de credenciais."""
    global _token_cache
    _token_cache = None


def _to_datetime_utc(epoch_seconds: float) -> datetime:
    """Converte epoch do Reddit para UTC naive (convenção do banco)."""
    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).replace(
        tzinfo=None
    )


def coletar_posts_reddit_json(
    subreddits: List[str],
    moeda: str,
    limite_por_sub: int = 25,
    ordenacao: str = "new",
    sleep_s: float = 0.8,
    avisos: List[str] | None = None,
) -> List[Dict]:
    """Coleta posts dos subreddits e filtra os que mencionam a moeda.

    Cada item devolvido traz ``external_id``, usado para deduplicar na hora de
    salvar — sem isso, coletar duas vezes inflava a contagem de posts e
    distorcia o índice de sentimento.

    ``avisos`` (opcional) recebe as falhas por subreddit, para que a API possa
    explicar ao usuário por que a coleta voltou vazia em vez de só dizer
    "0 posts".
    """
    if ordenacao not in ORDENACOES_VALIDAS:
        logger.warning("Ordenação '%s' inválida, usando 'new'.", ordenacao)
        ordenacao = "new"

    if avisos is None:
        avisos = []

    resultados: List[Dict] = []
    vistos: set[str] = set()

    base, headers = _base_e_headers()
    autenticado = base == REDDIT_OAUTH_BASE
    logger.info(
        "Coleta do Reddit em modo %s.",
        "OAuth" if autenticado else "anônimo (sujeito a bloqueio por IP)",
    )

    for sub in subreddits:
        sub = sub.strip().lstrip("r/")
        if not sub:
            continue

        # O endpoint OAuth não usa o sufixo .json — o formato já é JSON.
        caminho = f"/r/{sub}/{ordenacao}"
        url = f"{base}{caminho}" if autenticado else f"{base}{caminho}.json"
        params = {"limit": min(max(int(limite_por_sub), 1), 100), "raw_json": 1}

        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
        except requests.RequestException as e:
            msg = f"r/{sub}: falha de rede ({e.__class__.__name__})."
            logger.warning(msg)
            avisos.append(msg)
            continue

        if resp.status_code == 401 and autenticado:
            # Token pode ter sido revogado no meio da coleta; força renovação
            # para que a próxima rodada não herde um token morto.
            limpar_token()
            msg = f"r/{sub}: token do Reddit expirou ou foi revogado (401)."
            logger.warning(msg)
            avisos.append(msg)
            continue

        if resp.status_code in (429, 403):
            if autenticado:
                msg = (
                    f"r/{sub}: Reddit recusou (HTTP {resp.status_code}) mesmo "
                    "autenticado. Pode ser limite de taxa (reduza a frequência) "
                    "ou um subreddit privado/banido."
                )
            else:
                msg = (
                    f"r/{sub}: o Reddit bloqueou a requisição (HTTP "
                    f"{resp.status_code}). O acesso anônimo é limitado por IP. "
                    "Configure REDDIT_CLIENT_ID e REDDIT_CLIENT_SECRET no .env "
                    "para usar OAuth."
                )
            logger.warning(msg)
            avisos.append(msg)
            time.sleep(max(sleep_s, 2.0))
            continue

        if resp.status_code == 404:
            msg = f"r/{sub}: subreddit não encontrado."
            logger.warning(msg)
            avisos.append(msg)
            continue

        try:
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            msg = f"r/{sub}: resposta inválida ({e})."
            logger.warning(msg)
            avisos.append(msg)
            continue

        children = (data.get("data") or {}).get("children") or []
        encontrados = 0

        for item in children:
            post = item.get("data") or {}

            titulo = (post.get("title") or "").strip()
            selftext = (post.get("selftext") or "").strip()
            texto = f"{titulo}\n{selftext}".strip()
            if not texto:
                continue

            if not texto_menciona_moeda(texto, moeda):
                continue

            post_id = post.get("id")
            # ``name`` é o fullname (ex.: t3_abc123) e é globalmente único.
            external_id = post.get("name") or (f"t3_{post_id}" if post_id else None)
            if external_id and external_id in vistos:
                continue
            if external_id:
                vistos.add(external_id)

            permalink = post.get("permalink") or ""
            created_utc = post.get("created_utc")

            resultados.append(
                {
                    "texto": texto,
                    "timestamp_post": (
                        _to_datetime_utc(created_utc)
                        if created_utc
                        else datetime.now(timezone.utc).replace(tzinfo=None)
                    ),
                    "external_id": external_id,
                    "url": f"{REDDIT_BASE}{permalink}" if permalink else None,
                    "autor": f"r/{sub}",
                    "titulo": titulo,
                }
            )
            encontrados += 1

        logger.info(
            "r/%s: %d posts relevantes para %s (de %d recebidos)",
            sub, encontrados, moeda.upper(), len(children),
        )
        time.sleep(sleep_s)

    return resultados
