import time
import requests
from datetime import datetime, timezone
from typing import List, Dict

REDDIT_BASE = "https://www.reddit.com"

# Importante: Reddit bloqueia User-Agent genérico
DEFAULT_HEADERS = {
    "User-Agent": "sentimento-cripto-tcc/1.0 (by u/anonymous)"
}

def _to_datetime_utc(epoch_seconds: float) -> datetime:
    return datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc)

def coletar_posts_reddit_json(
    subreddits: List[str],
    moeda: str,
    limite_por_sub: int = 25,
    ordenacao: str = "new",
    sleep_s: float = 0.8,
) -> List[Dict]:
    """
    Coleta posts públicos do Reddit via endpoints JSON:
    https://www.reddit.com/r/<sub>/<sort>.json

    Retorna uma lista de dicts:
      { "texto": str, "timestamp_post": datetime, "origem_url": str, "subreddit": str, "titulo": str }
    """
    moeda_u = moeda.upper()
    resultados: List[Dict] = []

    for sub in subreddits:
        url = f"{REDDIT_BASE}/r/{sub}/{ordenacao}.json"
        params = {
            "limit": min(int(limite_por_sub), 100),
            "raw_json": 1
        }

        resp = requests.get(url, headers=DEFAULT_HEADERS, params=params, timeout=20)

        # Rate limit / bloqueio
        if resp.status_code in (429, 403):
            # tenta uma pausa maior e continua
            time.sleep(max(sleep_s, 2.0))
            continue

        resp.raise_for_status()
        data = resp.json()

        children = (data.get("data") or {}).get("children") or []
        for item in children:
            post = (item.get("data") or {})
            title = (post.get("title") or "").strip()
            selftext = (post.get("selftext") or "").strip()
            created_utc = post.get("created_utc")
            permalink = post.get("permalink") or ""
            full_url = f"{REDDIT_BASE}{permalink}" if permalink else ""

            # Alguns posts são links e selftext vazio; ainda dá pra usar o título.
            texto_composto = f"{title}\n{selftext}".strip()

            # filtro simples por moeda (pode refinar depois)
            t_up = texto_composto.upper()
            if moeda_u not in t_up and f"${moeda_u}" not in t_up:
                # também deixa passar se for BTC e texto tem "BITCOIN", etc. (opcional)
                continue

            if not created_utc:
                ts = datetime.now(timezone.utc)
            else:
                ts = _to_datetime_utc(created_utc)

            resultados.append({
                "texto": texto_composto,
                "timestamp_post": ts,
                "origem_url": full_url,
                "subreddit": sub,
                "titulo": title,
            })

        time.sleep(sleep_s)

    return resultados
