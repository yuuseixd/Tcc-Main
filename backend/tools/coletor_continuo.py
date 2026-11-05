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

    # Lista grande de perfis (perfis_x.txt), 25 por rodada em rodízio
    python tools/coletor_continuo.py --lote 25

Observações
-----------
- O modelo BERT é carregado uma única vez, na inicialização.
- A deduplicação impede que rodadas frequentes inflem a base: um post já
  salvo é descartado antes mesmo de chegar ao modelo.
- Grava no mesmo banco que a API lê. O SQLite está em modo WAL, então o
  dashboard continua funcionando normalmente durante a coleta.
- Sem ``--perfis``, a lista vem de ``perfis_x.txt``. O X libera cerca de 50
  leituras de timeline a cada 15 min por conta; coletar 100 perfis de uma
  vez estoura o limite (HTTP 429). Por isso cada rodada pega só ``--lote``
  perfis, em rodízio: com 100 perfis, lote 25 e intervalo de 20 min, cada
  perfil é lido a cada 80 min.
- Perfis marcados com ``*`` em ``perfis_x.txt`` são lidos em toda rodada e
  ocupam vagas do lote (5 prioritários + 20 em rodízio = os mesmos 25
  pedidos). Servem para as contas que mais postam: em dias de mercado
  agitado elas aceleram, e é quando a correlação mais importa.
- Posts com mais de ``IDADE_MAXIMA_POST_DIAS`` (60) dias são ignorados.
- A posição do rodízio fica em ``logs/coletor_estado.json``: reiniciar o
  coletor continua de onde parou. Perfis que ficaram sem leitura porque a
  conta caiu no limite entram primeiro na rodada seguinte.
- O dashboard usa a mesma conta do X. Com a coleta automática do dashboard
  ligada ao mesmo tempo, os dois dividem o mesmo limite.
"""

import argparse
import json
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
from db import SessionLocal, criar_tabelas
from services import posts as svc_posts
from services import sentimento as svc_sentimento

LOG_DIR = BASE_DIR / "logs"
ARQUIVO_PERFIS = BASE_DIR / "perfis_x.txt"
LOG_DIR.mkdir(exist_ok=True)
ARQUIVO_ESTADO = LOG_DIR / "coletor_estado.json"

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


def _ler_lista(caminho: Path) -> list[tuple[str, bool]]:
    """(perfil, prioritário) de cada linha; ``#`` comenta, ``*`` marca prioridade."""
    itens: list[tuple[str, bool]] = []
    vistos: set[str] = set()
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        nome = linha.split("#", 1)[0].strip()
        prioritario = nome.startswith("*")
        nome = nome.lstrip("*").strip().lstrip("@")
        if nome and nome.lower() not in vistos:
            vistos.add(nome.lower())
            itens.append((nome, prioritario))
    return itens


def carregar_perfis(caminho: Path = ARQUIVO_PERFIS) -> list[str]:
    """Perfis de ``perfis_x.txt`` (um por linha, ``#`` comenta), sem repetição."""
    if not caminho.exists():
        return list(PERFIS_PADRAO)
    return [nome for nome, _ in _ler_lista(caminho)] or list(PERFIS_PADRAO)


def carregar_prioritarios(caminho: Path = ARQUIVO_PERFIS) -> list[str]:
    """Perfis marcados com ``*`` em ``perfis_x.txt``: lidos em toda rodada."""
    if not caminho.exists():
        return []
    return [nome for nome, prioritario in _ler_lista(caminho) if prioritario]


