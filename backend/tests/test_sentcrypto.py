"""Testes automatizados do SentCrypto.

Cobrem as regras que sustentam os resultados do trabalho: deduplicação,
normalização de tempo, filtro de relevância e cálculo das métricas.

Executar (a partir da pasta backend/):

    python -m pytest tests/ -v
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Sobe o BERT desligado: os testes validam regras, não a inferência do modelo.
os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import Base
from models import MarketPoint, SocialPost
from services import correlacao as svc_correlacao
from services import posts as svc_posts
from services import sentimento as svc_sentimento
from utils import moedas as svc_moedas
from utils import tempo


# ── Infraestrutura ──────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Banco SQLite em memória, isolado por teste."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


@pytest.fixture(autouse=True)
def bert_falso(monkeypatch):
    """Substitui o BERT por uma regra determinística.

    Palavras positivas -> positivo; negativas -> negativo; senão neutro.
    Assim os testes verificam o encanamento sem depender do modelo real.
    """
    def classificar_fake(texto: str) -> dict:
        if not (texto or "").strip():
            raise ValueError("Texto vazio.")
        if not svc_sentimento.texto_e_crypto_relevante(texto):
            return {"sentimento": svc_sentimento.NULO, "indice": None,
                    "score": None, "label_bert": None}
        baixo = texto.lower()
        if any(p in baixo for p in ("subindo", "alta", "ótimo", "otimo")):
            s = svc_sentimento.POSITIVO
        elif any(p in baixo for p in ("caindo", "queda", "péssimo", "pessimo")):
            s = svc_sentimento.NEGATIVO
        else:
            s = svc_sentimento.NEUTRO
        return {"sentimento": s, "indice": svc_sentimento.sentimento_para_indice(s),
                "score": 0.9, "label_bert": "fake"}

    monkeypatch.setattr(svc_sentimento, "classificar", classificar_fake)


def post_bruto(external_id: str, texto: str, hora: datetime) -> dict:
    return {
        "texto": texto,
        "timestamp_post": hora,
        "external_id": external_id,
        "url": f"https://exemplo/{external_id}",
        "autor": "@teste",
    }


BASE = datetime(2026, 3, 3, 14, 0, 0)


# ── Deduplicação ────────────────────────────────────────────────────────────


class TestDeduplicacao:
    """O bug mais grave encontrado: 71% dos posts do banco eram duplicatas."""

    def test_coletar_duas_vezes_nao_duplica(self, db):
        lote = [
            post_bruto("t3_a", "bitcoin subindo forte", BASE),
            post_bruto("t3_b", "bitcoin caindo muito", BASE),
        ]

        primeira = svc_posts.salvar_posts(db, "BTC", "Reddit", lote)
        assert primeira.salvos == 2
        assert primeira.duplicados == 0

        segunda = svc_posts.salvar_posts(db, "BTC", "Reddit", lote)
        assert segunda.salvos == 0
        assert segunda.duplicados == 2

        assert db.query(SocialPost).count() == 2

    def test_duplicata_dentro_do_mesmo_lote(self, db):
        """O mesmo tweet pode chegar por dois perfis na mesma coleta."""
        lote = [
            post_bruto("t1", "bitcoin subindo", BASE),
            post_bruto("t1", "bitcoin subindo", BASE),
        ]
        r = svc_posts.salvar_posts(db, "BTC", "X", lote)
        assert r.salvos == 1
        assert r.duplicados == 1

    def test_mesmo_id_em_fontes_diferentes_coexiste(self, db):
        """IDs só são únicos dentro da própria fonte."""
        svc_posts.salvar_posts(db, "BTC", "X", [post_bruto("1", "bitcoin alta", BASE)])
        svc_posts.salvar_posts(db, "BTC", "Reddit", [post_bruto("1", "bitcoin alta", BASE)])
        assert db.query(SocialPost).count() == 2

    def test_posts_sem_id_nao_sao_confundidos(self, db):
        """Sem external_id não há como deduplicar — ambos devem ser salvos."""
        lote = [
            {"texto": "bitcoin subindo", "timestamp_post": BASE},
            {"texto": "bitcoin caindo", "timestamp_post": BASE},
        ]
        r = svc_posts.salvar_posts(db, "BTC", "X", lote)
        assert r.salvos == 2


# ── Normalização de tempo ───────────────────────────────────────────────────


class TestTempo:
    """O SQLite descarta tzinfo; a normalização evita comparações erradas."""

    def test_aware_vira_naive_em_utc(self):
        aware = datetime(2026, 3, 3, 14, 0, tzinfo=timezone(timedelta(hours=-3)))
        naive = tempo.para_utc_naive(aware)
        assert naive.tzinfo is None
        assert naive == datetime(2026, 3, 3, 17, 0)   # -03:00 -> UTC

    def test_naive_permanece_intacto(self):
        d = datetime(2026, 3, 3, 14, 0)
        assert tempo.para_utc_naive(d) == d

    def test_saida_marca_utc_explicitamente(self):
        """Sem o sufixo, o navegador leria a data como horário local."""
        assert tempo.para_iso_utc(datetime(2026, 3, 3, 14, 0)).endswith("+00:00")

    def test_data_fim_cobre_o_dia_inteiro(self):
        fim = tempo.parse_data_fim("2026-03-03")
        assert fim.hour == 23 and fim.minute == 59

    def test_data_com_hora_nao_e_esticada(self):
        assert tempo.parse_data_fim("2026-03-03T10:00:00").hour == 10

    def test_offset_que_virou_espaco_na_url(self):
        """Em query string, '+' chega como espaço; a data continua válida."""
        assert tempo._parse_iso("2026-03-03T14:00:00 00:00") == datetime(2026, 3, 3, 14, 0)

    def test_texto_invalido_retorna_none(self):
        assert tempo.parse_data_inicio("abacaxi") is None
        assert tempo.parse_data_inicio("") is None


# ── Filtro de relevância ────────────────────────────────────────────────────


class TestRelevancia:
    """A busca por substring classificava textos aleatórios como cripto."""

    @pytest.mark.parametrize("texto", [
        "Bitcoin está subindo muito hoje",
        "comprei ETH ontem",
        "staking de solana rendendo bem",
        "gas fee está caro demais",
    ])
    def test_reconhece_texto_de_cripto(self, texto):
        assert svc_sentimento.texto_e_crypto_relevante(texto) is True

    @pytest.mark.parametrize("texto", [
        "atualizei meu perfil no LinkedIn",       # continha "link"
        "fui pra Las Vegas no fim de semana",     # continha "gas"
        "meu time ganhou de 3 a 0",
        "o mercado de ações caiu hoje",
    ])
    def test_rejeita_texto_fora_do_dominio(self, texto):
        assert svc_sentimento.texto_e_crypto_relevante(texto) is False

    def test_texto_vazio(self):
        assert svc_sentimento.texto_e_crypto_relevante("") is False


class TestMencaoMoeda:
    def test_ticker_e_nome_por_extenso(self):
        assert svc_moedas.texto_menciona_moeda("comprei BTC", "BTC")
        assert svc_moedas.texto_menciona_moeda("Bitcoin subiu", "BTC")
        assert svc_moedas.texto_menciona_moeda("olha o $BTC", "BTC")

    def test_nao_casa_dentro_de_outra_palavra(self):
        """'ADA' dentro de 'CANADA' inflava a base com posts irrelevantes."""
        assert not svc_moedas.texto_menciona_moeda("moro no CANADA", "ADA")
        assert not svc_moedas.texto_menciona_moeda("found a SOLUTION", "SOL")


# ── Mapeamento de labels ────────────────────────────────────────────────────


class TestMapeamentoLabels:
    @pytest.mark.parametrize("label,esperado", [
        ("1 star", "negativo"),
        ("2 stars", "negativo"),
        ("3 stars", "neutro"),
        ("4 stars", "positivo"),
        ("5 stars", "positivo"),
        ("POSITIVE", "positivo"),
        ("NEGATIVE", "negativo"),
        ("", "neutro"),
        (None, "neutro"),
    ])
    def test_traduz_label_do_modelo(self, label, esperado):
        assert svc_sentimento.mapear_estrela_para_sentimento(label) == esperado


# ── Métricas de correlação ──────────────────────────────────────────────────


class TestMetricas:
    def _agregado(self, pos: int, neg: int, neu: int) -> svc_correlacao.HoraAgregada:
        g = svc_correlacao.HoraAgregada(timestamp=BASE)
        g.positivos, g.negativos, g.neutros = pos, neg, neu
        g.indices = (
            [0.8] * pos + [0.2] * neg + [0.5] * neu
        )
        return g

    def test_sentiment_score_extremos(self):
        assert self._agregado(5, 0, 0).sentiment_score == 1.0
        assert self._agregado(0, 5, 0).sentiment_score == -1.0
        assert self._agregado(0, 0, 5).sentiment_score == 0.0

    def test_sentiment_score_misto(self):
        # (7 - 3) / 10 = 0.4
        assert self._agregado(7, 3, 0).sentiment_score == 0.4

    def test_hora_sem_posts_nao_divide_por_zero(self):
        assert svc_correlacao.HoraAgregada(timestamp=BASE).sentiment_score == 0.0

    def test_direcao_respeita_o_limiar(self):
        # Empate e diferenças pequenas ficam abaixo do limiar de 0.1.
        assert self._agregado(5, 5, 0).direcao == "neutro"    # score 0.00
        assert self._agregado(6, 5, 0).direcao == "neutro"    # score 0.09
        # A partir do limiar, a hora ganha direção.
        assert self._agregado(6, 4, 0).direcao == "positivo"  # score 0.20
        assert self._agregado(9, 1, 0).direcao == "positivo"  # score 0.80
        assert self._agregado(1, 9, 0).direcao == "negativo"  # score -0.80

    def test_agrupamento_por_hora_ignora_nulos(self, db):
        posts = [
            SocialPost(moeda="BTC", fonte="X", texto="a", sentimento="positivo",
                       timestamp_post=BASE),
            SocialPost(moeda="BTC", fonte="X", texto="b", sentimento="negativo",
                       timestamp_post=BASE + timedelta(minutes=30)),
            SocialPost(moeda="BTC", fonte="X", texto="c", sentimento="nulo",
                       timestamp_post=BASE + timedelta(minutes=45)),
        ]
        agrupado = svc_correlacao.agregar_posts_por_hora(posts)

        assert len(agrupado) == 1               # os 3 caem na mesma hora
        grupo = next(iter(agrupado.values()))
        assert grupo.total == 2                 # o "nulo" não conta
        assert grupo.positivos == 1 and grupo.negativos == 1

    def test_posts_de_horas_distintas_ficam_separados(self):
        posts = [
            SocialPost(moeda="BTC", fonte="X", texto="a", sentimento="positivo",
                       timestamp_post=BASE),
            SocialPost(moeda="BTC", fonte="X", texto="b", sentimento="positivo",
                       timestamp_post=BASE + timedelta(hours=1)),
        ]
        assert len(svc_correlacao.agregar_posts_por_hora(posts)) == 2


# ── Candles ─────────────────────────────────────────────────────────────────


class TestCandle:
    def _candle(self, abertura: float, fechamento: float):
        from services.mercado import Candle
        return Candle(timestamp=BASE, abertura=abertura, fechamento=fechamento)

    def test_alta_expressiva_e_positiva(self):
        assert self._candle(100, 101).sentimento == "positivo"   # +1%

    def test_queda_expressiva_e_negativa(self):
        assert self._candle(100, 99).sentimento == "negativo"    # -1%

    def test_variacao_pequena_e_neutra(self):
        assert self._candle(100, 100.1).sentimento == "neutro"   # +0,1%

    def test_abertura_zero_nao_quebra(self):
        """Divisão por zero derrubaria a rota inteira."""
        assert self._candle(0, 100).variacao == 0.0


# ── Deduplicação de candles ─────────────────────────────────────────────────


class TestCandlesNoBanco:
    def test_sincronizar_duas_vezes_nao_duplica(self, db):
        from services.mercado import Candle, salvar_candles

        candles = [
            Candle(timestamp=BASE, abertura=100, fechamento=101),
            Candle(timestamp=BASE + timedelta(hours=1), abertura=101, fechamento=102),
        ]

        assert salvar_candles(db, "BTC", candles) == 2
        assert salvar_candles(db, "BTC", candles) == 0
        assert db.query(MarketPoint).count() == 2

    def test_upsert_atualiza_preco(self, db):
        from services.mercado import Candle, salvar_candles

        salvar_candles(db, "BTC", [Candle(timestamp=BASE, abertura=100, fechamento=101)])
        salvar_candles(db, "BTC", [Candle(timestamp=BASE, abertura=100, fechamento=150)])

        assert db.query(MarketPoint).count() == 1
        assert db.query(MarketPoint).first().preco == 150.0
