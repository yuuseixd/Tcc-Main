"""
Collector do X (Twitter) — SentCrypto.

Ordem de tentativa (mais confiável primeiro):
  1. twikit com cookies do navegador (robusto, sem API paga)
  2. Syndication endpoint (público, sem auth — frágil mas funciona)
  3. Twitter API v2 (Bearer Token — requer plano Basic+)

Inclui cache TTL para evitar rate limit.
"""

import json
import logging
import os
import re
import time as _time
from datetime import datetime, timezone
from typing import List, Dict
from urllib.parse import unquote

import requests as req
from dotenv import load_dotenv

from collectors.cookie_auth import cookies_validos, COOKIES_PATH

load_dotenv()

logger = logging.getLogger("sentcrypto.x")

TWITTER_API = "https://api.twitter.com/2"
SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Cache simples: {username: (timestamp, [tweets])}
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 300  # 5 minutos

MAPA_NOMES = {
    "BTC": ["BITCOIN"],
    "ETH": ["ETHEREUM", "ETHER"],
    "SOL": ["SOLANA"],
    "DOGE": ["DOGECOIN"],
    "XRP": ["RIPPLE"],
    "ADA": ["CARDANO"],
    "MATIC": ["POLYGON"],
    "DOT": ["POLKADOT"],
    "AVAX": ["AVALANCHE"],
    "LINK": ["CHAINLINK"],
}


# ── Helpers de timestamp ────────────────────────────────────────────────────


def _parse_ts_twitter(ts_str: str) -> datetime:
    """Parse timestamp clássico: 'Thu Feb 26 16:45:47 +0000 2026'."""
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.strptime(ts_str, "%a %b %d %H:%M:%S %z %Y")
    except Exception:
        pass
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _parse_ts_iso(ts_str: str) -> datetime:
    """Parse ISO timestamp."""
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
#  Método 1 — twikit com cookies do navegador (PRIMÁRIO)
# ═══════════════════════════════════════════════════════════════════════════


def _coletar_perfil_twikit(username: str, limite: int = 30) -> List[Dict]:
    """
    Coleta tweets via twikit usando cookies extraídos do navegador.
    Método mais confiável - não precisa de API paga.
    """
    if not cookies_validos():
        raise FileNotFoundError(
            "Cookies do Twitter não encontrados. "
            "Execute o setup de login primeiro."
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
            ts = (
                _parse_ts_twitter(tweet.created_at)
                if tweet.created_at
                else datetime.now(timezone.utc)
            )
            resultados.append(
                {
                    "texto": texto,
                    "timestamp_post": ts.isoformat(),
                    "perfil": f"@{username}",
                    "nome_exibicao": user.name or username,
                    "avatar": getattr(user, "profile_image_url", None),
                    "tweet_id": tweet.id,
                    "likes": getattr(tweet, "favorite_count", 0) or 0,
                    "retweets": getattr(tweet, "retweet_count", 0) or 0,
                    "replies": getattr(tweet, "reply_count", 0) or 0,
                }
            )
        return resultados

    return asyncio.run(_fetch())


# ═══════════════════════════════════════════════════════════════════════════
#  Método 2 — Syndication (público, sem autenticação)
# ═══════════════════════════════════════════════════════════════════════════


def _fetch_syndication_html(url: str) -> str:
    """Busca HTML do syndication usando curl + fallback requests."""
    import subprocess

    try:
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "25", url],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout and len(result.stdout) > 1000:
            return result.stdout.decode("utf-8", errors="replace")
        if not result.stdout or len(result.stdout) < 100:
            raise RuntimeError("curl retornou resposta vazia ou rate limited")
    except FileNotFoundError:
        pass  # curl não instalado

    session = req.Session()
    session.headers.update(
        {
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
            "Connection": "close",
        }
    )
    resp = session.get(url, timeout=25)
    session.close()

    if resp.status_code == 429:
        raise RuntimeError("Rate limit (429)")
    resp.raise_for_status()
    return resp.text


