"""SentCrypto — API de análise de sentimento do mercado de criptomoedas.

Camadas
-------
``app.py``      rotas HTTP (esta camada: valida entrada, chama serviço, responde)
``services/``   regras de negócio (BERT, Binance, correlação, PDF)
``collectors/`` integrações externas (Reddit, X)
``models.py``   persistência

As rotas são propositalmente finas: toda regra que aparecia repetida entre
endpoints foi movida para ``services/``.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from collectors.cookie_auth import COOKIES_PATH, cookies_validos
from collectors.reddit_collector import coletar_posts_reddit_json
from collectors.x_collector import coletar_feed_x, coletar_tweets_x
from config import (
    ADMIN_TOKEN,
    CORS_ORIGINS,
    DIAS_JANELA_PADRAO,
    MOEDAS_SUPORTADAS,
    RELATORIOS_DIR,
    reddit_autenticado,
)
from db import Base, engine, get_db
from models import MarketPoint, SocialPost
from schemas import (
    ColetaRedditRequest,
    ColetaXRequest,
    FeedXRequest,
    LoginXRequest,
    TextoParaAnalise,
)
from services import correlacao as svc_correlacao
from services import mercado as svc_mercado
from services import posts as svc_posts
from services import relatorios as svc_relatorios
from services import sentimento as svc_sentimento
from utils.tempo import (
    agora_utc,
    para_iso_utc,
    parse_data_fim,
    parse_data_inicio,
    truncar_hora,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentcrypto")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Prepara o banco e carrega o modelo antes de aceitar requisições."""
    Base.metadata.create_all(bind=engine)
    # Carregar o BERT leva dezenas de segundos; fazer isso fora do event loop
    # evita que o servidor pareça travado durante a inicialização.
    await run_in_threadpool(svc_sentimento.carregar_modelo)
    yield


app = FastAPI(
    title="SentCrypto API",
    description="API de análise de sentimento do mercado de criptomoedas",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

RELATORIOS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/relatorios",
    StaticFiles(directory=str(RELATORIOS_DIR)),
    name="relatorios",
)


# ── Helpers de rota ─────────────────────────────────────────────────────────


def validar_moeda(moeda: str) -> str:
    """Normaliza e recusa moedas fora da lista suportada.

    Sem isso, um símbolo inválido só falhava lá na Binance, devolvendo um 502
    confuso em vez de dizer que a moeda não é aceita.
    """
    moeda_u = (moeda or "").strip().upper()
    if moeda_u not in MOEDAS_SUPORTADAS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Moeda '{moeda}' não suportada. "
                f"Disponíveis: {', '.join(MOEDAS_SUPORTADAS)}."
            ),
        )
    return moeda_u


def validar_fonte(fonte: str) -> str:
    """Normaliza a fonte social para os valores gravados no banco."""
    mapa = {"x": "X", "twitter": "X", "reddit": "Reddit"}
    normalizada = mapa.get((fonte or "").strip().lower())
    if not normalizada:
        raise HTTPException(
            status_code=400, detail="Fonte inválida. Use 'X' ou 'Reddit'."
        )
    return normalizada


