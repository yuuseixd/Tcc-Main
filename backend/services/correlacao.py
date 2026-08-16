"""Correlação entre o sentimento social e o movimento do preço.

Esta era a maior duplicação do projeto: a rota ``/correlacao`` (JSON) e a rota
de relatório PDF calculavam as mesmas métricas em ~150 linhas quase idênticas,
com limiares levemente diferentes — ou seja, o gráfico e o PDF podiam discordar
sobre a mesma hora. Agora ambos consomem :func:`calcular_correlacao`.

Métricas
--------
**Sentiment Score** = (Positivos - Negativos) / Total, no intervalo [-1, +1].
Resume o humor da hora num único número.

**Return After Sentiment** = ((Preço_t+n - Preço_t) / Preço_t) x 100.
Mede o retorno da moeda nas janelas seguintes ao sentimento observado, que é
o que permite discutir se o sentimento antecede o preço.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from config import (
    LIMIAR_SENTIMENT_SCORE,
    LIMIAR_VARIACAO_PRECO_PCT,
)
from models import SocialPost
from services import mercado
from services.sentimento import (
    NEGATIVO,
    NULO,
    POSITIVO,
    sentimento_para_indice,
)
from utils.tempo import truncar_hora

logger = logging.getLogger("sentcrypto.correlacao")

# Janelas (em horas) usadas na métrica Return After Sentiment.
JANELAS_RETORNO = (1, 4, 24)

# Profundidade máxima da janela de preços consultada na Binance.
MAX_DIAS_HISTORICO = 180


@dataclass
class HoraAgregada:
    """Agregação dos posts de uma hora cheia."""

    timestamp: datetime
    positivos: int = 0
    negativos: int = 0
    neutros: int = 0
    indices: list[float] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.positivos + self.negativos + self.neutros

    @property
    def sentiment_score(self) -> float:
        """(Pos - Neg) / Total, em [-1, +1]."""
        if self.total == 0:
            return 0.0
        return round((self.positivos - self.negativos) / self.total, 4)

    @property
    def sentimento_medio(self) -> float:
        """Média do índice 0–1 — usado no eixo dos gráficos."""
        if not self.indices:
            return 0.5
        return round(sum(self.indices) / len(self.indices), 4)

    @property
    def direcao(self) -> str:
        """Direção do sentimento segundo o Sentiment Score."""
        if self.sentiment_score > LIMIAR_SENTIMENT_SCORE:
            return POSITIVO
        if self.sentiment_score < -LIMIAR_SENTIMENT_SCORE:
            return NEGATIVO
        return "neutro"


def agregar_posts_por_hora(posts: list[SocialPost]) -> dict[str, HoraAgregada]:
    """Agrupa posts em horas cheias, ignorando os marcados como ``nulo``."""
    agrupado: dict[str, HoraAgregada] = {}

    for p in posts:
        if p.sentimento == NULO or not p.timestamp_post:
            continue

        hora = truncar_hora(p.timestamp_post)
        chave = hora.isoformat()

        grupo = agrupado.get(chave)
        if grupo is None:
            grupo = HoraAgregada(timestamp=hora)
            agrupado[chave] = grupo

        grupo.indices.append(sentimento_para_indice(p.sentimento))
        if p.sentimento == POSITIVO:
            grupo.positivos += 1
        elif p.sentimento == NEGATIVO:
            grupo.negativos += 1
        else:
            grupo.neutros += 1

    return agrupado


def precos_para_horas(moeda: str, horas_iso: list[str]) -> dict[str, dict]:
    """Busca na Binance os preços das horas informadas.

    A janela consultada acompanha o período dos dados em vez de assumir "as
    últimas N horas", que só funcionava quando a análise era feita no mesmo dia
    da coleta. Devolve ``{hora_iso: {preco_abertura, preco_fechamento,
    variacao_pct}}``; horas sem candle simplesmente não aparecem.
    """
    if not horas_iso:
        return {}

    horas = sorted(horas_iso)
    inicio = datetime.fromisoformat(horas[0])
    fim = datetime.fromisoformat(horas[-1]) + timedelta(
        hours=max(JANELAS_RETORNO) + 1
    )

    limite_inferior = fim - timedelta(days=MAX_DIAS_HISTORICO)
    if inicio < limite_inferior:
        logger.info(
            "Janela de preços limitada a %d dias (posts mais antigos "
            "ficam sem preço).", MAX_DIAS_HISTORICO,
        )
        inicio = limite_inferior

    try:
        candles = mercado.buscar_klines_intervalo(
            mercado.simbolo_para(moeda), inicio=inicio, fim=fim, intervalo="1h"
        )
    except mercado.ErroBinance as e:
        logger.warning("Sem dados de preço para %s: %s", moeda, e)
        return {}

    return mercado.mapa_precos_por_hora(candles)


def _direcao_preco(variacao_pct: float | None) -> str | None:
    if variacao_pct is None:
        return None
    if variacao_pct > LIMIAR_VARIACAO_PRECO_PCT:
        return "subiu"
    if variacao_pct < -LIMIAR_VARIACAO_PRECO_PCT:
        return "desceu"
    return "estavel"


def calcular_correlacao(db: Session, moeda: str, fonte: str) -> dict:
    """Calcula todas as métricas de correlação para uma moeda/fonte.

    Devolve um dicionário com ``pontos`` (uma entrada por hora com posts) e
    ``resumo`` (contagens e taxa de acerto). É a única fonte de verdade para
    o gráfico, a tabela e o relatório PDF.
    """
    moeda_u = moeda.upper()

    posts = (
        db.query(SocialPost)
        .filter(
            SocialPost.moeda == moeda_u,
            SocialPost.fonte == fonte,
            SocialPost.sentimento != NULO,
        )
        .order_by(SocialPost.timestamp_post.asc())
        .all()
    )

    if not posts:
        return {
            "moeda": moeda_u,
            "fonte": fonte,
            "pontos": [],
            "resumo": _resumo_vazio(),
        }

    agrupado = agregar_posts_por_hora(posts)
    if not agrupado:
        return {
            "moeda": moeda_u,
            "fonte": fonte,
            "pontos": [],
            "resumo": _resumo_vazio(),
        }

    # ── Preços ──────────────────────────────────────────────────────────
    precos = precos_para_horas(moeda_u, list(agrupado.keys()))

    # Lista ordenada de horas + índice reverso: evita o list.index() dentro
    # do laço, que tornava o cálculo quadrático.
    horas_ordenadas = sorted(precos.keys())
    posicao_da_hora = {h: i for i, h in enumerate(horas_ordenadas)}

    # ── Métricas por hora ───────────────────────────────────────────────
    pontos = []
    acertos = 0
    erros = 0
    total_comparavel = 0

    for chave in sorted(agrupado.keys()):
        grupo = agrupado[chave]
        preco_info = precos.get(chave, {})

        variacao_1h = preco_info.get("variacao_pct")
        preco_abertura = preco_info.get("preco_abertura")
        preco_fechamento = preco_info.get("preco_fechamento")

        retornos = _calcular_retornos(
            chave, preco_abertura, precos, horas_ordenadas, posicao_da_hora
        )

        direcao_sent = grupo.direcao
        direcao_preco = _direcao_preco(variacao_1h)
        acertou = None

        # Só é possível avaliar acerto quando ambos os lados têm direção
        # definida — sentimento neutro ou preço estável não são previsões.
        if direcao_sent != "neutro" and direcao_preco not in (None, "estavel"):
            total_comparavel += 1
            acertou = (
                (direcao_sent == POSITIVO and direcao_preco == "subiu")
                or (direcao_sent == NEGATIVO and direcao_preco == "desceu")
            )
            if acertou:
                acertos += 1
            else:
                erros += 1

        pontos.append(
            {
                "hora": grupo.timestamp.strftime("%d/%m %H:%M"),
                "timestamp": chave,
                "positivos": grupo.positivos,
                "negativos": grupo.negativos,
                "neutros": grupo.neutros,
                "total": grupo.total,
                "sentiment_score": grupo.sentiment_score,
                "sentimento_medio": grupo.sentimento_medio,
                "sentimento_direcao": direcao_sent,
                "preco_abertura": preco_abertura,
                "preco_fechamento": preco_fechamento,
                "variacao_preco": variacao_1h,
                "preco_direcao": direcao_preco,
                "retorno_1h": retornos.get(1),
                "retorno_4h": retornos.get(4),
                "retorno_24h": retornos.get(24),
                "acertou": acertou,
            }
        )

    scores = [p["sentiment_score"] for p in pontos]
    retornos_pos = [
        p["retorno_1h"]
        for p in pontos
        if p["sentimento_direcao"] == POSITIVO and p["retorno_1h"] is not None
    ]
    retornos_neg = [
        p["retorno_1h"]
        for p in pontos
        if p["sentimento_direcao"] == NEGATIVO and p["retorno_1h"] is not None
    ]

    resumo = {
        "total_horas_analisadas": len(pontos),
        "total_posts": sum(p["total"] for p in pontos),
        "total_comparavel": total_comparavel,
        "acertos": acertos,
        "erros": erros,
        "taxa_acerto_pct": (
            round(acertos / total_comparavel * 100, 1) if total_comparavel else None
        ),
        "score_medio": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "retorno_medio_apos_positivo": (
            round(sum(retornos_pos) / len(retornos_pos), 4) if retornos_pos else None
        ),
        "retorno_medio_apos_negativo": (
            round(sum(retornos_neg) / len(retornos_neg), 4) if retornos_neg else None
        ),
        # Amostra pequena não sustenta conclusão estatística; o dashboard usa
        # este sinal para exibir a ressalva ao lado da taxa de acerto.
        "amostra_suficiente": total_comparavel >= 30,
    }

    return {"moeda": moeda_u, "fonte": fonte, "pontos": pontos, "resumo": resumo}


def _calcular_retornos(
    chave: str,
    preco_base: float | None,
    precos: dict[str, dict],
    horas_ordenadas: list[str],
    posicao_da_hora: dict[str, int],
) -> dict[int, float | None]:
    """Return After Sentiment nas janelas configuradas."""
    retornos: dict[int, float | None] = {j: None for j in JANELAS_RETORNO}

    idx = posicao_da_hora.get(chave)
    if idx is None or not preco_base:
        return retornos

    for janela in JANELAS_RETORNO:
        alvo = idx + janela
        if alvo < len(horas_ordenadas):
            preco_futuro = precos[horas_ordenadas[alvo]]["preco_fechamento"]
            retornos[janela] = round(
                (preco_futuro - preco_base) / preco_base * 100, 4
            )

    return retornos


def _resumo_vazio() -> dict:
    return {
        "total_horas_analisadas": 0,
        "total_posts": 0,
        "total_comparavel": 0,
        "acertos": 0,
        "erros": 0,
        "taxa_acerto_pct": None,
        "score_medio": 0.0,
        "retorno_medio_apos_positivo": None,
        "retorno_medio_apos_negativo": None,
        "amostra_suficiente": False,
    }
