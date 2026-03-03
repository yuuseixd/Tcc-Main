import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fpdf import FPDF
from pydantic import BaseModel
from sqlalchemy.orm import Session
from transformers import pipeline

from collectors.reddit_collector import coletar_posts_reddit_json
from collectors.x_collector import coletar_tweets_x, coletar_feed_x
from collectors.cookie_auth import cookies_validos, extrair_cookies_do_navegador
from db import Base, engine, SessionLocal
from models import MarketPoint, SocialPost

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentcrypto")

Base.metadata.create_all(bind=engine)

# ── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SentCrypto API",
    description="API de análise de sentimento do mercado de criptomoedas",
    version="1.0.0",
)

# ── BERT (IA) ───────────────────────────────────────────────────────────────
BERT_MODEL_NAME = "nlptown/bert-base-multilingual-uncased-sentiment"
sentiment_pipeline = None

try:
    logger.info("Carregando modelo BERT de sentimento...")
    sentiment_pipeline = pipeline(
        "sentiment-analysis",
        model=BERT_MODEL_NAME,
        tokenizer=BERT_MODEL_NAME,
    )
    logger.info("BERT carregado com sucesso.")
except Exception as e:
    logger.error("Erro ao carregar BERT: %s", e)

# ── CORS ────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Servir pasta de relatórios como arquivos estáticos ──────────────────────
_relatorios_dir = Path(__file__).parent / "relatorios"
_relatorios_dir.mkdir(exist_ok=True)
app.mount("/relatorios", StaticFiles(directory=str(_relatorios_dir)), name="relatorios")


# ── Dependência de banco ───────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Binance helpers ─────────────────────────────────────────────────────────
BINANCE_API_URL = "https://api.binance.com/api/v3/klines"


