"""Testes da contagem de posts descartados na triagem e do rodízio de perfis.

Executar (a partir da pasta backend/):

    python -m pytest tests/test_descartados.py -v
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from collectors import x_collector
from db import Base
from models import PostDescartado, SocialPost
from services import posts as svc_posts
from tools import coletor_continuo

BASE = datetime(2026, 3, 3, 12, 0, 0)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def tweet(i: int, texto: str = "almoço de domingo", hora: datetime = BASE) -> dict:
    return {"tweet_id": str(i), "texto": texto, "perfil": "@alguem",
            "timestamp_post": hora}


def salvo(i: int, sentimento: str, hora: datetime = BASE) -> SocialPost:
    return SocialPost(moeda="BTC", fonte="X", external_id=str(i), texto="bitcoin",
                      sentimento=sentimento, timestamp_post=hora)


class TestRegistrarDescartados:
    def test_conta_cada_post_uma_vez(self, db):
        assert svc_posts.registrar_descartados(db, "btc", "X", [tweet(1), tweet(2)]) == 2
        # A rodada seguinte relê os mesmos tweets: só o novo conta.
        assert svc_posts.registrar_descartados(db, "BTC", "X", [tweet(1), tweet(3)]) == 1
        assert db.query(PostDescartado).count() == 3

    def test_repetido_no_mesmo_lote(self, db):
        assert svc_posts.registrar_descartados(db, "BTC", "X", [tweet(1), tweet(1)]) == 1

    def test_ignora_sem_id_ou_sem_data(self, db):
        sem_id = {"texto": "x", "timestamp_post": BASE}
        sem_data = {"tweet_id": "9", "texto": "x"}
        assert svc_posts.registrar_descartados(db, "BTC", "X", [sem_id, sem_data]) == 0

    def test_moedas_contadas_separadamente(self, db):
        svc_posts.registrar_descartados(db, "BTC", "X", [tweet(1)])
        assert svc_posts.registrar_descartados(db, "ETH", "X", [tweet(1)]) == 1

    def test_lista_vazia(self, db):
        assert svc_posts.registrar_descartados(db, "BTC", "X", []) == 0


class TestVolumeAnalisado:
    def test_soma_salvos_e_descartados(self, db):
        db.add_all([salvo(1, "positivo"), salvo(2, "negativo"), salvo(3, "nulo")])
        db.commit()
        svc_posts.registrar_descartados(db, "BTC", "X", [tweet(10), tweet(11)])

        v = svc_posts.volume_analisado(db, "BTC", "X")
        assert v["analisados"] == 5
        assert v["relevantes"] == 2
        assert v["nao_cripto"] == 1
        assert v["sem_a_moeda"] == 2
        assert v["descartados"] == 3
        assert v["contagem_desde"].endswith("Z")

    def test_respeita_periodo_e_fonte(self, db):
        depois = BASE + timedelta(days=2)
        db.add_all([salvo(1, "positivo"), salvo(2, "positivo", depois)])
        db.commit()
        svc_posts.registrar_descartados(db, "BTC", "X", [tweet(10), tweet(11, hora=depois)])
        svc_posts.registrar_descartados(db, "BTC", "Reddit", [tweet(12)])

        v = svc_posts.volume_analisado(db, "BTC", "X", inicio=depois - timedelta(hours=1))
        assert v["analisados"] == 2
        assert v["relevantes"] == 1

    def test_base_vazia(self, db):
        v = svc_posts.volume_analisado(db, "BTC", "X")
        assert v["analisados"] == 0
        assert v["contagem_desde"] is None


class TestColetorX:
    def test_separa_descartados(self, monkeypatch):
        feed = [tweet(1, "Bitcoin a 100k"), tweet(2), tweet(3, "comprei $BTC")]
        monkeypatch.setattr(x_collector, "coletar_feed_x", lambda *a, **k: feed)
        descartados = []
        relevantes = x_collector.coletar_tweets_x(["a"], "BTC", descartados=descartados)
        assert [t["tweet_id"] for t in relevantes] == ["1", "3"]
        assert [t["tweet_id"] for t in descartados] == ["2"]

    def test_sem_lista_continua_funcionando(self, monkeypatch):
        monkeypatch.setattr(x_collector, "coletar_feed_x", lambda *a, **k: [tweet(2)])
        assert x_collector.coletar_tweets_x(["a"], "BTC") == []


class TestRodizioDePerfis:
    PERFIS = ["a", "b", "c", "d", "e"]

    def rodar(self, estado, tamanho=2, adiados=()):
        lote, avanco = coletor_continuo.montar_lote(self.PERFIS, estado, tamanho)
        novo = {"proximo": (estado["proximo"] + avanco) % len(self.PERFIS),
                "pendentes": list(adiados)}
        return lote, novo

    def test_cobre_todos_em_rodadas(self):
        estado, lotes = {"proximo": 0, "pendentes": []}, []
        for _ in range(4):
            lote, estado = self.rodar(estado)
            lotes.append(lote)
        assert lotes == [["a", "b"], ["c", "d"], ["e", "a"], ["b", "c"]]

    def test_adiados_entram_primeiro_sem_perder_o_rodizio(self):
        lote, estado = self.rodar({"proximo": 0, "pendentes": []}, adiados=["b"])
        assert lote == ["a", "b"]
        lote, estado = self.rodar(estado)
        assert lote == ["b", "c"]  # b adiado volta antes; rodízio segue em c
        lote, _ = self.rodar(estado)
        assert lote == ["d", "e"]

    def test_adiado_que_saiu_da_lista_e_ignorado(self):
        lote, _ = self.rodar({"proximo": 0, "pendentes": ["sumiu", "C"]})
        assert lote == ["c", "a"]

    def test_lote_zero_ou_maior_usa_todos(self):
        vazio = {"proximo": 3, "pendentes": []}
        assert coletor_continuo.montar_lote(self.PERFIS, vazio, 0) == (self.PERFIS, 0)
        assert coletor_continuo.montar_lote(self.PERFIS, vazio, 10) == (self.PERFIS, 0)

    def test_estado_sobrevive_a_reinicio(self, tmp_path):
        arq = tmp_path / "estado.json"
        assert coletor_continuo.carregar_estado(arq) == {"proximo": 0, "pendentes": []}
        coletor_continuo.salvar_estado({"proximo": 7, "pendentes": ["x"]}, arq)
        assert coletor_continuo.carregar_estado(arq) == {"proximo": 7, "pendentes": ["x"]}

    def test_carregar_perfis_do_arquivo(self, tmp_path):
        arq = tmp_path / "perfis.txt"
        arq.write_text("# comentário\n@saylor\nglassnode  # on-chain\n\nSaylor\n",
                       encoding="utf-8")
        assert coletor_continuo.carregar_perfis(arq) == ["saylor", "glassnode"]

    def test_sem_arquivo_usa_padrao(self, tmp_path):
        assert coletor_continuo.carregar_perfis(tmp_path / "nao_existe.txt") == \
            coletor_continuo.PERFIS_PADRAO


class TestPrioritarios:
    PERFIS = ["a", "b", "c", "d", "e", "f"]
    VAZIO = {"proximo": 0, "pendentes": []}

    def test_marcados_com_asterisco(self, tmp_path):
        arq = tmp_path / "perfis.txt"
        arq.write_text("*@whale  # muito ativo\nsaylor\n* glassnode\n*whale\n", encoding="utf-8")
        assert coletor_continuo.carregar_perfis(arq) == ["whale", "saylor", "glassnode"]
        assert coletor_continuo.carregar_prioritarios(arq) == ["whale", "glassnode"]

    def test_prioritarios_em_toda_rodada_sem_aumentar_o_lote(self):
        estado, lotes = dict(self.VAZIO), []
        for _ in range(3):
            lote, avanco, n = coletor_continuo.lote_da_rodada(self.PERFIS, ["e"], estado, 3)
            lotes.append(lote)
            estado = {"proximo": (estado["proximo"] + avanco) % n, "pendentes": []}
        # "e" sempre entra; os outros 5 giram de 2 em 2.
        assert lotes == [["e", "a", "b"], ["e", "c", "d"], ["e", "f", "a"]]
        assert all(len(lote) == 3 for lote in lotes)

    def test_sempre_sobra_vaga_para_o_rodizio(self):
        lote, _, n = coletor_continuo.lote_da_rodada(self.PERFIS, ["a", "b", "c"], self.VAZIO, 3)
        # Só 2 dos 3 viram fixos; "c" volta para o rodízio (e é o primeiro dele).
        assert lote == ["a", "b", "c"] and n == 4

    def test_prioritario_fora_da_lista_e_ignorado(self):
        lote, _, n = coletor_continuo.lote_da_rodada(self.PERFIS, ["zzz"], self.VAZIO, 2)
        assert lote == ["a", "b"] and n == 6

    def test_lote_que_cobre_todos(self):
        assert coletor_continuo.lote_da_rodada(self.PERFIS, ["e"], self.VAZIO, 0)[0] == self.PERFIS


class TestIdadeMaxima:
    AGORA = BASE + timedelta(days=100)

    def test_tira_posts_mais_antigos_que_o_limite(self):
        brutos = [tweet(1, hora=self.AGORA - timedelta(days=59)),
                  tweet(2, hora=self.AGORA - timedelta(days=61)),
                  tweet(3, hora=self.AGORA - timedelta(days=900))]
        recentes, antigos = svc_posts.separar_antigos(brutos, 60, self.AGORA)
        assert [t["tweet_id"] for t in recentes] == ["1"]
        assert antigos == 2

    def test_post_sem_data_fica(self):
        recentes, antigos = svc_posts.separar_antigos([{"tweet_id": "9"}], 60, self.AGORA)
        assert len(recentes) == 1 and antigos == 0

    def test_zero_desliga(self):
        brutos = [tweet(1, hora=self.AGORA - timedelta(days=900))]
        assert svc_posts.separar_antigos(brutos, 0, self.AGORA) == (brutos, 0)

    def test_aceita_data_com_fuso(self):
        from datetime import timezone
        hora = (self.AGORA - timedelta(days=1)).replace(tzinfo=timezone.utc)
        recentes, _ = svc_posts.separar_antigos([tweet(1, hora=hora)], 60, self.AGORA)
        assert len(recentes) == 1

    def test_mensagem_da_coleta(self):
        r = svc_posts.ResultadoColeta(recebidos=5, salvos=3, antigos=2)
        assert "2 com mais de" in r.mensagem and r.como_dict()["antigos"] == 2