def exigir_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Protege rotas que alteram credenciais.

    Se ``ADMIN_TOKEN`` não estiver configurado, a rota continua aberta — o
    padrão de desenvolvimento local. Em qualquer exposição de rede, definir
    ADMIN_TOKEN no .env passa a ser obrigatório.
    """
    if not ADMIN_TOKEN:
        return
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token administrativo inválido.")


def _buscar_klines_ou_502(simbolo: str, **kwargs) -> list:
    try:
        return svc_mercado.buscar_klines(simbolo, **kwargs)
    except svc_mercado.ErroBinance as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════════════════
#  Saúde e autenticação
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/", tags=["health"])
def health_check():
    """Estado geral da API."""
    return {
        "status": "ok",
        "versao": app.version,
        "bert_carregado": svc_sentimento.modelo_carregado(),
        "twitter_cookies": cookies_validos(),
        "reddit_oauth": reddit_autenticado(),
        "moedas_suportadas": MOEDAS_SUPORTADAS,
    }


@app.get("/status/reddit", tags=["health"])
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


@app.get("/status/twitter", tags=["health"])
def twitter_status():
    """Diz se os cookies do X estão configurados."""
    valido = cookies_validos()
    return {
        "cookies_validos": valido,
        "mensagem": (
            "Cookies do X válidos. Pronto para coletar."
            if valido
            else "Cookies do X não encontrados. Configure via /login/x."
        ),
    }


@app.post("/login/x", tags=["auth"], dependencies=[Depends(exigir_admin)])
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


# ═══════════════════════════════════════════════════════════════════════════
#  Preço e sentimento de mercado
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/sentimento", tags=["sentimento"])
def sentimento_atual(moeda: str = Query("BTC")):
    """Sentimento derivado do último candle horário da Binance."""
    moeda_u = validar_moeda(moeda)
    candles = _buscar_klines_ou_502(
        svc_mercado.simbolo_para(moeda_u), intervalo="1h", limite=2
    )

    if not candles:
        raise HTTPException(status_code=404, detail="Nenhum candle retornado.")

    ultimo = candles[-1]
    return {
        "moeda": moeda_u,
        "sentimento_atual": ultimo.sentimento,
        "indice_sentimento": round(ultimo.indice_sentimento, 2),
        "preco": round(ultimo.fechamento, 2),
        "variacao_percentual": round(ultimo.variacao_pct, 4),
        "timestamp_candle": para_iso_utc(ultimo.timestamp),
        "ultimo_update": para_iso_utc(agora_utc()),
    }


@app.get("/historico-sentimento", tags=["historico"])
def historico_sentimento(
    moeda: str = Query("BTC"),
    limite: int = Query(24, ge=1, le=1000),
    data_inicio: str = Query(None, description="Data ISO (ex: 2026-03-01)"),
    data_fim: str = Query(None, description="Data ISO (ex: 2026-03-03)"),
):
    """Histórico de preço + sentimento consultado ao vivo na Binance."""
    moeda_u = validar_moeda(moeda)

    candles = _buscar_klines_ou_502(
        svc_mercado.simbolo_para(moeda_u),
        intervalo="1h",
        limite=limite,
        inicio=parse_data_inicio(data_inicio),
        fim=parse_data_fim(data_fim),
    )

    return {
        "moeda": moeda_u,
        "pontos": [
            {
                "timestamp": para_iso_utc(c.timestamp),
                "preco": round(c.fechamento, 2),
                "indice_sentimento": c.indice_sentimento,
            }
            for c in candles
        ],
    }


@app.get("/historico-db", tags=["historico"])
def historico_db(
    moeda: str = Query("BTC"),
    limite: int = Query(500, ge=1, le=2000),
    data_inicio: str = Query(None),
    data_fim: str = Query(None),
    db: Session = Depends(get_db),
):
    """Histórico salvo em ``market_points``.

    Se o banco estiver vazio para o filtro pedido, busca na Binance e persiste,
    de modo que a primeira visita já mostre dados.
    """
    moeda_u = validar_moeda(moeda)
    inicio = parse_data_inicio(data_inicio)
    fim = parse_data_fim(data_fim)

    def consultar():
        query = db.query(MarketPoint).filter(MarketPoint.moeda == moeda_u)
        if inicio:
            query = query.filter(MarketPoint.timestamp >= inicio)
        if fim:
            query = query.filter(MarketPoint.timestamp <= fim)
        registros = query.order_by(MarketPoint.timestamp.desc()).limit(limite).all()
        registros.reverse()
        return registros

    registros = consultar()

    if not registros:
        try:
            candles = svc_mercado.buscar_klines(
                svc_mercado.simbolo_para(moeda_u),
                intervalo="1h",
                limite=500 if (inicio or fim) else 24,
                inicio=inicio,
                fim=fim,
            )
            novos = svc_mercado.salvar_candles(db, moeda_u, candles)
            logger.info("Sincronizou %d novos pontos de %s.", novos, moeda_u)
            registros = consultar()
        except svc_mercado.ErroBinance as e:
            # Sem dados locais nem remotos: devolve vazio em vez de derrubar
            # o dashboard inteiro.
            logger.warning("Falha ao popular histórico de %s: %s", moeda_u, e)

    return {
        "moeda": moeda_u,
        "pontos": [
            {
                "timestamp": para_iso_utc(r.timestamp),
                "preco": round(r.preco, 2) if r.preco is not None else None,
                "indice_sentimento": (
                    round(r.indice_sentimento, 2)
                    if r.indice_sentimento is not None
                    else None
                ),
            }
            for r in registros
        ],
    }


@app.post("/sync-binance", tags=["historico"])
def sync_binance(
    moeda: str = Query("BTC"),
    dias: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Baixa os últimos N dias de candles e grava no banco (sem duplicar)."""
    moeda_u = validar_moeda(moeda)

    from datetime import timedelta

    inicio = agora_utc() - timedelta(days=dias)
    candles = _buscar_klines_ou_502(
        svc_mercado.simbolo_para(moeda_u),
        intervalo="1h",
        limite=dias * 24,
        inicio=inicio,
    )

    novos = svc_mercado.salvar_candles(db, moeda_u, candles)

    return {
        "mensagem": f"Sincronização concluída: {novos} novos pontos salvos.",
        "moeda": moeda_u,
        "total_klines": len(candles),
        "novos": novos,
        "atualizados": len(candles) - novos,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Histórico social
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/historico-social", tags=["historico"])
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


@app.get("/posts-por-hora", tags=["historico"])
def posts_por_hora(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    hora: str = Query(..., description="ISO da hora (ex: 2026-03-03T14:00:00Z)"),
    db: Session = Depends(get_db),
):
    """Todos os posts de uma hora específica, com o resumo da hora."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)
    hora_inicio = _parse_hora_obrigatoria(hora)

    posts = _posts_da_hora(db, moeda_u, fonte_n, hora_inicio)

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


def _parse_hora_obrigatoria(hora: str) -> datetime:
    dt = parse_data_inicio(hora)
    if dt is None:
        raise HTTPException(
            status_code=400, detail="Formato de hora inválido. Use ISO 8601."
        )
    return truncar_hora(dt)


def _posts_da_hora(
    db: Session, moeda: str, fonte: str, hora_inicio: datetime
) -> list[SocialPost]:
    from datetime import timedelta

    # Intervalo semiaberto [hora, hora+1h): evita depender de microssegundos
    # para não incluir/excluir posts na virada da hora.
    hora_fim = hora_inicio + timedelta(hours=1)
    return (
        db.query(SocialPost)
        .filter(
            SocialPost.moeda == moeda,
            SocialPost.fonte == fonte,
            SocialPost.timestamp_post >= hora_inicio,
            SocialPost.timestamp_post < hora_fim,
        )
        .order_by(SocialPost.timestamp_post.asc())
        .all()
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Correlação
# ═══════════════════════════════════════════════════════════════════════════


@app.get("/correlacao", tags=["correlacao"])
async def correlacao_sentimento_preco(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    db: Session = Depends(get_db),
):
    """Compara o sentimento social com a variação de preço, hora a hora."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)
    # A função consulta a Binance (I/O bloqueante) — fora do event loop.
    return await run_in_threadpool(
        svc_correlacao.calcular_correlacao, db, moeda_u, fonte_n
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Relatórios PDF
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/gerar-relatorio", tags=["relatorio"])
async def gerar_relatorio(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    hora: str = Query(..., description="ISO da hora"),
    indice: float = Query(None, ge=0, le=1),
    db: Session = Depends(get_db),
):
    """Gera o PDF com os posts de uma hora e devolve o link para download."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)
    hora_inicio = _parse_hora_obrigatoria(hora)

    posts = _posts_da_hora(db, moeda_u, fonte_n, hora_inicio)
    if not posts:
        raise HTTPException(
            status_code=404,
            detail="Nenhum post encontrado nessa hora para gerar o relatório.",
        )

    caminho = await run_in_threadpool(
        svc_relatorios.gerar_relatorio_posts,
        moeda_u, fonte_n, hora_inicio, posts, indice,
    )

    return {
        "mensagem": "Relatório gerado com sucesso!",
        "arquivo": caminho.name,
        "caminho_completo": str(caminho.resolve()),
        "url": f"/relatorios/{caminho.name}",
        "total_posts": len(posts),
    }


@app.post("/gerar-relatorio-correlacao", tags=["relatorio"])
async def gerar_relatorio_correlacao(
    moeda: str = Query("BTC"),
    fonte: str = Query("X"),
    db: Session = Depends(get_db),
):
    """Gera o PDF de correlação sentimento x preço."""
    moeda_u = validar_moeda(moeda)
    fonte_n = validar_fonte(fonte)

    dados = await run_in_threadpool(
        svc_correlacao.calcular_correlacao, db, moeda_u, fonte_n
    )
    if not dados["pontos"]:
        raise HTTPException(
            status_code=404,
            detail="Nenhum post analisado ainda. Faça uma coleta antes.",
        )

    caminho = await run_in_threadpool(
        svc_relatorios.gerar_relatorio_correlacao, dados
    )

    return {
        "mensagem": "Relatório de correlação gerado com sucesso!",
        "arquivo": caminho.name,
        "caminho_completo": str(caminho.resolve()),
        "url": f"/relatorios/{caminho.name}",
        "total_horas": len(dados["pontos"]),
        "taxa_acerto": dados["resumo"]["taxa_acerto_pct"],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Coleta
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/coletar/reddit", tags=["coleta"])
async def coletar_reddit(
    body: ColetaRedditRequest, db: Session = Depends(get_db)
):
    """Coleta posts do Reddit, classifica com BERT e salva sem duplicar."""
    _exigir_bert()
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


@app.post("/coletar/x", tags=["coleta"])
async def coletar_x(body: ColetaXRequest, db: Session = Depends(get_db)):
    """Coleta tweets dos perfis, classifica com BERT e salva sem duplicar."""
    _exigir_bert()
    moeda_u = validar_moeda(body.moeda)

    try:
        brutos = await run_in_threadpool(
            coletar_tweets_x, body.perfis, moeda_u, body.limite_por_perfil
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    resultado = await run_in_threadpool(
        svc_posts.salvar_posts, db, moeda_u, "X", brutos
    )
    return {**resultado.como_dict(), "moeda": moeda_u,
            "perfis_consultados": body.perfis}


@app.post("/feed/x", tags=["feed"])
async def feed_x(body: FeedXRequest):
    """Timeline dos perfis com análise de sentimento, sem gravar no banco."""
    try:
        tweets = await run_in_threadpool(
            coletar_feed_x, body.perfis, body.limite_por_perfil
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    resultado = await run_in_threadpool(_classificar_feed, tweets)

    return {"total": len(resultado), "perfis": body.perfis, "tweets": resultado}


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


# ═══════════════════════════════════════════════════════════════════════════
#  Análise avulsa
# ═══════════════════════════════════════════════════════════════════════════


@app.post("/analisar-texto", tags=["sentimento"])
async def analisar_texto(body: TextoParaAnalise):
    """Classifica um texto livre com o BERT, sem salvar no banco."""
    _exigir_bert()

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


def _exigir_bert() -> None:
    if not svc_sentimento.modelo_carregado():
        raise HTTPException(
            status_code=503,
            detail="Modelo BERT não carregado. Verifique os logs do servidor.",
        )
