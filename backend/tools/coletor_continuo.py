"""Coletor contínuo do SentCrypto — roda sem depender do navegador.

Por que existe
--------------
A coleta automática do dashboard é um ``setInterval`` do React: ela só roda
enquanto a aba está aberta na fonte "X". Fechar o navegador, trocar de aba ou
suspender a máquina interrompe a coleta silenciosamente, deixando buracos no
histórico que não dá para recuperar depois.

Este script roda como processo próprio, coleta em intervalo fixo, registra
tudo em arquivo e continua vivo quando uma fonte falha.

Uso
---
    # Coleta contínua a cada 20 minutos (Ctrl+C encerra)
    python tools/coletor_continuo.py --intervalo 20

    # Uma rodada só — para agendar no Agendador de Tarefas do Windows/cron
    python tools/coletor_continuo.py --once

    # Escolhendo moedas e perfis
    python tools/coletor_continuo.py --moedas BTC ETH --perfis whale_alert BitcoinMagazine

Observações
-----------
- O modelo BERT é carregado uma única vez, na inicialização.
- A deduplicação impede que rodadas frequentes inflem a base: um post já
  salvo é descartado antes mesmo de chegar ao modelo.
- Grava no mesmo banco que a API lê. O SQLite está em modo WAL, então o
  dashboard continua funcionando normalmente durante a coleta.
"""

import argparse
import logging
import logging.handlers
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import BASE_DIR, MOEDAS_SUPORTADAS
from collectors.reddit_collector import coletar_posts_reddit_json
from collectors.x_collector import coletar_tweets_x, limpar_cache
from db import SessionLocal
from services import posts as svc_posts
from services import sentimento as svc_sentimento

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("coletor")

# Perfis padrão: quanto mais fontes, mais horas do dia com publicações — e é
# a quantidade de HORAS cobertas, não de posts, que determina o tamanho da
# amostra comparável na correlação.
PERFIS_PADRAO = [
    "whale_alert",
    "BitcoinMagazine",
    "cointelegraph",
    "CoinDesk",
    "documentingbtc",
]

SUBREDDITS_PADRAO = {
    "BTC": ["Bitcoin", "CryptoCurrency", "BitcoinMarkets"],
    "ETH": ["ethereum", "ethtrader", "CryptoCurrency"],
    "SOL": ["solana", "CryptoCurrency"],
    "DOGE": ["dogecoin", "CryptoCurrency"],
    "XRP": ["XRP", "Ripple", "CryptoCurrency"],
    "ADA": ["cardano", "CryptoCurrency"],
    "AVAX": ["Avax", "CryptoCurrency"],
    "LINK": ["Chainlink", "CryptoCurrency"],
}

# Sinalizado pelo Ctrl+C para encerrar a rodada atual sem matar o processo
# no meio de uma gravação.
_encerrar = False


def configurar_log(verboso: bool) -> None:
    """Log simultâneo em arquivo (rotativo) e console."""
    formato = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Rotaciona a cada 5 MB, mantendo 5 arquivos — meses de coleta não podem
    # gerar um log de vários gigabytes.
    arquivo = logging.handlers.RotatingFileHandler(
        LOG_DIR / "coleta.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    arquivo.setFormatter(formato)

    console = logging.StreamHandler()
    console.setFormatter(formato)

    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG if verboso else logging.INFO)
    raiz.handlers.clear()
    raiz.addHandler(arquivo)
    raiz.addHandler(console)

    # Bibliotecas de rede são muito verbosas em execução longa.
    for ruidoso in ("urllib3", "httpx", "httpcore", "transformers"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)


def _tratar_sinal(_signum, _frame) -> None:
    global _encerrar
    if _encerrar:
        logger.warning("Encerramento forçado.")
        sys.exit(1)
    _encerrar = True
    logger.info("Encerrando após concluir a rodada atual... (Ctrl+C de novo força)")


def coletar_x(db, moeda: str, perfis: list[str], limite: int) -> svc_posts.ResultadoColeta:
    """Coleta e salva tweets de uma moeda."""
    try:
        brutos = coletar_tweets_x(perfis=perfis, moeda=moeda, limite_por_perfil=limite)
    except Exception as e:
        logger.warning("  X/%s: coleta falhou — %s", moeda, e)
        return svc_posts.ResultadoColeta()

    return svc_posts.salvar_posts(db, moeda, "X", brutos)


def coletar_reddit(db, moeda: str, limite: int) -> svc_posts.ResultadoColeta:
    """Coleta e salva posts do Reddit de uma moeda."""
    subreddits = SUBREDDITS_PADRAO.get(moeda, ["CryptoCurrency"])
    avisos: list[str] = []

    try:
        brutos = coletar_posts_reddit_json(
            subreddits=subreddits, moeda=moeda,
            limite_por_sub=limite, ordenacao="new", avisos=avisos,
        )
    except Exception as e:
        logger.warning("  Reddit/%s: coleta falhou — %s", moeda, e)
        return svc_posts.ResultadoColeta()

    if avisos and not brutos:
        logger.warning("  Reddit/%s: %s", moeda, avisos[0])

    return svc_posts.salvar_posts(db, moeda, "Reddit", brutos)


