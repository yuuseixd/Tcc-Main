"""Coleta de tweets do X (Twitter) — SentCrypto.

Ordem de tentativa (mais confiável primeiro):
  1. twikit com cookies do navegador (robusto, sem API paga)
  2. Syndication endpoint (público, sem auth — frágil mas funciona)
  3. Twitter API v2 (Bearer Token — requer plano Basic+)

Inclui cache com TTL para não estourar o rate limit do X durante a coleta
automática do dashboard.
"""

import json
import logging
import os
import re
import time as _time
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import unquote

import requests as req

from collectors.cookie_auth import COOKIES_PATH, cookies_validos
from config import X_CACHE_TTL
from utils.moedas import texto_menciona_moeda

logger = logging.getLogger("sentcrypto.x")

TWITTER_API = "https://api.twitter.com/2"
SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Cache: {username: (momento_da_coleta, quantidade_pedida, [tweets], metodo)}
_CACHE: Dict[str, tuple] = {}


# ── Helpers de timestamp ────────────────────────────────────────────────────


def _agora_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _para_utc_naive(dt: datetime) -> datetime:
    """Normaliza para UTC sem tzinfo — convenção de todo o projeto."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_ts_twitter(ts_str: str) -> datetime:
    """Parse do formato clássico: 'Thu Feb 26 16:45:47 +0000 2026'."""
    if not ts_str:
        return _agora_utc_naive()
    try:
        return _para_utc_naive(
            datetime.strptime(ts_str, "%a %b %d %H:%M:%S %z %Y")
        )
    except ValueError:
        pass
    return _parse_ts_iso(ts_str)


def _parse_ts_iso(ts_str: str) -> datetime:
    """Parse de timestamp ISO 8601."""
    if not ts_str:
        return _agora_utc_naive()
    try:
        return _para_utc_naive(
            datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        )
    except (ValueError, TypeError):
        logger.debug("Timestamp não reconhecido: %r", ts_str)
        return _agora_utc_naive()


# ═══════════════════════════════════════════════════════════════════════════
#  Método 1 — twikit com cookies do navegador (PRIMÁRIO)
# ═══════════════════════════════════════════════════════════════════════════


def _coletar_perfil_twikit(username: str, limite: int = 30) -> List[Dict]:
    """Coleta tweets via twikit usando os cookies extraídos do navegador."""
    if not cookies_validos():
        raise FileNotFoundError(
            "Cookies do X não encontrados. Configure em /login/x."
        )

    import asyncio

    async def _fetch():
        from twikit import Client

        client = Client("pt-BR")
        client.load_cookies(str(COOKIES_PATH))

        user = await client.get_user_by_screen_name(username)
        if not user:
            return []

        tweets = await user.get_tweets("Tweets", count=limite)
        resultados = []
        for tweet in tweets:
            texto = (tweet.text or "").strip()
            if not texto:
                continue
            resultados.append(
                {
                    "texto": texto,
                    "timestamp_post": _parse_ts_twitter(tweet.created_at),
                    "perfil": f"@{username}",
                    "nome_exibicao": user.name or username,
                    "avatar": getattr(user, "profile_image_url", None),
                    "tweet_id": str(tweet.id) if tweet.id else None,
                    "url": f"https://x.com/{username}/status/{tweet.id}"
                    if tweet.id
                    else None,
                    "likes": getattr(tweet, "favorite_count", 0) or 0,
                    "retweets": getattr(tweet, "retweet_count", 0) or 0,
                    "replies": getattr(tweet, "reply_count", 0) or 0,
                }
            )
        return resultados

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_fetch())

    # Já existe um event loop nesta thread (caso do FastAPI): rodar
    # asyncio.run() aqui levantaria RuntimeError.
    raise RuntimeError(
        "twikit não pode ser chamado de dentro de um event loop ativo."
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Método 2 — Syndication (público, sem autenticação)
# ═══════════════════════════════════════════════════════════════════════════


def _fetch_syndication_html(url: str) -> str:
    """Busca o HTML do syndication usando curl, com fallback para requests."""
    import subprocess

    try:
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "25", url],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout and len(result.stdout) > 1000:
            return result.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("curl indisponível/falhou (%s); usando requests.", e)

    session = req.Session()
    try:
        session.headers.update(
            {
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
                "Connection": "close",
            }
        )
        resp = session.get(url, timeout=25)
        if resp.status_code == 429:
            raise RuntimeError("Rate limit do X (HTTP 429)")
        resp.raise_for_status()
        return resp.text
    finally:
        session.close()


def _coletar_via_syndication(username: str, limite: int = 30) -> List[Dict]:
    """Extrai tweets do HTML público do syndication.twitter.com."""
    html = _fetch_syndication_html(f"{SYNDICATION_URL}/{username}")

    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not match:
        raise RuntimeError("Estrutura do syndication mudou: __NEXT_DATA__ ausente")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON do syndication inválido: {e}") from e

    entries = (
        data.get("props", {})
        .get("pageProps", {})
        .get("timeline", {})
        .get("entries", [])
    )

    resultados: List[Dict] = []
    for entry in entries:
        if entry.get("type") != "tweet":
            continue

        tweet = entry.get("content", {}).get("tweet", {})
        if not tweet:
            continue

        texto = (tweet.get("full_text") or tweet.get("text") or "").strip()
        if not texto:
            continue

        user = tweet.get("user", {})
        screen_name = user.get("screen_name", username)
        tweet_id = tweet.get("id_str")

        resultados.append(
            {
                "texto": texto,
                "timestamp_post": _parse_ts_twitter(tweet.get("created_at", "")),
                "perfil": f"@{screen_name}",
                "nome_exibicao": user.get("name", username),
                "avatar": user.get("profile_image_url_https"),
                "tweet_id": tweet_id,
                "url": f"https://x.com/{screen_name}/status/{tweet_id}"
                if tweet_id
                else None,
                "likes": tweet.get("favorite_count", 0) or 0,
                "retweets": tweet.get("retweet_count", 0) or 0,
                "replies": tweet.get("reply_count", 0) or 0,
            }
        )

        if len(resultados) >= limite:
            break

    return resultados


# ═══════════════════════════════════════════════════════════════════════════
#  Método 3 — Twitter API v2 (Bearer Token — plano Basic+)
# ═══════════════════════════════════════════════════════════════════════════


def _bearer_headers() -> dict:
    token = os.getenv("TWITTER_BEARER_TOKEN", "").strip()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {unquote(token)}",
        "User-Agent": "SentCryptoApp/1.0",
    }


def _coletar_perfil_api(username: str, limite: int = 30) -> List[Dict]:
    """Coleta via API oficial v2 (requer plano pago)."""
    headers = _bearer_headers()
    if not headers:
        raise RuntimeError("TWITTER_BEARER_TOKEN não configurado")

    user_resp = req.get(
        f"{TWITTER_API}/users/by/username/{username}",
        headers=headers,
        params={"user.fields": "name,profile_image_url"},
        timeout=15,
    )
    if user_resp.status_code in (401, 402, 403):
        raise PermissionError(
            f"API v2 recusou o acesso (HTTP {user_resp.status_code}). "
            "O endpoint exige plano Basic ou superior."
        )
    user_resp.raise_for_status()

    user_data = user_resp.json().get("data")
    if not user_data:
        return []

    user_id = user_data["id"]
    nome = user_data.get("name", username)
    avatar = user_data.get("profile_image_url")

    tw_resp = req.get(
        f"{TWITTER_API}/users/{user_id}/tweets",
        headers=headers,
        params={
            # A API v2 exige max_results entre 5 e 100.
            "max_results": min(max(limite, 5), 100),
            "tweet.fields": "created_at,text,public_metrics",
        },
        timeout=15,
    )
    tw_resp.raise_for_status()

    resultados: List[Dict] = []
    for tw in tw_resp.json().get("data", []):
        metrics = tw.get("public_metrics", {})
        tweet_id = tw.get("id")
        resultados.append(
            {
                "texto": (tw.get("text") or "").strip(),
                "timestamp_post": _parse_ts_iso(tw.get("created_at", "")),
                "perfil": f"@{username}",
                "nome_exibicao": nome,
                "avatar": avatar,
                "tweet_id": tweet_id,
                "url": f"https://x.com/{username}/status/{tweet_id}"
                if tweet_id
                else None,
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
            }
        )

    return resultados


# ═══════════════════════════════════════════════════════════════════════════
#  Interface pública
# ═══════════════════════════════════════════════════════════════════════════


# Rótulos legíveis dos métodos de fallback, usados nos avisos ao usuário.
_ROTULO_METODO = {
    "syndication": "modo público (sem login — cobertura de tweets mais limitada)",
    "api-v2": "API oficial v2",
}


def _aviso_fallback(username: str, metodo: str) -> str:
    return (
        f"@{username}: coleta autenticada (twikit) indisponível — usando "
        f"{_ROTULO_METODO.get(metodo, metodo)}."
    )


def _coletar_perfil(
    username: str, limite: int = 30, avisos: List[str] | None = None,
) -> List[Dict]:
    """Tenta os três métodos em ordem de confiabilidade, com cache por TTL.

    ``avisos`` (opcional, mesmo padrão de
    ``collectors/reddit_collector.py::coletar_posts_reddit_json``) recebe um
    aviso sempre que o método mais confiável (twikit, autenticado) falha e a
    coleta cai para um método de cobertura mais limitada — sem isso, um feed
    incompleto ou com grandes lacunas de data parecia bug em vez de fallback.
    """
    if avisos is None:
        avisos = []

    cache_key = username.lower()

    entrada = _CACHE.get(cache_key)
    if entrada:
        momento, qtd_pedida, tweets_cache, metodo_cache = entrada
        # O cache só serve se cobrir a quantidade pedida agora. Antes ele
        # devolvia `tweets[:limite]` mesmo quando guardava menos tweets do que
        # o novo pedido, retornando silenciosamente menos do que o solicitado.
        if _time.time() - momento < X_CACHE_TTL and qtd_pedida >= limite:
            logger.info("[cache] %d tweets de @%s", len(tweets_cache), username)
            if metodo_cache != "twikit":
                avisos.append(_aviso_fallback(username, metodo_cache))
            return tweets_cache[:limite]

    metodos = (
        ("twikit", _coletar_perfil_twikit),
        ("syndication", _coletar_via_syndication),
        ("api-v2", _coletar_perfil_api),
    )

    falhas: List[str] = []
    for nome, metodo in metodos:
        try:
            tweets = metodo(username, limite)
        except Exception as e:
            logger.warning("[%s] falhou para @%s: %s", nome, username, e)
            falhas.append(nome)
            continue

        if tweets:
            logger.info("[%s] %d tweets de @%s", nome, len(tweets), username)
            _CACHE[cache_key] = (_time.time(), limite, tweets, nome)
            if nome != "twikit":
                avisos.append(_aviso_fallback(username, nome))
            return tweets

    avisos.append(
        f"@{username}: nenhum método de coleta funcionou "
        f"({', '.join(falhas) or 'sem métodos disponíveis'})."
    )
    return []


def limpar_cache() -> None:
    """Descarta o cache — útil ao trocar de conta ou forçar recoleta."""
    _CACHE.clear()


def coletar_feed_x(
    perfis: List[str],
    limite_por_perfil: int = 30,
    avisos: List[str] | None = None,
) -> List[Dict]:
    """Coleta a timeline dos perfis, sem filtrar por moeda.

    ``avisos`` (opcional) recebe as mensagens de fallback/falha por perfil —
    ver :func:`_coletar_perfil`.

    Levanta ``RuntimeError`` apenas se nenhum perfil retornar nada — falha
    parcial é registrada em log e não impede o restante da coleta.
    """
    if avisos is None:
        avisos = []

    todos: List[Dict] = []
    falharam: List[str] = []

    for perfil in perfis:
        username = perfil.lstrip("@").strip()
        if not username:
            continue

        tweets = _coletar_perfil(username, limite_por_perfil, avisos)
        if tweets:
            todos.extend(tweets)
        else:
            falharam.append(username)

    if falharam:
        logger.warning("Perfis sem resultado: %s", ", ".join(falharam))

    if not todos and falharam:
        raise RuntimeError(
            f"Não foi possível coletar tweets de: {', '.join(falharam)}. "
            "Verifique se os perfis existem, são públicos e se os cookies do X "
            "estão configurados."
        )

    todos.sort(key=lambda t: t["timestamp_post"], reverse=True)
    return todos


def coletar_tweets_x(
    perfis: List[str],
    moeda: str = "BTC",
    limite_por_perfil: int = 20,
    avisos: List[str] | None = None,
) -> List[Dict]:
    """Coleta tweets dos perfis e mantém apenas os que citam a moeda."""
    todos = coletar_feed_x(perfis, limite_por_perfil, avisos)

    return [tw for tw in todos if texto_menciona_moeda(tw["texto"], moeda)]
