"""Testes de contas, sessões, perfil de investimento e proteção das rotas.

Usa SQLite em memória e o TestClient do FastAPI sem iniciar o app (assim o
modelo de sentimento não é carregado).

Executar (a partir da pasta backend/):

    python -m pytest tests/test_contas.py -v
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

os.environ["CARREGAR_BERT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app as app_modulo
from db import Base, get_db
from models import Sessao, Usuario
from services import contas as svc_contas
from services import perfil as svc_perfil
from utils.tempo import agora_utc

SENHA = "senha-bem-longa-123"


@pytest.fixture
def sessao_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    fabrica = sessionmaker(bind=engine)
    sessao = fabrica()
    yield sessao, fabrica
    sessao.close()


@pytest.fixture
def client(sessao_db, monkeypatch):
    _, fabrica = sessao_db

    def _db():
        db = fabrica()
        try:
            yield db
        finally:
            db.close()

    app_modulo.app.dependency_overrides[get_db] = _db
    # Cada teste começa com a contagem de tentativas zerada.
    monkeypatch.setattr(
        app_modulo, "limitador_login", svc_contas.LimitadorTentativas()
    )
    yield TestClient(app_modulo.app)
    app_modulo.app.dependency_overrides.clear()


def cadastrar(client, email="ana@exemplo.com", nome="Ana Souza", senha=SENHA):
    return client.post(
        "/auth/cadastro", json={"nome": nome, "email": email, "senha": senha}
    )


def cabecalho(token):
    return {"Authorization": f"Bearer {token}"}


# ── Senha ───────────────────────────────────────────────────────────────────


class TestSenha:
    def test_verifica_a_senha_certa_e_recusa_a_errada(self):
        h = svc_contas.hash_senha(SENHA)
        assert svc_contas.verificar_senha(SENHA, h)
        assert not svc_contas.verificar_senha("outra-senha-123", h)

    def test_o_sal_faz_hashes_diferentes_para_a_mesma_senha(self):
        assert svc_contas.hash_senha(SENHA) != svc_contas.hash_senha(SENHA)

    def test_o_hash_nao_contem_a_senha(self):
        assert SENHA not in svc_contas.hash_senha(SENHA)

    @pytest.mark.parametrize("lixo", ["", "abc", "scrypt$1$2", "md5$a$b$c$d$e", "scrypt$x$8$1$AA==$AA=="])
    def test_hash_malformado_nao_valida_nem_quebra(self, lixo):
        assert svc_contas.verificar_senha(SENHA, lixo) is False


# ── Cadastro ────────────────────────────────────────────────────────────────


class TestCadastro:
    def test_cria_a_conta_e_ja_entra(self, client):
        r = cadastrar(client)
        assert r.status_code == 201
        corpo = r.json()
        assert corpo["token"]
        assert corpo["usuario"]["email"] == "ana@exemplo.com"
        assert corpo["usuario"]["perfil"] is None

    def test_a_resposta_nunca_devolve_senha_nem_hash(self, client):
        texto = cadastrar(client).text
        assert SENHA not in texto and "scrypt" not in texto

    def test_a_senha_nao_fica_no_banco(self, client, sessao_db):
        db, _ = sessao_db
        cadastrar(client)
        usuario = db.query(Usuario).one()
        assert usuario.senha_hash.startswith("scrypt$")
        assert SENHA not in usuario.senha_hash

    def test_email_repetido_e_recusado_sem_diferenciar_maiusculas(self, client):
        cadastrar(client, email="ana@exemplo.com")
        r = cadastrar(client, email="  ANA@Exemplo.com ")
        assert r.status_code == 409

    @pytest.mark.parametrize("email", ["", "sem-arroba", "a@b", "a b@c.com"])
    def test_email_invalido(self, client, email):
        assert cadastrar(client, email=email).status_code == 400

    @pytest.mark.parametrize("senha", ["", "curta", "        "])
    def test_senha_fraca(self, client, senha):
        assert cadastrar(client, senha=senha).status_code == 400

    def test_nome_muito_curto(self, client):
        assert cadastrar(client, nome="A").status_code == 400


# ── Login e sessão ──────────────────────────────────────────────────────────


class TestLogin:
    def test_entra_com_email_e_senha_corretos(self, client):
        cadastrar(client)
        r = client.post("/auth/login", json={"email": "ANA@exemplo.com", "senha": SENHA})
        assert r.status_code == 200
        assert r.json()["token"]

    def test_mesma_mensagem_para_senha_errada_e_email_inexistente(self, client):
        cadastrar(client)
        errada = client.post("/auth/login", json={"email": "ana@exemplo.com", "senha": "errada-123"})
        inexistente = client.post("/auth/login", json={"email": "ninguem@exemplo.com", "senha": SENHA})
        assert errada.status_code == inexistente.status_code == 401
        assert errada.json() == inexistente.json()

    def test_bloqueia_apos_muitas_falhas(self, client):
        cadastrar(client)
        dados = {"email": "ana@exemplo.com", "senha": "errada-123"}
        for _ in range(5):
            assert client.post("/auth/login", json=dados).status_code == 401
        assert client.post("/auth/login", json=dados).status_code == 429
        # Mesmo com a senha certa, a pessoa precisa esperar.
        certa = {"email": "ana@exemplo.com", "senha": SENHA}
        assert client.post("/auth/login", json=certa).status_code == 429

    def test_login_certo_zera_a_contagem_de_falhas(self, client):
        cadastrar(client)
        ruim = {"email": "ana@exemplo.com", "senha": "errada-123"}
        bom = {"email": "ana@exemplo.com", "senha": SENHA}
        for _ in range(4):
            client.post("/auth/login", json=ruim)
        assert client.post("/auth/login", json=bom).status_code == 200
        for _ in range(4):
            assert client.post("/auth/login", json=ruim).status_code == 401

    def test_eu_exige_token_valido(self, client):
        assert client.get("/auth/eu").status_code == 401
        assert client.get("/auth/eu", headers=cabecalho("token-falso")).status_code == 401
        token = cadastrar(client).json()["token"]
        r = client.get("/auth/eu", headers=cabecalho(token))
        assert r.status_code == 200 and r.json()["nome"] == "Ana Souza"

    def test_logout_invalida_o_token(self, client):
        token = cadastrar(client).json()["token"]
        assert client.post("/auth/logout", headers=cabecalho(token)).status_code == 200
        assert client.get("/auth/eu", headers=cabecalho(token)).status_code == 401

    def test_sessao_expirada_nao_vale(self, client, sessao_db):
        db, _ = sessao_db
        token = cadastrar(client).json()["token"]
        s = db.query(Sessao).one()
        s.expira_em = agora_utc() - timedelta(seconds=1)
        db.commit()
        assert client.get("/auth/eu", headers=cabecalho(token)).status_code == 401

    def test_o_banco_guarda_so_o_hash_do_token(self, client, sessao_db):
        db, _ = sessao_db
        token = cadastrar(client).json()["token"]
        assert token not in db.query(Sessao).one().token_hash

    def test_cabecalho_sem_bearer_e_ignorado(self, client):
        token = cadastrar(client).json()["token"]
        assert client.get("/auth/eu", headers={"Authorization": token}).status_code == 401


# ── Limitador ───────────────────────────────────────────────────────────────


class TestLimitador:
    def test_libera_depois_da_janela(self):
        agora = [0.0]
        lim = svc_contas.LimitadorTentativas(maximo=2, janela_s=60, relogio=lambda: agora[0])
        lim.registrar_falha("k"); lim.registrar_falha("k")
        assert lim.bloqueado("k")
        agora[0] = 61
        assert not lim.bloqueado("k")

    def test_chaves_diferentes_nao_se_misturam(self):
        lim = svc_contas.LimitadorTentativas(maximo=1)
        lim.registrar_falha("a")
        assert lim.bloqueado("a") and not lim.bloqueado("b")


# ── Rotas protegidas ────────────────────────────────────────────────────────

PROTEGIDAS = [
    ("post", "/simulacao/investimento"),
    ("get", "/comparacao-fontes"),
    ("post", "/gerar-relatorio"),
    ("post", "/gerar-relatorio-correlacao"),
    ("post", "/coletar/reddit"),
    ("post", "/coletar/x"),
    ("post", "/feed/x"),
    ("post", "/analisar-texto"),
    ("post", "/sync-binance"),
    ("get", "/posts-por-hora"),
]


class TestProtecaoDasRotas:
    @pytest.mark.parametrize("metodo,caminho", PROTEGIDAS)
    def test_sem_login_responde_401(self, client, metodo, caminho):
        assert client.request(metodo.upper(), caminho, json={}).status_code == 401

    @pytest.mark.parametrize("metodo,caminho", PROTEGIDAS)
    def test_com_login_passa_da_autenticacao(self, client, metodo, caminho):
        token = cadastrar(client).json()["token"]
        r = client.request(metodo.upper(), caminho, json={}, headers=cabecalho(token))
        assert r.status_code != 401

    def test_rotas_publicas_continuam_abertas(self, client):
        assert client.get("/").status_code == 200
        assert client.get("/perfil/questionario").status_code == 200
        assert client.get("/status/reddit").status_code == 200


# ── Questionário de perfil ──────────────────────────────────────────────────

IDS = [p["id"] for p in svc_perfil.PERGUNTAS]


def respostas(**sobrescritas):
    """Todas as respostas na opção 1 (neutras), com ajustes por pergunta."""
    base = {i: 1 for i in IDS}
    base.update(sobrescritas)
    return base


class TestQuestionario:
    def test_tudo_cauteloso_e_conservador(self):
        r = svc_perfil.calcular_perfil({i: 0 for i in IDS})
        assert r["perfil"] == "Conservador" and r["pontuacao"] == 11

    def test_tudo_arrojado_e_arrojado(self):
        r = svc_perfil.calcular_perfil({i: 3 for i in IDS})
        assert r["perfil"] == "Arrojado" and r["pontuacao"] == 44 == r["pontuacao_maxima"]

    def test_meio_termo_e_moderado(self):
        r = svc_perfil.calcular_perfil({i: 2 for i in IDS})
        assert r["perfil"] == "Moderado" and r["pontuacao"] == 33

    @pytest.mark.parametrize("pontos,esperado", [(22, "Conservador"), (23, "Moderado"), (33, "Moderado"), (34, "Arrojado")])
    def test_limites_entre_perfis(self, pontos, esperado):
        assert svc_perfil._perfil_pela_pontuacao(pontos) == esperado

    def test_cortes_dividem_a_faixa_de_pontos_em_tercos(self):
        assert svc_perfil.LIMITE_CONSERVADOR == 22 and svc_perfil.LIMITE_MODERADO == 33

    def test_sem_reserva_limita_arrojado_a_moderado(self):
        r = svc_perfil.calcular_perfil({**{i: 3 for i in IDS}, "reserva": 0})
        assert r["perfil"] == "Moderado" and r["travas"]

    def test_precisar_do_dinheiro_limita_arrojado_a_moderado(self):
        r = svc_perfil.calcular_perfil({**{i: 3 for i in IDS}, "dependencia": 0})
        assert r["perfil"] == "Moderado" and r["travas"]

    def test_falta_de_experiencia_limita_arrojado_a_moderado(self):
        r = svc_perfil.calcular_perfil({**{i: 3 for i in IDS}, "experiencia": 0, "operacoes": 0})
        assert r["perfil"] == "Moderado" and "experiência" in r["travas"][0]

    def test_so_um_dos_dois_sinais_de_pouco_conhecimento_nao_trava(self):
        r = svc_perfil.calcular_perfil({**{i: 3 for i in IDS}, "experiencia": 0})
        assert r["perfil"] == "Arrojado" and r["travas"] == []

    @pytest.mark.parametrize("fragilidades", [
        {"reserva": 0, "dependencia": 0},
        {"reserva": 0, "experiencia": 0, "operacoes": 0},
        {"dependencia": 0, "experiencia": 0, "operacoes": 0},
        {"reserva": 0, "dependencia": 0, "experiencia": 0, "operacoes": 0},
    ])
    def test_duas_fragilidades_ou_mais_viram_conservador(self, fragilidades):
        r = svc_perfil.calcular_perfil({**{i: 3 for i in IDS}, **fragilidades})
        assert r["perfil"] == "Conservador" and r["travas"]

    def test_trava_nao_mexe_em_quem_ja_e_conservador(self):
        r = svc_perfil.calcular_perfil({i: 0 for i in IDS})
        assert r["perfil"] == "Conservador" and r["travas"] == []

    def test_pontos_por_eixo_somam_a_pontuacao(self):
        r = svc_perfil.calcular_perfil({i: 2 for i in IDS})
        assert sum(e["pontos"] for e in r["por_eixo"].values()) == r["pontuacao"]
        assert sum(e["maximo"] for e in r["por_eixo"].values()) == r["pontuacao_maxima"]
        assert set(r["por_eixo"]) == {"objetivos", "situacao_financeira", "conhecimento"}

    def test_pergunta_sem_resposta(self):
        faltando = {i: 1 for i in IDS[:-1]}
        with pytest.raises(svc_perfil.ErroQuestionario):
            svc_perfil.calcular_perfil(faltando)

    @pytest.mark.parametrize("valor", [-1, 4, 99, "1", 1.5, None, True])
    def test_opcao_invalida(self, valor):
        with pytest.raises(svc_perfil.ErroQuestionario):
            svc_perfil.calcular_perfil({**{i: 1 for i in IDS}, "objetivo": valor})

    def test_toda_pergunta_tem_quatro_opcoes_e_id_unico(self):
        assert len(set(IDS)) == len(IDS)
        assert all(len(p["opcoes"]) == 4 for p in svc_perfil.PERGUNTAS)

    def test_cobre_todos_os_itens_minimos_do_art_3_da_resolucao_cvm_30(self):
        # § 1º (objetivos), § 2º (situação financeira) e § 3º (conhecimento),
        # três itens cada um. Se alguém remover uma pergunta, este teste avisa.
        itens = [f"§ {p}º, {i} " for p in (1, 2, 3) for i in ("I", "II", "III")]
        bases = " ".join(p["base"] + " " for p in svc_perfil.PERGUNTAS)
        for item in itens:
            assert item in bases, f"nenhuma pergunta cobre o art. 3º, {item.strip()}"

    def test_toda_pergunta_aponta_sua_base_e_seu_eixo(self):
        for p in svc_perfil.PERGUNTAS:
            assert p["base"].startswith("Res. CVM 30")
            assert p["eixo"] in svc_perfil.EIXOS

    def test_as_fontes_sao_citadas(self):
        urls = " ".join(f["url"] for f in svc_perfil.FONTES)
        assert "cvm.gov.br" in urls and "anbima.com.br" in urls


class TestPerfilNaApi:
    def test_fluxo_responder_e_confirmar(self, client):
        token = cadastrar(client).json()["token"]
        h = cabecalho(token)
        r = client.post("/perfil/questionario", json={"respostas": {i: 3 for i in IDS}}, headers=h)
        assert r.status_code == 200 and r.json()["perfil"] == "Arrojado"

        # Sugerido, mas ainda não confirmado.
        eu = client.get("/auth/eu", headers=h).json()
        assert eu["perfil_sugerido"] == "Arrojado" and eu["perfil"] is None

        # A pessoa escolhe outro perfil.
        r = client.post("/perfil/confirmar", json={"perfil": "Moderado"}, headers=h)
        assert r.status_code == 200 and r.json()["perfil"] == "Moderado"
        assert client.get("/auth/eu", headers=h).json()["perfil"] == "Moderado"

    def test_perfil_invalido(self, client):
        token = cadastrar(client).json()["token"]
        r = client.post("/perfil/confirmar", json={"perfil": "Ousado"}, headers=cabecalho(token))
        assert r.status_code == 422

    def test_respostas_incompletas(self, client):
        token = cadastrar(client).json()["token"]
        r = client.post("/perfil/questionario", json={"respostas": {"objetivo": 1}}, headers=cabecalho(token))
        assert r.status_code == 422

    def test_perfil_exige_login(self, client):
        assert client.post("/perfil/confirmar", json={"perfil": "Moderado"}).status_code == 401
        assert client.post("/perfil/questionario", json={"respostas": {}}).status_code == 401

    def test_um_usuario_nao_altera_o_perfil_de_outro(self, client):
        t1 = cadastrar(client, email="a@exemplo.com").json()["token"]
        t2 = cadastrar(client, email="b@exemplo.com").json()["token"]
        client.post("/perfil/confirmar", json={"perfil": "Arrojado"}, headers=cabecalho(t1))
        assert client.get("/auth/eu", headers=cabecalho(t2)).json()["perfil"] is None