def rodada(moedas: list[str], perfis: list[str], limite: int,
           usar_reddit: bool) -> dict:
    """Executa uma rodada completa de coleta. Nunca levanta exceção."""
    totais = {"salvos": 0, "duplicados": 0, "erros": 0}

    # O cache do coletor do X guarda resultados por alguns minutos; numa
    # rodada nova queremos os tweets realmente mais recentes.
    limpar_cache()

    db = SessionLocal()
    try:
        for moeda in moedas:
            r = coletar_x(db, moeda, perfis, limite)
            if r.recebidos or r.salvos:
                logger.info("  X/%-5s %s", moeda, r.mensagem)
            for chave in totais:
                totais[chave] += getattr(r, chave)

            if usar_reddit:
                r = coletar_reddit(db, moeda, limite)
                if r.recebidos or r.salvos:
                    logger.info("  Reddit/%-5s %s", moeda, r.mensagem)
                for chave in totais:
                    totais[chave] += getattr(r, chave)

            if _encerrar:
                break
    except Exception as e:
        # Uma rodada com problema não pode derrubar dias de coleta.
        logger.exception("Erro inesperado na rodada: %s", e)
    finally:
        db.close()

    return totais


def resumo_da_base() -> str:
    """Estatísticas atuais do banco, para acompanhar o progresso da amostra."""
    from sqlalchemy import func

    db = SessionLocal()
    try:
        from models import SocialPost

        total = db.query(func.count(SocialPost.id)).scalar() or 0
        relevantes = (
            db.query(func.count(SocialPost.id))
            .filter(SocialPost.sentimento != svc_sentimento.NULO)
            .scalar()
        ) or 0
        # Horas distintas é o que realmente limita a correlação.
        horas = (
            db.query(func.count(func.distinct(
                func.strftime("%Y-%m-%d %H", SocialPost.timestamp_post)
            )))
            .filter(SocialPost.sentimento != svc_sentimento.NULO)
            .scalar()
        ) or 0
        return f"base: {total} posts ({relevantes} relevantes) em {horas} horas distintas"
    except Exception as e:
        return f"base: indisponível ({e})"
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coletor contínuo do SentCrypto",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--intervalo", type=int, default=20,
                        help="minutos entre rodadas (padrão: 20)")
    parser.add_argument("--moedas", nargs="+", default=["BTC"],
                        help="moedas a coletar (padrão: BTC)")
    parser.add_argument("--perfis", nargs="+", default=PERFIS_PADRAO,
                        help="perfis do X")
    parser.add_argument("--limite", type=int, default=30,
                        help="posts por perfil/subreddit (padrão: 30)")
    parser.add_argument("--sem-reddit", action="store_true",
                        help="coletar apenas do X")
    parser.add_argument("--once", action="store_true",
                        help="uma rodada só (para uso com agendador)")
    parser.add_argument("-v", "--verboso", action="store_true")
    args = parser.parse_args()

    configurar_log(args.verboso)

    invalidas = [m for m in args.moedas if m.upper() not in MOEDAS_SUPORTADAS]
    if invalidas:
        logger.error("Moedas não suportadas: %s", ", ".join(invalidas))
        logger.error("Disponíveis: %s", ", ".join(MOEDAS_SUPORTADAS))
        sys.exit(1)
    moedas = [m.upper() for m in args.moedas]

    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    logger.info("=" * 68)
    logger.info("SentCrypto — coletor contínuo")
    logger.info("  moedas:    %s", ", ".join(moedas))
    logger.info("  perfis X:  %s", ", ".join(args.perfis))
    logger.info("  reddit:    %s", "não" if args.sem_reddit else "sim")
    logger.info("  modo:      %s", "rodada única" if args.once
                else f"contínuo a cada {args.intervalo} min")
    logger.info("  log:       %s", LOG_DIR / "coleta.log")
    logger.info("=" * 68)

    logger.info("Carregando modelo BERT...")
    if not svc_sentimento.carregar_modelo():
        logger.error("Modelo não carregou. Coleta abortada.")
        sys.exit(1)

    acumulado = {"salvos": 0, "duplicados": 0, "erros": 0}
    n_rodadas = 0
    inicio = datetime.now()

    while not _encerrar:
        n_rodadas += 1
        logger.info("── Rodada %d ──────────────────────────────", n_rodadas)

        t0 = time.time()
        totais = rodada(moedas, args.perfis, args.limite, not args.sem_reddit)
        for chave in acumulado:
            acumulado[chave] += totais[chave]

        logger.info(
            "  rodada: +%d novos, %d duplicados, %d erros (%.1fs)",
            totais["salvos"], totais["duplicados"], totais["erros"],
            time.time() - t0,
        )
        logger.info("  %s", resumo_da_base())

        if args.once or _encerrar:
            break

        # Espera em fatias curtas para o Ctrl+C responder na hora, em vez de
        # ficar preso num sleep de vários minutos.
        proxima = time.time() + args.intervalo * 60
        while time.time() < proxima and not _encerrar:
            time.sleep(1)

    duracao = datetime.now() - inicio
    logger.info("=" * 68)
    logger.info("Coleta encerrada após %d rodada(s) em %s",
                n_rodadas, str(duracao).split(".")[0])
    logger.info("  total acumulado: +%d novos, %d duplicados, %d erros",
                acumulado["salvos"], acumulado["duplicados"], acumulado["erros"])
    logger.info("  %s", resumo_da_base())
    logger.info("=" * 68)


if __name__ == "__main__":
    main()
