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
from services import estatistica, mercado
from services.sentimento import (
    NEGATIVO,
    NULO,
    POSITIVO,
    sentimento_para_indice,
)
from utils.tempo import agora_utc, truncar_hora

logger = logging.getLogger("sentcrypto.correlacao")

# Janelas (em horas) usadas na métrica Return After Sentiment.
JANELAS_RETORNO = (1, 4, 24)

# Horizontes da análise preditiva. O 0 é o candle da própria hora dos posts:
# mede reação, não antecipação, porque os posts e o movimento acontecem juntos.
# Os demais vão do FECHAMENTO da hora dos posts até N horas depois — o retorno
# que alguém conseguiria capturar operando no sinal assim que ele existe.
HORIZONTES_PREDITIVOS = (0, 1, 4, 24)

# Horas com posts separadas por mais que isto viram trechos de consulta
# independentes na Binance — evita baixar meses de candles entre dois períodos
# de coleta distantes.
INTERVALO_MAX_TRECHO = timedelta(days=7)

# Teto de trechos consultados por análise (posts antigos isolados, como os que
# o modo público do X devolve, viram um trecho cada).
MAX_TRECHOS_PRECO = 40

# Cache dos candles de trechos já encerrados: (símbolo, início, fim) -> mapa.
_CACHE_TRECHOS: dict[tuple, dict[str, dict]] = {}
MAX_CACHE_TRECHOS = 500


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

    As horas são agrupadas em trechos contíguos e cada trecho é consultado à
    parte. Antes a janela era "do post mais recente para trás, até 180 dias":
    bastava uma coleta nova para todo o período anterior da pesquisa ficar sem
    preço. Devolve ``{hora_iso: {preco_abertura, preco_fechamento,
    variacao_pct}}``; horas sem candle simplesmente não aparecem.
    """
    if not horas_iso:
        return {}

    folga = timedelta(hours=max(JANELAS_RETORNO) + 1)
    trechos = _agrupar_em_trechos(
        sorted(datetime.fromisoformat(h) for h in horas_iso)
    )
    if len(trechos) > MAX_TRECHOS_PRECO:
        logger.info(
            "%d trechos de horas; consultando só os %d mais recentes "
            "(os demais ficam sem preço).", len(trechos), MAX_TRECHOS_PRECO,
        )
        trechos = trechos[-MAX_TRECHOS_PRECO:]

    simbolo = mercado.simbolo_para(moeda)
    agora = agora_utc()
    precos: dict[str, dict] = {}
    for inicio, fim in trechos:
        # Uma hora antes do trecho: é o candle que a baseline "momentum" usa
        # para a primeira hora com posts.
        chave = (simbolo, inicio - timedelta(hours=1), fim + folga)
        if chave in _CACHE_TRECHOS:
            precos.update(_CACHE_TRECHOS[chave])
            continue
        try:
            candles = mercado.buscar_klines_intervalo(
                simbolo, inicio=chave[1], fim=chave[2], intervalo="1h"
            )
        except mercado.ErroBinance as e:
            logger.warning("Sem dados de preço para %s: %s", moeda, e)
            continue
        mapa = mercado.mapa_precos_por_hora(candles)
        # Candles de um trecho que já terminou nunca mudam; o trecho atual
        # ainda ganha candles novos e é sempre consultado de novo.
        if chave[2] + timedelta(hours=1) < agora:
            if len(_CACHE_TRECHOS) >= MAX_CACHE_TRECHOS:
                _CACHE_TRECHOS.pop(next(iter(_CACHE_TRECHOS)))
            _CACHE_TRECHOS[chave] = mapa
        precos.update(mapa)

    return precos


def limpar_cache() -> None:
    _CACHE_TRECHOS.clear()


def _agrupar_em_trechos(horas: list[datetime]) -> list[tuple[datetime, datetime]]:
    """Agrupa horas ordenadas em trechos (início, fim) sem lacunas longas."""
    trechos: list[tuple[datetime, datetime]] = []
    for hora in horas:
        if trechos and hora - trechos[-1][1] <= INTERVALO_MAX_TRECHO:
            trechos[-1] = (trechos[-1][0], hora)
        else:
            trechos.append((hora, hora))
    return trechos


def _direcao_preco(variacao_pct: float | None) -> str | None:
    if variacao_pct is None:
        return None
    if variacao_pct > LIMIAR_VARIACAO_PRECO_PCT:
        return "subiu"
    if variacao_pct < -LIMIAR_VARIACAO_PRECO_PCT:
        return "desceu"
    return "estavel"


def calcular_correlacao(
    db: Session,
    moeda: str,
    fonte: str,
    inicio: datetime | None = None,
    fim: datetime | None = None,
) -> dict:
    """Calcula todas as métricas de correlação para uma moeda/fonte.

    ``inicio``/``fim`` (opcionais, UTC) limitam os posts considerados ao
    período escolhido; sem eles a análise cobre todo o banco.

    Devolve um dicionário com ``pontos`` (uma entrada por hora com posts) e
    ``resumo`` (contagens e taxa de acerto). É a única fonte de verdade para
    o gráfico, a tabela e o relatório PDF.
    """
    moeda_u = moeda.upper()
    vazio = {
        "moeda": moeda_u,
        "fonte": fonte,
        "inicio": inicio.isoformat() if inicio else None,
        "fim": fim.isoformat() if fim else None,
        "pontos": [],
        "resumo": _resumo_vazio(),
        "analise_preditiva": [],
        "conclusao": estatistica.conclusao([]),
    }

    consulta = db.query(SocialPost).filter(
        SocialPost.moeda == moeda_u,
        SocialPost.fonte == fonte,
        SocialPost.sentimento != NULO,
    )
    if inicio:
        consulta = consulta.filter(SocialPost.timestamp_post >= inicio)
    if fim:
        consulta = consulta.filter(SocialPost.timestamp_post <= fim)
    posts = consulta.order_by(SocialPost.timestamp_post.asc()).all()

    if not posts:
        return vazio

    agrupado = agregar_posts_por_hora(posts)
    if not agrupado:
        return vazio

    # ── Preços ──────────────────────────────────────────────────────────
    precos = precos_para_horas(moeda_u, list(agrupado.keys()))

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

        retornos = _calcular_retornos(chave, preco_abertura, precos)

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

    preditiva = analise_preditiva(agrupado, precos)
    # A taxa de acerto do resumo é a do horizonte 0; os testes vêm junto para
    # o número nunca aparecer sem o p-valor e o intervalo de confiança.
    mesma_hora = preditiva[0]
    resumo.update(
        {
            "p_valor": mesma_hora["p_valor"],
            "ic95_pct": mesma_hora["ic95_pct"],
            "significativo": mesma_hora["significativo"],
            "baseline_sempre_sobe_pct": mesma_hora["baseline_sempre_sobe_pct"],
            "baseline_momentum_pct": mesma_hora["baseline_momentum_pct"],
        }
    )

    return {
        **vazio,
        "pontos": pontos,
        "resumo": resumo,
        "analise_preditiva": preditiva,
        "conclusao": estatistica.conclusao(preditiva),
    }


def analise_preditiva(
    agrupado: dict[str, HoraAgregada],
    precos: dict[str, dict],
) -> list[dict]:
    """Taxa de acerto, testes e baselines para cada horizonte preditivo."""
    resultado = []
    for horizonte in HORIZONTES_PREDITIVOS:
        observacoes = []
        for chave, grupo in agrupado.items():
            atual = precos.get(chave)
            if not atual:
                continue
            hora = datetime.fromisoformat(chave)

            if horizonte == 0:
                retorno = atual["variacao_pct"]
                anterior = precos.get((hora - timedelta(hours=1)).isoformat())
                momentum = anterior["variacao_pct"] if anterior else None
            else:
                futuro = precos.get((hora + timedelta(hours=horizonte)).isoformat())
                if not futuro or not atual["preco_fechamento"]:
                    continue
                base = atual["preco_fechamento"]
                retorno = (futuro["preco_fechamento"] - base) / base * 100
                # O último candle fechado quando o sinal fica pronto é o da
                # própria hora dos posts.
                momentum = atual["variacao_pct"]

            observacoes.append(
                {
                    "score": grupo.sentiment_score,
                    "direcao": {POSITIVO: 1, NEGATIVO: -1}.get(grupo.direcao, 0),
                    "retorno": retorno,
                    "momentum": momentum,
                }
            )

        avaliacao = estatistica.avaliar_horizonte(
            observacoes, LIMIAR_VARIACAO_PRECO_PCT
        )
        avaliacao["horizonte_h"] = horizonte
        avaliacao["rotulo"] = "mesma hora" if horizonte == 0 else f"+{horizonte}h"
        resultado.append(avaliacao)
    return resultado


def _calcular_retornos(
    chave: str,
    preco_base: float | None,
    precos: dict[str, dict],
) -> dict[int, float | None]:
    """Return After Sentiment nas janelas configuradas.

    O preço futuro é buscado pela hora exata (``t + janela``), não pela
    posição na lista de candles: com os preços vindo em trechos separados,
    "N posições à frente" poderia cair num trecho de outra semana.
    """
    retornos: dict[int, float | None] = {j: None for j in JANELAS_RETORNO}
    if not preco_base:
        return retornos

    hora = datetime.fromisoformat(chave)
    for janela in JANELAS_RETORNO:
        futuro = precos.get((hora + timedelta(hours=janela)).isoformat())
        if futuro:
            retornos[janela] = round(
                (futuro["preco_fechamento"] - preco_base) / preco_base * 100, 4
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
        "p_valor": None,
        "ic95_pct": None,
        "significativo": False,
        "baseline_sempre_sobe_pct": None,
        "baseline_momentum_pct": None,
    }
