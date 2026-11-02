"""SentCrypto — API de análise de sentimento do mercado de criptomoedas.

Camadas
-------
``app.py``      montagem da aplicação (middlewares, arquivos estáticos)
``routers/``    rotas HTTP por domínio (valida entrada, chama serviço, responde)
``services/``   regras de negócio (BERT, Binance, correlação, PDF)
``collectors/`` integrações externas (Reddit, X)
``models.py``   persistência

As rotas são propositalmente finas: toda regra que aparecia repetida entre
endpoints foi movida para ``services/``.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import CORS_ORIGINS, RELATORIOS_DIR
from db import criar_tabelas
from routers import (
    analise,
    coleta,
    contas,
    correlacao,
    mercado,
    relatorios,
    saude,
    simulacao,
    social,
)
from services import sentimento as svc_sentimento

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentcrypto")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Prepara o banco e carrega o modelo antes de aceitar requisições."""
    criar_tabelas()
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

# Mesma ordem das seções do antigo app.py (é a ordem em /docs).
app.include_router(saude.router)
app.include_router(contas.router)
app.include_router(mercado.router)
app.include_router(social.router)
app.include_router(correlacao.router)
app.include_router(simulacao.router)
app.include_router(relatorios.router)
app.include_router(coleta.router)
app.include_router(analise.router)