def fetch_binance_klines(symbol: str, interval: str = "1h", limit: int = 24,
                         start_time: int = None, end_time: int = None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    try:
        resp = requests.get(BINANCE_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Erro ao buscar dados na Binance: {e}"
        )


# ── Sentimento helpers ──────────────────────────────────────────────────────
def mapear_estrela_para_sentimento(label: str) -> str:
    """Converte label do modelo (ex: '1 star') em negativo/neutro/positivo."""
    if not label:
        return "neutro"
    if "1" in label or "2" in label:
        return "negativo"
    if "3" in label:
        return "neutro"
    return "positivo"


# ── Filtro de relevância crypto ─────────────────────────────────────────────
CRYPTO_KEYWORDS = {
    # Moedas
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "dogecoin", "doge",
    "xrp", "ripple", "cardano", "ada", "avalanche", "avax", "chainlink", "link",
    "bnb", "binance", "polygon", "matic", "polkadot", "dot", "litecoin", "ltc",
    "shiba", "shib", "tron", "trx", "tether", "usdt", "usdc",
    # Termos gerais
    "crypto", "cryptocurrency", "criptomoeda", "criptomoedas", "cripto",
    "blockchain", "defi", "nft", "web3", "altcoin", "altcoins",
    "token", "tokens", "staking", "mining", "minerar", "mineração",
    "wallet", "carteira", "exchange", "corretora",
    "bull", "bear", "bullish", "bearish", "pump", "dump",
    "hodl", "hold", "moon", "mooning", "whale", "whales",
    "market cap", "marketcap", "trading", "trade", "trader",
    "satoshi", "nakamoto", "halving", "hash", "hashrate",
    "dex", "cex", "yield", "airdrop", "ico", "ido",
    "smart contract", "contrato inteligente", "layer 2", "l2",
    "metaverse", "metaverso", "dao", "gas fee", "gas",
}


def texto_e_crypto_relevante(texto: str) -> bool:
    """Verifica se o texto contém palavras-chave relacionadas a crypto."""
    if not texto:
        return False
    texto_lower = texto.lower()
    return any(kw in texto_lower for kw in CRYPTO_KEYWORDS)


def sentimento_para_indice(sentimento: str) -> float:
    """Converte sentimento em índice numérico (0-1)."""
    if sentimento == "negativo":
        return 0.2
    if sentimento == "positivo":
        return 0.8
    return 0.5


def analisar_e_salvar_post(
    db: Session,
    moeda: str,
    fonte: str,
    texto: str,
    timestamp_post: datetime,
) -> SocialPost:
    """Analisa o texto com BERT e salva no SQLite.

    Se o texto não for relacionado a crypto, salva com sentimento=nulo
    para não influenciar o gráfico nem positiva nem negativamente.
    """
    if sentiment_pipeline is None:
        raise HTTPException(status_code=500, detail="Modelo BERT não carregado.")

    texto = (texto or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Texto vazio.")

    # Filtro de relevância: se não é sobre crypto, marca como nulo
    if not texto_e_crypto_relevante(texto):
        post = SocialPost(
            moeda=moeda.upper(),
            fonte=fonte,
            texto=texto,
            sentimento="nulo",
            score=0.0,
            timestamp_post=timestamp_post,
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        logger.info("Post ignorado (não é crypto): %s...", texto[:60])
        return post

    result = sentiment_pipeline(texto, truncation=True, max_length=512)[0]
    label = result["label"]
    score = float(result["score"])
    sentimento = mapear_estrela_para_sentimento(label)

    post = SocialPost(
        moeda=moeda.upper(),
        fonte=fonte,
        texto=texto,
        sentimento=sentimento,
        score=score,
        timestamp_post=timestamp_post,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


# ── Schemas (Pydantic) ──────────────────────────────────────────────────────
class ColetaRedditRequest(BaseModel):
    moeda: str = "BTC"
    subreddits: List[str] = ["CryptoCurrency", "Bitcoin", "ethtrader"]
    limite_por_sub: int = 25
    ordenacao: str = "new"


class ColetaXRequest(BaseModel):
    moeda: str = "BTC"
    perfis: List[str] = ["whale_alert", "cabortopcripto"]
    limite_por_perfil: int = 20


class FeedXRequest(BaseModel):
    perfis: List[str] = ["whale_alert", "cabortopcripto"]
    limite_por_perfil: int = 30


class TextoParaAnalise(BaseModel):
    texto: str
    moeda: str = "BTC"


class LoginXRequest(BaseModel):
    metodo: str = "auto"  # auto | manual
    auth_token: str = ""
    ct0: str = ""


# ═══════════════════════════════════════════════════════════════════════════
#  ROTAS
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/", tags=["health"])
def health_check():
    """Verifica se a API está no ar e se o BERT está carregado."""
    return {
        "status": "ok",
        "bert_carregado": sentiment_pipeline is not None,
        "twitter_cookies": cookies_validos(),
    }


@app.get("/status/twitter", tags=["health"])
def twitter_status():
    """Verifica se os cookies do Twitter estão configurados."""
    return {
        "cookies_validos": cookies_validos(),
        "mensagem": (
            "Cookies do Twitter válidos. Pronto para coletar."
            if cookies_validos()
            else "Cookies do Twitter não encontrados. Configure via /login/x."
        ),
    }


@app.post("/login/x", tags=["auth"])
def login_twitter_manual(body: LoginXRequest):
    """Salva cookies do Twitter manualmente (auth_token e ct0)."""
    if not body.auth_token or not body.ct0:
        raise HTTPException(
            status_code=400,
            detail="auth_token e ct0 são obrigatórios.",
        )

    import json
    from collectors.cookie_auth import COOKIES_PATH

    cookies_dict = {"auth_token": body.auth_token, "ct0": body.ct0}
    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies_dict, f, indent=2, ensure_ascii=False)

    return {"sucesso": True, "mensagem": "Cookies do Twitter salvos com sucesso!"}


# ── Sentimento atual (candle) ───────────────────────────────────────────────
@app.get("/sentimento", tags=["sentimento"])
def sentimento_atual(moeda: str = Query("BTC")):
    """Retorna o sentimento derivado do último candle da Binance."""
    symbol = f"{moeda.upper()}USDT"
    klines = fetch_binance_klines(symbol, interval="1h", limit=2)

    if not klines:
        raise HTTPException(status_code=404, detail="Nenhum dado encontrado.")

    ultimo = klines[-1]
    abertura = float(ultimo[1])
    fechamento = float(ultimo[4])
    variacao = (fechamento - abertura) / abertura if abertura else 0

    if variacao > 0.005:
        sentimento = "positivo"
    elif variacao < -0.005:
        sentimento = "negativo"
    else:
        sentimento = "neutro"

    ts = datetime.fromtimestamp(ultimo[0] / 1000, tz=timezone.utc)

    return {
        "moeda": moeda.upper(),
        "sentimento_atual": sentimento,
        "indice_sentimento": round(sentimento_para_indice(sentimento), 2),
        "preco": round(fechamento, 2),
        "variacao_percentual": round(variacao * 100, 4),
        "ultimo_update": datetime.now(timezone.utc).isoformat(),
    }


# ── Histórico ao vivo (Binance) ────────────────────────────────────────────
@app.get("/historico-sentimento", tags=["historico"])
def historico_sentimento(
    moeda: str = Query("BTC"),
    limite: int = Query(24, ge=1, le=1000),
    data_inicio: str = Query(None, description="Data início ISO (ex: 2026-03-01)"),
    data_fim: str = Query(None, description="Data fim ISO (ex: 2026-03-03)"),
):
    """Retorna histórico de preço + sentimento via Binance (ao vivo)."""
    symbol = f"{moeda.upper()}USDT"

    start_ms = None
    end_ms = None
    if data_inicio:
        try:
            dt = datetime.fromisoformat(data_inicio)
            start_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            pass
    if data_fim:
        try:
            dt = datetime.fromisoformat(data_fim)
            # fim do dia
            dt = dt.replace(hour=23, minute=59, second=59)
            end_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            pass

    klines = fetch_binance_klines(
        symbol, interval="1h", limit=limite,
        start_time=start_ms, end_time=end_ms,
    )

    pontos = []
    for k in klines:
        abertura = float(k[1])
        fechamento = float(k[4])
        variacao = (fechamento - abertura) / abertura if abertura else 0

        if variacao > 0.005:
            sent = "positivo"
        elif variacao < -0.005:
            sent = "negativo"
        else:
            sent = "neutro"

        ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
        pontos.append(
            {
                "timestamp": ts.isoformat(),
                "preco": round(fechamento, 2),
                "indice_sentimento": sentimento_para_indice(sent),
            }
        )

    return {"moeda": moeda.upper(), "pontos": pontos}


# ── Histórico salvo no banco (market_points) ───────────────────────────────
@app.get("/historico-db", tags=["historico"])
def historico_db(
    moeda: str = Query("BTC"),
    limite: int = Query(500, ge=1, le=2000),
    data_inicio: str = Query(None, description="Data início ISO (ex: 2026-03-01)"),
    data_fim: str = Query(None, description="Data fim ISO (ex: 2026-03-03)"),
    db: Session = Depends(get_db),
):
    """Retorna histórico salvo na tabela market_points com filtro de data.
    Se não houver dados locais, busca da Binance e salva no banco."""

    query = db.query(MarketPoint).filter(MarketPoint.moeda == moeda.upper())

    if data_inicio:
        try:
            dt_ini = datetime.fromisoformat(data_inicio).replace(tzinfo=timezone.utc)
            query = query.filter(MarketPoint.timestamp >= dt_ini)
        except ValueError:
            pass
    if data_fim:
        try:
            dt_end = datetime.fromisoformat(data_fim).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            query = query.filter(MarketPoint.timestamp <= dt_end)
        except ValueError:
            pass

    registros = (
        query.order_by(MarketPoint.timestamp.desc())
        .limit(limite)
        .all()
    )
    registros.reverse()

    # Se não houver dados no banco, tentar buscar da Binance e popular
    if not registros:
        try:
            symbol = f"{moeda.upper()}USDT"
            start_ms = None
            end_ms = None
            kline_limit = 24
            if data_inicio:
                try:
                    dt = datetime.fromisoformat(data_inicio)
                    start_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
                    kline_limit = 500
                except ValueError:
                    pass
            if data_fim:
                try:
                    dt = datetime.fromisoformat(data_fim).replace(hour=23, minute=59, second=59)
                    end_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
                except ValueError:
                    pass

            klines = fetch_binance_klines(
                symbol, interval="1h", limit=kline_limit,
                start_time=start_ms, end_time=end_ms,
            )

            novos = []
            for k in klines:
                abertura = float(k[1])
                fechamento = float(k[4])
                variacao = (fechamento - abertura) / abertura if abertura else 0
                if variacao > 0.005:
                    sent = "positivo"
                elif variacao < -0.005:
                    sent = "negativo"
                else:
                    sent = "neutro"

                ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
                mp = MarketPoint(
                    moeda=moeda.upper(),
                    timestamp=ts,
                    preco=round(fechamento, 2),
                    indice_sentimento=sentimento_para_indice(sent),
                )
                db.add(mp)
                novos.append(mp)

            db.commit()
            registros = novos
            logger.info("Sincronizou %d pontos da Binance para %s", len(novos), moeda.upper())
        except Exception as e:
            logger.warning("Falha ao sincronizar da Binance: %s", e)

    pontos = [
        {
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "preco": round(r.preco, 2) if r.preco else None,
            "indice_sentimento": (
                round(r.indice_sentimento, 2) if r.indice_sentimento else None
            ),
        }
        for r in registros
    ]
    return {"moeda": moeda.upper(), "pontos": pontos}


# ── Sincronizar histórico Binance → banco ────────────────────────────────
@app.post("/sync-binance", tags=["historico"])
def sync_binance(
    moeda: str = Query("BTC"),
    dias: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Busca dados da Binance dos últimos N dias e salva no banco."""
    symbol = f"{moeda.upper()}USDT"
    agora = datetime.now(timezone.utc)
    start_ms = int((agora.timestamp() - dias * 86400) * 1000)

    klines = fetch_binance_klines(
        symbol, interval="1h", limit=dias * 24,
        start_time=start_ms,
    )

    salvos = 0
    for k in klines:
        abertura = float(k[1])
        fechamento = float(k[4])
        variacao = (fechamento - abertura) / abertura if abertura else 0
        if variacao > 0.005:
            sent = "positivo"
        elif variacao < -0.005:
            sent = "negativo"
        else:
            sent = "neutro"

        ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)

        existente = (
            db.query(MarketPoint)
            .filter(
                MarketPoint.moeda == moeda.upper(),
                MarketPoint.timestamp == ts,
            )
            .first()
        )
        if existente:
            existente.preco = round(fechamento, 2)
            existente.indice_sentimento = sentimento_para_indice(sent)
        else:
            mp = MarketPoint(
                moeda=moeda.upper(),
                timestamp=ts,
                preco=round(fechamento, 2),
                indice_sentimento=sentimento_para_indice(sent),
            )
            db.add(mp)
            salvos += 1

    db.commit()
    return {
        "mensagem": f"Sincronização concluída: {salvos} novos pontos salvos.",
        "total_klines": len(klines),
        "novos": salvos,
        "moeda": moeda.upper(),
    }


# ── Histórico social (social_posts) ────────────────────────────────────────
@app.get("/historico-social", tags=["historico"])
def historico_social(
    moeda: str = Query("BTC"),
    fonte: str = Query("Reddit"),
    limite: int = Query(500, ge=1, le=2000),
    data_inicio: str = Query(None, description="Data início ISO (ex: 2026-03-01)"),
    data_fim: str = Query(None, description="Data fim ISO (ex: 2026-03-03)"),
    db: Session = Depends(get_db),
):
    """Retorna histórico de sentimento dos posts sociais agrupado por hora."""
    query = (
        db.query(SocialPost)
        .filter(
            SocialPost.moeda == moeda.upper(),
            SocialPost.fonte == fonte,
        )
    )

    if data_inicio:
        try:
            dt_ini = datetime.fromisoformat(data_inicio).replace(tzinfo=timezone.utc)
            query = query.filter(SocialPost.timestamp_post >= dt_ini)
        except ValueError:
            pass
    if data_fim:
        try:
            dt_end = datetime.fromisoformat(data_fim).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            query = query.filter(SocialPost.timestamp_post <= dt_end)
        except ValueError:
            pass

    posts = (
        query.order_by(SocialPost.timestamp_post.desc())
        .limit(limite)
        .all()
    )
    posts.reverse()

    agrupado: dict = {}
    for p in posts:
        # Ignora posts marcados como "nulo" (não relacionados a crypto)
        if p.sentimento == "nulo":
            continue
        hora = p.timestamp_post.replace(minute=0, second=0, microsecond=0)
        chave = hora.isoformat()
        if chave not in agrupado:
            agrupado[chave] = {"indices": [], "timestamp": chave, "total": 0, "positivos": 0, "negativos": 0, "neutros": 0}
        agrupado[chave]["indices"].append(sentimento_para_indice(p.sentimento))
        agrupado[chave]["total"] += 1
        if p.sentimento == "positivo":
            agrupado[chave]["positivos"] += 1
        elif p.sentimento == "negativo":
            agrupado[chave]["negativos"] += 1
        else:
            agrupado[chave]["neutros"] += 1

    pontos = []
    for grupo in agrupado.values():
        media = sum(grupo["indices"]) / len(grupo["indices"])
        pontos.append(
            {
                "timestamp": grupo["timestamp"],
                "preco": None,
                "indice_sentimento": round(media, 2),
                "total_posts": grupo["total"],
                "positivos": grupo["positivos"],
                "negativos": grupo["negativos"],
                "neutros": grupo["neutros"],
            }
        )

    return {"moeda": moeda.upper(), "fonte": fonte, "pontos": pontos}


# ── Posts por hora (para relatório PDF) ─────────────────────────────────────
@app.get("/posts-por-hora", tags=["historico"])
def posts_por_hora(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    hora: str = Query(..., description="ISO timestamp da hora (ex: 2026-03-03T14:00:00)"),
    db: Session = Depends(get_db),
):
    """Retorna todos os posts de uma hora específica para gerar relatório."""
    try:
        hora_dt = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de hora inválido. Use ISO 8601.")

    hora_inicio = hora_dt.replace(minute=0, second=0, microsecond=0)
    hora_fim = hora_inicio.replace(minute=59, second=59, microsecond=999999)

    posts = (
        db.query(SocialPost)
        .filter(
            SocialPost.moeda == moeda.upper(),
            SocialPost.fonte == fonte,
            SocialPost.timestamp_post >= hora_inicio,
            SocialPost.timestamp_post <= hora_fim,
        )
        .order_by(SocialPost.timestamp_post.asc())
        .all()
    )

    resultado = []
    for p in posts:
        resultado.append({
            "id": p.id,
            "texto": p.texto,
            "sentimento": p.sentimento,
            "score": round(p.score, 4) if p.score else None,
            "timestamp_post": p.timestamp_post.isoformat() if p.timestamp_post else None,
        })

    # Estatísticas resumidas
    total = len(resultado)
    crypto_posts = [p for p in resultado if p["sentimento"] != "nulo"]
    positivos = sum(1 for p in crypto_posts if p["sentimento"] == "positivo")
    negativos = sum(1 for p in crypto_posts if p["sentimento"] == "negativo")
    neutros = sum(1 for p in crypto_posts if p["sentimento"] == "neutro")
    nulos = total - len(crypto_posts)

    return {
        "moeda": moeda.upper(),
        "fonte": fonte,
        "hora": hora_inicio.isoformat(),
        "total": total,
        "positivos": positivos,
        "negativos": negativos,
        "neutros": neutros,
        "nulos": nulos,
        "posts": resultado,
    }


# ── Gerar relatório PDF (salva em backend/relatorios/) ─────────────────────
RELATORIOS_DIR = Path(__file__).parent / "relatorios"
RELATORIOS_DIR.mkdir(exist_ok=True)


class SentCryptoPDF(FPDF):
    """PDF customizado com header/footer do SentCrypto."""

    def header(self):
        self.set_fill_color(15, 23, 42)
        self.rect(0, 0, self.w, 32, "F")
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 12, "SentCrypto - Relatório de Sentimento", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        if hasattr(self, "_subtitulo"):
            self.cell(0, 8, self._subtitulo, new_x="LMARGIN", new_y="NEXT")
        self.ln(14)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(148, 163, 184)
        self.cell(
            0, 10,
            f"Gerado por SentCrypto · {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}   |   Pág. {self.page_no()}/{{nb}}",
            align="C",
        )


@app.post("/gerar-relatorio", tags=["relatorio"])
def gerar_relatorio(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    hora: str = Query(..., description="ISO timestamp da hora"),
    indice: float = Query(None, description="Índice de sentimento médio"),
    db: Session = Depends(get_db),
):
    """Gera um PDF com os posts da hora selecionada e salva em backend/relatorios/."""
    try:
        hora_dt = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de hora inválido.")

    hora_inicio = hora_dt.replace(minute=0, second=0, microsecond=0)
    hora_fim = hora_inicio.replace(minute=59, second=59, microsecond=999999)

    posts = (
        db.query(SocialPost)
        .filter(
            SocialPost.moeda == moeda.upper(),
            SocialPost.fonte == fonte,
            SocialPost.timestamp_post >= hora_inicio,
            SocialPost.timestamp_post <= hora_fim,
        )
        .order_by(SocialPost.timestamp_post.asc())
        .all()
    )

    total = len(posts)
    crypto_posts = [p for p in posts if p.sentimento != "nulo"]
    positivos = sum(1 for p in crypto_posts if p.sentimento == "positivo")
    negativos = sum(1 for p in crypto_posts if p.sentimento == "negativo")
    neutros = sum(1 for p in crypto_posts if p.sentimento == "neutro")
    nulos = total - len(crypto_posts)

    # ── Montar PDF ──
    pdf = SentCryptoPDF()
    hora_str = hora_inicio.strftime("%d/%m/%Y %H:%M")
    pdf._subtitulo = f"{moeda.upper()}/USDT  ·  {fonte}  ·  {hora_str}"
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    # Resumo
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, "Resumo", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    indice_str = f"{indice * 100:.1f}%" if indice is not None else "N/A"
    linhas_resumo = [
        f"Total de posts: {total}",
        f"Positivos: {positivos}  |  Negativos: {negativos}  |  Neutros: {neutros}  |  Não-crypto: {nulos}",
        f"Índice médio de sentimento: {indice_str}",
    ]
    for linha in linhas_resumo:
        pdf.cell(0, 6, linha, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # Tabela
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(15, 23, 42)
    pdf.set_text_color(255, 255, 255)
    col_widths = [10, 95, 25, 22, 18]
    headers = ["#", "Texto", "Sentimento", "Score", "Hora"]
    for w, h in zip(col_widths, headers):
        pdf.cell(w, 8, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for i, p in enumerate(posts, 1):
        # Cor do sentimento
        if p.sentimento == "positivo":
            pdf.set_text_color(22, 163, 74)
        elif p.sentimento == "negativo":
            pdf.set_text_color(220, 38, 38)
        elif p.sentimento == "nulo":
            pdf.set_text_color(148, 163, 184)
        else:
            pdf.set_text_color(0, 0, 0)

        texto = (p.texto or "")[:100] + ("..." if p.texto and len(p.texto) > 100 else "")
        # Sanitizar caracteres que a fonte Helvetica não suporta
        texto = texto.encode("latin-1", errors="replace").decode("latin-1")

        score_str = f"{p.score:.4f}" if p.score else "-"
        hora_post = p.timestamp_post.strftime("%H:%M") if p.timestamp_post else "-"

        pdf.set_text_color(0, 0, 0)
        pdf.cell(col_widths[0], 7, str(i), border=1, align="C")
        pdf.cell(col_widths[1], 7, texto, border=1)

        # Sentimento com cor
        if p.sentimento == "positivo":
            pdf.set_text_color(22, 163, 74)
        elif p.sentimento == "negativo":
            pdf.set_text_color(220, 38, 38)
        elif p.sentimento == "nulo":
            pdf.set_text_color(148, 163, 184)
        else:
            pdf.set_text_color(100, 100, 0)
        pdf.cell(col_widths[2], 7, p.sentimento or "-", border=1, align="C")

        pdf.set_text_color(0, 0, 0)
        pdf.cell(col_widths[3], 7, score_str, border=1, align="C")
        pdf.cell(col_widths[4], 7, hora_post, border=1, align="C")
        pdf.ln()

    # Salvar
    nome_arquivo = f"sentcrypto_{moeda.upper()}_{fonte}_{hora_inicio.strftime('%Y%m%d_%Hh')}.pdf"
    caminho = RELATORIOS_DIR / nome_arquivo
    pdf.output(str(caminho))
    logger.info("Relatório PDF salvo em: %s", caminho)

    return {
        "mensagem": f"Relatório gerado com sucesso!",
        "arquivo": nome_arquivo,
        "caminho_completo": str(caminho.resolve()),
        "url": f"/relatorios/{nome_arquivo}",
        "total_posts": total,
    }


# ── Correlação Sentimento vs Preço ──────────────────────────────────────────
@app.get("/correlacao", tags=["historico"])
def correlacao_sentimento_preco(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    db: Session = Depends(get_db),
):
    """Compara sentimento social com variação de preço hora a hora."""
    # 1. Buscar posts sociais agrupados por hora
    posts = (
        db.query(SocialPost)
        .filter(
            SocialPost.moeda == moeda.upper(),
            SocialPost.fonte == fonte,
            SocialPost.sentimento != "nulo",
        )
        .order_by(SocialPost.timestamp_post.asc())
        .all()
    )

    if not posts:
        return {"moeda": moeda.upper(), "fonte": fonte, "pontos": [], "resumo": {}}

    # Agrupar por hora
    agrupado: dict = {}
    for p in posts:
        hora = p.timestamp_post.replace(minute=0, second=0, microsecond=0)
        chave = hora.isoformat()
        if chave not in agrupado:
            agrupado[chave] = {"positivos": 0, "negativos": 0, "neutros": 0, "indices": []}
        agrupado[chave]["indices"].append(sentimento_para_indice(p.sentimento))
        if p.sentimento == "positivo":
            agrupado[chave]["positivos"] += 1
        elif p.sentimento == "negativo":
            agrupado[chave]["negativos"] += 1
        else:
            agrupado[chave]["neutros"] += 1

    # 2. Buscar preços da Binance para as últimas 48h
    symbol = f"{moeda.upper()}USDT"
    try:
        klines = fetch_binance_klines(symbol, interval="1h", limit=48)
    except Exception:
        klines = []

    precos_por_hora: dict = {}
    for k in klines:
        ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
        chave = ts.replace(minute=0, second=0, microsecond=0, tzinfo=None).isoformat()
        abertura = float(k[1])
        fechamento = float(k[4])
        variacao_pct = ((fechamento - abertura) / abertura * 100) if abertura else 0
        precos_por_hora[chave] = {
            "preco_abertura": round(abertura, 2),
            "preco_fechamento": round(fechamento, 2),
            "variacao_pct": round(variacao_pct, 4),
        }

    # 3. Montar pontos de correlação
    pontos = []
    acertos = 0
    erros_corr = 0
    total_comparavel = 0

    for chave, grupo in agrupado.items():
        media_sent = sum(grupo["indices"]) / len(grupo["indices"])
        preco_info = precos_por_hora.get(chave, {})
        variacao_preco = preco_info.get("variacao_pct")

        sentimento_direcao = "positivo" if media_sent > 0.55 else "negativo" if media_sent < 0.45 else "neutro"
        preco_direcao = None
        acertou = None

        if variacao_preco is not None:
            preco_direcao = "subiu" if variacao_preco > 0.05 else "desceu" if variacao_preco < -0.05 else "estável"

            if sentimento_direcao != "neutro" and preco_direcao != "estável":
                total_comparavel += 1
                if (sentimento_direcao == "positivo" and preco_direcao == "subiu") or \
                   (sentimento_direcao == "negativo" and preco_direcao == "desceu"):
                    acertos += 1
                    acertou = True
                else:
                    erros_corr += 1
                    acertou = False

        hora_fmt = chave.split("T")[1][:5] if "T" in chave else chave

        pontos.append({
            "hora": hora_fmt,
            "timestamp": chave,
            "sentimento_medio": round(media_sent, 2),
            "sentimento_direcao": sentimento_direcao,
            "variacao_preco": variacao_preco,
            "preco_direcao": preco_direcao,
            "acertou": acertou,
            "positivos": grupo["positivos"],
            "negativos": grupo["negativos"],
            "neutros": grupo["neutros"],
        })

    taxa_acerto = round((acertos / total_comparavel * 100), 1) if total_comparavel > 0 else None

    return {
        "moeda": moeda.upper(),
        "fonte": fonte,
        "pontos": pontos,
        "resumo": {
            "total_horas_analisadas": len(pontos),
            "total_comparavel": total_comparavel,
            "acertos": acertos,
            "erros": erros_corr,
            "taxa_acerto_pct": taxa_acerto,
        },
    }


# ── Coleta Reddit ──────────────────────────────────────────────────────────
@app.post("/coletar/reddit", tags=["coleta"])
def coletar_reddit(body: ColetaRedditRequest, db: Session = Depends(get_db)):
    """Coleta posts do Reddit, analisa sentimento com BERT e salva no banco."""
    if sentiment_pipeline is None:
        raise HTTPException(status_code=500, detail="Modelo BERT não carregado.")

    posts_brutos = coletar_posts_reddit_json(
        subreddits=body.subreddits,
        moeda=body.moeda,
        limite_por_sub=body.limite_por_sub,
        ordenacao=body.ordenacao,
    )

    salvos = 0
    erros = 0
    for raw in posts_brutos:
        try:
            analisar_e_salvar_post(
                db=db,
                moeda=body.moeda,
                fonte="Reddit",
                texto=raw["texto"],
                timestamp_post=raw["timestamp_post"],
            )
            salvos += 1
        except Exception:
            erros += 1

    return {
        "mensagem": f"Coleta finalizada: {salvos} posts salvos, {erros} erros.",
        "total_coletados": len(posts_brutos),
        "salvos": salvos,
        "erros": erros,
    }


# ── Feed do X (timeline em tempo real) ──────────────────────────────────────
@app.post("/feed/x", tags=["feed"])
def feed_x(body: FeedXRequest):
    """Puxa tweets recentes de perfis específicos e retorna com análise BERT."""
    try:
        tweets = coletar_feed_x(
            perfis=body.perfis,
            limite_por_perfil=body.limite_por_perfil,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Analisa sentimento de cada tweet (se BERT disponível)
    resultado = []
    for tw in tweets:
        sentimento = None
        indice = None
        score_bert = None
        crypto_relevante = texto_e_crypto_relevante(tw["texto"] or "")

        if sentiment_pipeline and tw["texto"] and crypto_relevante:
            try:
                r = sentiment_pipeline(
                    tw["texto"], truncation=True, max_length=512
                )[0]
                sentimento = mapear_estrela_para_sentimento(r["label"])
                indice = sentimento_para_indice(sentimento)
                score_bert = round(float(r["score"]), 4)
            except Exception:
                pass
        elif tw["texto"] and not crypto_relevante:
            sentimento = "nulo"
            indice = None
            score_bert = None

        resultado.append({
            "texto": tw["texto"],
            "perfil": tw["perfil"],
            "nome_exibicao": tw.get("nome_exibicao", tw["perfil"]),
            "avatar": tw.get("avatar"),
            "timestamp": tw["timestamp_post"],
            "tweet_id": tw.get("tweet_id"),
            "likes": tw.get("likes", 0),
            "retweets": tw.get("retweets", 0),
            "replies": tw.get("replies", 0),
            "sentimento": sentimento,
            "indice_sentimento": indice,
            "score_bert": score_bert,
            "crypto_relevante": crypto_relevante,
        })

    return {
        "total": len(resultado),
        "perfis": body.perfis,
        "tweets": resultado,
    }


# ── Coleta X (Twitter) ─────────────────────────────────────────────────────
@app.post("/coletar/x", tags=["coleta"])
def coletar_x(body: ColetaXRequest, db: Session = Depends(get_db)):
    """Coleta tweets de perfis específicos do X, analisa sentimento e salva."""
    if sentiment_pipeline is None:
        raise HTTPException(status_code=500, detail="Modelo BERT não carregado.")

    try:
        tweets_brutos = coletar_tweets_x(
            perfis=body.perfis,
            moeda=body.moeda,
            limite_por_perfil=body.limite_por_perfil,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    salvos = 0
    erros = 0
    for raw in tweets_brutos:
        try:
            analisar_e_salvar_post(
                db=db,
                moeda=body.moeda,
                fonte="X",
                texto=raw["texto"],
                timestamp_post=raw["timestamp_post"],
            )
            salvos += 1
        except Exception:
            erros += 1

    return {
        "mensagem": f"Coleta X finalizada: {salvos} tweets salvos, {erros} erros.",
        "total_coletados": len(tweets_brutos),
        "salvos": salvos,
        "erros": erros,
        "perfis_consultados": body.perfis,
    }


# ── Análise de texto livre ─────────────────────────────────────────────────
@app.post("/analisar-texto", tags=["sentimento"])
def analisar_texto(body: TextoParaAnalise):
    """Analisa o sentimento de um texto livre com BERT (sem salvar no banco)."""
    if sentiment_pipeline is None:
        raise HTTPException(status_code=500, detail="Modelo BERT não carregado.")

    texto = (body.texto or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Texto vazio.")

    result = sentiment_pipeline(texto, truncation=True, max_length=512)[0]
    sentimento = mapear_estrela_para_sentimento(result["label"])

    return {
        "texto": texto[:200],
        "sentimento": sentimento,
        "indice": sentimento_para_indice(sentimento),
        "score_bert": round(float(result["score"]), 4),
        "label_bert": result["label"],
    }
