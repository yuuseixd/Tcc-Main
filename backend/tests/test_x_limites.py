"""Testes da coleta do X sob limite de requisições (429) e do cache de IDs.

O cliente do twikit é substituído por um falso que conta as chamadas — nada
sai para a rede.

Executar (a partir da pasta backend/):

    python -m pytest tests/test_x_limites.py -v
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import twikit
from twikit.errors import TooManyRequests, UserNotFound

from collectors import x_collector


def tweet(i: int):
    return SimpleNamespace(
        id=i, text=f"bitcoin {i}", created_at="Thu Feb 26 16:45:47 +0000 2026",
        user=SimpleNamespace(name="Fulano", profile_image_url="http://a/b.png"),
        favorite_count=1, retweet_count=0, reply_count=0,
    )


class ClienteFalso:
    """Imita o ``twikit.Client``; ``erros`` é uma fila de exceções a levantar."""

    chamadas: list = []
    erros: list = []

    def __init__(self, *_a, **_k):
        pass

    def load_cookies(self, _caminho):
        pass

    @classmethod
    def _talvez_falhar(cls):
        if cls.erros:
            raise cls.erros.pop(0)

    async def get_user_by_screen_name(self, nome):
        ClienteFalso.chamadas.append(("perfil", nome))
        self._talvez_falhar()
        usuario = SimpleNamespace(id="42", name=nome)

        async def get_tweets(_tipo, count):
            ClienteFalso.chamadas.append(("tweets", "42"))
            return [tweet(1), tweet(2)][:count]

        usuario.get_tweets = get_tweets
        return usuario

    async def get_user_tweets(self, user_id, _tipo, count):
        ClienteFalso.chamadas.append(("tweets", user_id))
        self._talvez_falhar()
        return [tweet(1), tweet(2)][:count]


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    ClienteFalso.chamadas = []
    ClienteFalso.erros = []
    monkeypatch.setattr(twikit, "Client", ClienteFalso)
    monkeypatch.setattr(x_collector, "cookies_validos", lambda: True)
    monkeypatch.setattr(x_collector, "IDS_PATH", tmp_path / "ids.json")
    monkeypatch.setattr(x_collector, "_IDS", None)
    monkeypatch.setitem(x_collector._BLOQUEIO_429, "ate", 0.0)
    monkeypatch.setattr(x_collector, "X_PAUSA_ENTRE_PERFIS", 0)
    x_collector.limpar_cache()
    yield
    x_collector.limpar_cache()


def erro_429(reset_em_s: float):
    return TooManyRequests("429", headers={"x-rate-limit-reset": str(int(time.time() + reset_em_s))})


class TestCacheDeIds:
    def test_segunda_coleta_pula_consulta_do_perfil(self):
        x_collector._coletar_perfil_twikit("saylor", 2)
        x_collector._coletar_perfil_twikit("saylor", 2)
        assert ClienteFalso.chamadas == [
            ("perfil", "saylor"), ("tweets", "42"), ("tweets", "42"),
        ]

    def test_id_sobrevive_a_reinicio(self, monkeypatch):
        x_collector._coletar_perfil_twikit("saylor", 2)
        monkeypatch.setattr(x_collector, "_IDS", None)  # simula processo novo
        assert x_collector._id_do_perfil("SAYLOR") == "42"

    def test_id_invalido_volta_a_consultar_o_perfil(self):
        x_collector._guardar_id("saylor", "999")
        ClienteFalso.erros = [UserNotFound("sumiu")]
        tweets = x_collector._coletar_perfil_twikit("saylor", 2)
        assert len(tweets) == 2
        assert ClienteFalso.chamadas[1] == ("perfil", "saylor")
        assert x_collector._id_do_perfil("saylor") == "42"

    def test_nome_e_avatar_vem_do_tweet(self):
        t = x_collector._coletar_perfil_twikit("saylor", 1)[0]
        assert t["nome_exibicao"] == "Fulano"
        assert t["avatar"] == "http://a/b.png"
        assert t["perfil"] == "@saylor"


class TestLimite429:
    def test_reset_curto_espera_e_tenta_de_novo(self, monkeypatch):
        esperas = []

        async def dormir(s):
            esperas.append(s)

        monkeypatch.setattr(asyncio, "sleep", dormir)
        ClienteFalso.erros = [erro_429(5)]
        tweets = x_collector._coletar_perfil_twikit("saylor", 2)
        assert len(tweets) == 2
        assert esperas and esperas[0] <= x_collector.X_ESPERA_MAX_429

    def test_reset_longo_pausa_a_conta(self):
        ClienteFalso.erros = [erro_429(600)]
        with pytest.raises(RuntimeError, match="429"):
            x_collector._coletar_perfil_twikit("saylor", 2)
        assert x_collector._BLOQUEIO_429["ate"] > time.time() + 500

        # Os próximos perfis nem tentam o twikit enquanto durar a pausa.
        antes = len(ClienteFalso.chamadas)
        with pytest.raises(RuntimeError, match="limite"):
            x_collector._coletar_perfil_twikit("glassnode", 2)
        assert len(ClienteFalso.chamadas) == antes


class TestAdiamento:
    def test_conta_no_limite_adia_o_resto(self, monkeypatch):
        def bloqueado(*_a):
            raise RuntimeError("429")

        monkeypatch.setattr(x_collector, "_coletar_via_syndication", bloqueado)
        monkeypatch.setattr(x_collector, "_coletar_perfil_api", bloqueado)

        x_collector._coletar_perfil("a", 2)  # "a" lido (e em cache) antes do limite
        ClienteFalso.erros = [erro_429(600)]         # "b" esbarra no limite
        nao_lidos = []
        tweets = x_collector.coletar_feed_x(["a", "b", "c", "d"], 2, nao_lidos=nao_lidos)

        assert {t["perfil"] for t in tweets} == {"@a"}
        assert nao_lidos == ["b", "c", "d"]
        # Depois do 429, ninguém mais consultou o twikit.
        assert ClienteFalso.chamadas[-1] == ("perfil", "b")

    def test_sem_nao_lidos_mantem_fallback(self, monkeypatch):
        x_collector._BLOQUEIO_429["ate"] = time.time() + 600
        monkeypatch.setattr(x_collector, "_coletar_via_syndication",
                            lambda u, n: [{"texto": "btc", "timestamp_post": x_collector._agora_utc_naive(),
                                           "perfil": f"@{u}"}])
        tweets = x_collector.coletar_feed_x(["a"], 2)
        assert len(tweets) == 1


class TestPausaEntrePerfis:
    def test_pausa_so_entre_consultas_reais(self, monkeypatch):
        pausas = []
        monkeypatch.setattr(x_collector, "X_PAUSA_ENTRE_PERFIS", 1.5)
        monkeypatch.setattr(x_collector._time, "sleep", lambda s: pausas.append(s))

        x_collector.coletar_feed_x(["a", "b", "c"], 2)
        assert pausas == [1.5, 1.5]  # antes de b e de c, não antes de a

        pausas.clear()
        x_collector.coletar_feed_x(["a", "b", "c"], 2)  # tudo vem do cache
        assert pausas == []