def carregar_estado(caminho: Path = ARQUIVO_ESTADO) -> dict:
    """Posição do rodízio e perfis adiados, salvos entre execuções."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dados = {}
    return {"proximo": int(dados.get("proximo", 0)),
            "pendentes": list(dados.get("pendentes", []))}


def salvar_estado(estado: dict, caminho: Path = ARQUIVO_ESTADO) -> None:
    try:
        caminho.write_text(json.dumps(estado, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Não foi possível salvar o estado do rodízio: %s", e)


def montar_lote(perfis: list[str], estado: dict, tamanho: int) -> tuple[list[str], int]:
    """Perfis da rodada: primeiro os adiados, depois os próximos do rodízio.

    Devolve ``(lote, avanco)``: ``avanco`` é quantas posições do rodízio a
    rodada consumiu — o que salvar em ``estado["proximo"]`` depois. Sem
    estado salvo, um reinício voltava ao começo da lista e relia os mesmos
    perfis; e os que esbarravam no limite do X só voltavam voltas depois.
    """
    if tamanho <= 0 or tamanho >= len(perfis):
        return list(perfis), 0

    por_nome = {p.lower(): p for p in perfis}
    lote: list[str] = []
    for nome in estado["pendentes"]:
        perfil = por_nome.get(nome.lower())
        if perfil and perfil not in lote and len(lote) < tamanho:
            lote.append(perfil)

    inicio = estado["proximo"] % len(perfis)
    avanco = 0
    while len(lote) < tamanho and avanco < len(perfis):
        perfil = perfis[(inicio + avanco) % len(perfis)]
        avanco += 1
        if perfil not in lote:
            lote.append(perfil)
    return lote, avanco


def lote_da_rodada(perfis: list[str], prioritarios: list[str], estado: dict,
                   tamanho: int) -> tuple[list[str], int, int]:
    """Prioritários em toda rodada; o resto do lote vem do rodízio dos demais.

    Devolve ``(lote, avanco, tamanho_do_rodizio)``. O total por rodada não
    muda (``tamanho``): os prioritários ocupam vagas do rodízio, então a
    conta do X recebe o mesmo número de requisições.
    """
    if tamanho <= 0 or tamanho >= len(perfis):
        return list(perfis), 0, len(perfis)
    marcados = {p.lower() for p in prioritarios}
    # Pelo menos uma vaga fica para o rodízio, senão os demais nunca seriam lidos.
    fixos = [p for p in perfis if p.lower() in marcados][:tamanho - 1]
    rodizio = [p for p in perfis if p not in fixos]
    lote, avanco = montar_lote(rodizio, estado, tamanho - len(fixos))
    return fixos + lote, avanco, len(rodizio)


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


def coletar_x(db, moeda: str, perfis: list[str], limite: int,
              adiados: list[str]) -> svc_posts.ResultadoColeta:
    """Coleta e salva tweets de uma moeda; ``adiados`` recebe os não lidos."""
    descartados: list[dict] = []
    try:
        brutos = coletar_tweets_x(perfis=perfis, moeda=moeda, limite_por_perfil=limite,
                                  descartados=descartados, nao_lidos=adiados)
    except Exception as e:
        logger.warning("  X/%s: coleta falhou — %s", moeda, e)
        return svc_posts.ResultadoColeta()

    brutos, antigos = svc_posts.separar_antigos(brutos)
    descartados, antigos_descartados = svc_posts.separar_antigos(descartados)
    resultado = svc_posts.salvar_posts(db, moeda, "X", brutos)
    resultado.descartados = svc_posts.registrar_descartados(db, moeda, "X", descartados)
    resultado.antigos = antigos + antigos_descartados
    return resultado


def coletar_reddit(db, moeda: str, limite: int) -> svc_posts.ResultadoColeta:
    """Coleta e salva posts do Reddit de uma moeda."""
    subreddits = SUBREDDITS_PADRAO.get(moeda, ["CryptoCurrency"])
    avisos: list[str] = []
    descartados: list[dict] = []

    try:
        brutos = coletar_posts_reddit_json(
            subreddits=subreddits, moeda=moeda,
            limite_por_sub=limite, ordenacao="new", avisos=avisos,
            descartados=descartados,
        )
    except Exception as e:
        logger.warning("  Reddit/%s: coleta falhou — %s", moeda, e)
        return svc_posts.ResultadoColeta()

    if avisos and not brutos:
        logger.warning("  Reddit/%s: %s", moeda, avisos[0])

    brutos, antigos = svc_posts.separar_antigos(brutos)
    descartados, antigos_descartados = svc_posts.separar_antigos(descartados)
    resultado = svc_posts.salvar_posts(db, moeda, "Reddit", brutos)
    resultado.descartados = svc_posts.registrar_descartados(
        db, moeda, "Reddit", descartados
    )
    resultado.antigos = antigos + antigos_descartados
    return resultado


def rodada(moedas: list[str], perfis: list[str], limite: int,
           usar_reddit: bool, adiados: list[str] | None = None) -> dict:
    """Executa uma rodada completa de coleta. Nunca levanta exceção.

    ``adiados`` recebe os perfis do X que ficaram sem leitura porque a conta
    caiu no limite de requisições.
    """
    if adiados is None:
        adiados = []
    totais = {"salvos": 0, "duplicados": 0, "erros": 0, "descartados": 0, "antigos": 0}

    # O cache do coletor do X guarda resultados por alguns minutos; numa
    # rodada nova queremos os tweets realmente mais recentes.
    limpar_cache()

    db = SessionLocal()
    try:
        for moeda in moedas:
            r = coletar_x(db, moeda, perfis, limite, adiados)
            if r.recebidos or r.salvos or r.descartados:
                logger.info("  X/%-5s %s", moeda, r.mensagem)
            for chave in totais:
                totais[chave] += getattr(r, chave)

            if usar_reddit:
                r = coletar_reddit(db, moeda, limite)
                if r.recebidos or r.salvos or r.descartados:
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
        from models import PostDescartado, SocialPost

        total = db.query(func.count(SocialPost.id)).scalar() or 0
        descartados = db.query(func.count(PostDescartado.id)).scalar() or 0
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
        return (f"base: {total + descartados} posts analisados, {relevantes} relevantes "
                f"em {horas} horas distintas ({descartados} descartados sem a moeda)")
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
    parser.add_argument("--perfis", nargs="+", default=None,
                        help="perfis do X (padrão: lista de perfis_x.txt)")
    parser.add_argument("--lote", type=int, default=25,
                        help="perfis do X por rodada, em rodízio (0 = todos; padrão: 25)")
    parser.add_argument("--limite", type=int, default=30,
                        help="posts por perfil/subreddit (padrão: 30)")
    parser.add_argument("--sem-reddit", action="store_true",
                        help="coletar apenas do X")
    parser.add_argument("--once", action="store_true",
                        help="uma rodada só (para uso com agendador)")
    parser.add_argument("-v", "--verboso", action="store_true")
    args = parser.parse_args()

    configurar_log(args.verboso)
    # O coletor pode rodar sem a API ter subido com a versão atual do código:
    # sem isto, uma tabela nova (ex.: posts_descartados) não existiria.
    criar_tabelas()

    invalidas = [m for m in args.moedas if m.upper() not in MOEDAS_SUPORTADAS]
    if invalidas:
        logger.error("Moedas não suportadas: %s", ", ".join(invalidas))
        logger.error("Disponíveis: %s", ", ".join(MOEDAS_SUPORTADAS))
        sys.exit(1)
    moedas = [m.upper() for m in args.moedas]
    perfis = args.perfis or carregar_perfis()
    # Com --perfis explícito não há marcação: todos entram no rodízio.
    prioritarios = [] if args.perfis else carregar_prioritarios()

    signal.signal(signal.SIGINT, _tratar_sinal)
    signal.signal(signal.SIGTERM, _tratar_sinal)

    logger.info("=" * 68)
    logger.info("SentCrypto — coletor contínuo")
    logger.info("  moedas:    %s", ", ".join(moedas))
    if 0 < args.lote < len(perfis):
        fixos = min(len(prioritarios), args.lote - 1)
        logger.info("  perfis X:  %d (%d prioritários em toda rodada + %d em rodízio)",
                    len(perfis), fixos, args.lote - fixos)
    else:
        logger.info("  perfis X:  %d (todos em toda rodada)", len(perfis))
    logger.info("  reddit:    %s", "não" if args.sem_reddit else "sim")
    logger.info("  modo:      %s", "rodada única" if args.once
                else f"contínuo a cada {args.intervalo} min")
    logger.info("  log:       %s", LOG_DIR / "coleta.log")
    logger.info("=" * 68)

    logger.info("Carregando modelo BERT...")
    if not svc_sentimento.carregar_modelo():
        logger.error("Modelo não carregou. Coleta abortada.")
        sys.exit(1)

    acumulado = {"salvos": 0, "duplicados": 0, "erros": 0, "descartados": 0, "antigos": 0}
    n_rodadas = 0
    inicio = datetime.now()
    estado = carregar_estado()

    while not _encerrar:
        n_rodadas += 1
        logger.info("── Rodada %d ──────────────────────────────", n_rodadas)

        t0 = time.time()
        lote, avanco, n_rodizio = lote_da_rodada(perfis, prioritarios, estado, args.lote)
        logger.info("  perfis desta rodada: %s", ", ".join(lote))
        adiados: list[str] = []
        totais = rodada(moedas, lote, args.limite, not args.sem_reddit, adiados)
        estado = {"proximo": (estado["proximo"] + avanco) % n_rodizio,
                  "pendentes": list(dict.fromkeys(adiados))}
        salvar_estado(estado)
        if estado["pendentes"]:
            logger.info("  %d perfil(is) adiado(s) para a próxima rodada",
                        len(estado["pendentes"]))
        for chave in acumulado:
            acumulado[chave] += totais[chave]

        logger.info(
            "  rodada: +%d novos, %d duplicados, %d erros, %d descartados, "
            "%d antigos ignorados (%.1fs)",
            totais["salvos"], totais["duplicados"], totais["erros"],
            totais["descartados"], totais["antigos"], time.time() - t0,
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
    logger.info("  total acumulado: +%d novos, %d duplicados, %d erros, %d descartados",
                acumulado["salvos"], acumulado["duplicados"], acumulado["erros"],
                acumulado["descartados"])
    logger.info("  %s", resumo_da_base())
    logger.info("=" * 68)


if __name__ == "__main__":
    main()