def _coletar_via_syndication(username: str, limite: int = 30) -> List[Dict]:
    """Scrape de tweets via syndication.twitter.com."""
    url = f"{SYNDICATION_URL}/{username}"
    html = _fetch_syndication_html(url)

    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("Não foi possível encontrar dados no HTML da syndication")

    data = json.loads(match.group(1))
    entries = (
        data.get("props", {})
        .get("pageProps", {})
        .get("timeline", {})
        .get("entries", [])
    )

    if not entries:
        return []

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
        ts = _parse_ts_twitter(tweet.get("created_at", ""))

        resultados.append(
            {
                "texto": texto,
                "timestamp_post": ts.isoformat(),
                "perfil": f"@{user.get('screen_name', username)}",
                "nome_exibicao": user.get("name", username),
                "avatar": user.get("profile_image_url_https"),
                "tweet_id": tweet.get("id_str"),
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
    token = os.getenv("TWITTER_BEARER_TOKEN", "")
    if not token:
        return {}
    token = unquote(token)
    return {"Authorization": f"Bearer {token}", "User-Agent": "SentCryptoApp/1.0"}


def _coletar_perfil_api(username: str, limite: int = 30) -> List[Dict]:
    """Coleta tweets via Twitter API v2 (requer plano Basic ou superior)."""
    headers = _bearer_headers()
    if not headers:
        raise RuntimeError("TWITTER_BEARER_TOKEN não configurado")

    user_resp = req.get(
        f"{TWITTER_API}/users/by/username/{username}",
        headers=headers,
        params={"user.fields": "name,profile_image_url"},
        timeout=15,
    )
    if user_resp.status_code in (402, 403):
        raise PermissionError(f"Acesso negado pela API v2 ({user_resp.status_code})")
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
            "max_results": min(max(limite, 5), 100),
            "tweet.fields": "created_at,text,public_metrics",
        },
        timeout=15,
    )
    tw_resp.raise_for_status()

    resultados: List[Dict] = []
    for tw in tw_resp.json().get("data", []):
        metrics = tw.get("public_metrics", {})
        resultados.append(
            {
                "texto": (tw.get("text") or "").strip(),
                "timestamp_post": _parse_ts_iso(tw.get("created_at", "")).isoformat(),
                "perfil": f"@{username}",
                "nome_exibicao": nome,
                "avatar": avatar,
                "tweet_id": tw.get("id"),
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
            }
        )

    return resultados


# ═══════════════════════════════════════════════════════════════════════════
#  Interface pública
# ═══════════════════════════════════════════════════════════════════════════


def _coletar_perfil(username: str, limite: int = 30) -> List[Dict]:
    """Tenta os 3 métodos em ordem de confiabilidade. Usa cache TTL."""

    # Verifica cache
    cache_key = username.lower()
    if cache_key in _CACHE:
        cached_ts, cached_tweets = _CACHE[cache_key]
        if _time.time() - cached_ts < _CACHE_TTL:
            logger.info("[cache] %d tweets de @%s", len(cached_tweets), username)
            return cached_tweets[:limite]

    tweets: List[Dict] = []

    # 1 — twikit com cookies (mais confiável, sem API paga)
    if not tweets:
        try:
            tweets = _coletar_perfil_twikit(username, limite)
            if tweets:
                logger.info("[twikit] %d tweets de @%s", len(tweets), username)
        except Exception as e:
            logger.warning("[twikit] falhou para @%s: %s", username, e)

    # 2 — Syndication (público, sem auth — frágil)
    if not tweets:
        try:
            tweets = _coletar_via_syndication(username, limite)
            if tweets:
                logger.info("[syndication] %d tweets de @%s", len(tweets), username)
        except Exception as e:
            logger.warning("[syndication] falhou para @%s: %s", username, e)

    # 3 — API v2 (requer Bearer Token + plano pago)
    if not tweets:
        try:
            tweets = _coletar_perfil_api(username, limite)
            if tweets:
                logger.info("[api-v2] %d tweets de @%s", len(tweets), username)
        except Exception as e:
            logger.warning("[api-v2] falhou para @%s: %s", username, e)

    # Salva no cache se teve resultado
    if tweets:
        _CACHE[cache_key] = (_time.time(), tweets)

    return tweets


def coletar_feed_x(
    perfis: List[str],
    limite_por_perfil: int = 30,
) -> List[Dict]:
    """
    Coleta feed completo de perfis do X (sem filtro de moeda).
    Retorna todos os tweets encontrados, ordenados por data.
    """
    todos: List[Dict] = []
    erros: List[str] = []

    for perfil in perfis:
        username = perfil.lstrip("@").strip()
        if not username:
            continue
        tweets = _coletar_perfil(username, limite_por_perfil)
        if tweets:
            todos.extend(tweets)
        else:
            erros.append(username)

    if not todos and erros:
        raise RuntimeError(
            f"Não foi possível coletar tweets dos perfis: {', '.join(erros)}. "
            "Verifique se os perfis existem e são públicos."
        )

    todos.sort(key=lambda t: t["timestamp_post"], reverse=True)
    return todos


def coletar_tweets_x(
    perfis: List[str],
    moeda: str = "BTC",
    limite_por_perfil: int = 20,
) -> List[Dict]:
    """
    Coleta tweets de perfis e filtra por menções à moeda.
    Retorna apenas tweets relevantes para a moeda escolhida.
    """
    moeda_u = moeda.upper()
    termos = [moeda_u, f"${moeda_u}"] + MAPA_NOMES.get(moeda_u, [])

    todos = coletar_feed_x(perfis, limite_por_perfil)

    filtrados = []
    for tw in todos:
        texto_upper = tw["texto"].upper()
        if any(t in texto_upper for t in termos):
            ts = tw["timestamp_post"]
            if isinstance(ts, str):
                ts = _parse_ts_iso(ts)
            filtrados.append(
                {
                    "texto": tw["texto"],
                    "timestamp_post": ts,
                    "perfil": tw["perfil"],
                    "tweet_id": tw.get("tweet_id"),
                }
            )

    return filtrados
