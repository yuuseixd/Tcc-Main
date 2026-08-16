"""Coleta de posts públicos do Reddit via endpoints JSON.

Usa ``https://www.reddit.com/r/<sub>/<sort>.json``, que é público e não exige
credenciais. O Reddit bloqueia User-Agent genérico, então um identificador
próprio é obrigatório.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List

import requests

from utils.moedas import texto_menciona_moeda

logger = logging.getLogger("sentcrypto.reddit")

REDDIT_BASE = "https://www.reddit.com"

DEFAULT_HEADERS = {
    "User-Agent": "sentcrypto-tcc/1.0 (pesquisa academica)"
}

# Ordenações aceitas pelo endpoint JSON do Reddit.
ORDENACOES_VALIDAS = {"new", "hot", "top", "rising", "controversial"}


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

    for sub in subreddits:
        sub = sub.strip().lstrip("r/")
        if not sub:
            continue

        url = f"{REDDIT_BASE}/r/{sub}/{ordenacao}.json"
        params = {"limit": min(max(int(limite_por_sub), 1), 100), "raw_json": 1}

        try:
            resp = requests.get(
                url, headers=DEFAULT_HEADERS, params=params, timeout=20
            )
        except requests.RequestException as e:
            msg = f"r/{sub}: falha de rede ({e.__class__.__name__})."
            logger.warning(msg)
            avisos.append(msg)
            continue

        if resp.status_code in (429, 403):
            msg = (
                f"r/{sub}: o Reddit bloqueou a requisição (HTTP "
                f"{resp.status_code}). O acesso anônimo é limitado por IP; "
                "tente novamente mais tarde ou use credenciais de API."
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
