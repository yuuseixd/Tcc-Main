"""Contas de usuário: cadastro, login e sessões.

Decisões de segurança
---------------------
* **Senha**: nunca é guardada. Só o hash ``scrypt`` (biblioteca padrão, sem
  dependência extra) com sal aleatório por usuário, comparado em tempo
  constante.
* **Sessão**: o token é aleatório (256 bits) e o banco guarda só o SHA-256 dele;
  quem lê o banco não consegue se passar por ninguém. Expira em 7 dias e é
  revogado no logout.
* **Login**: a mensagem de erro é a mesma para e-mail inexistente e senha
  errada, e o tempo de resposta também (um hash falso é calculado quando o
  e-mail não existe). Tentativas repetidas são limitadas por e-mail e IP.
"""

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Sessao, Usuario
from utils.tempo import agora_utc

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
DURACAO_SESSAO = timedelta(days=7)
MIN_SENHA, MAX_SENHA = 8, 128
_PADRAO_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErroConta(Exception):
    """Erro de regra de negócio das contas, com o status HTTP adequado."""

    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


# ── Senha ───────────────────────────────────────────────────────────────────


def hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(16)
    derivada = hashlib.scrypt(
        senha.encode(), salt=sal, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32,
    )
    return "$".join([
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.b64encode(sal).decode(), base64.b64encode(derivada).decode(),
    ])


def verificar_senha(senha: str, armazenado: str) -> bool:
    try:
        esquema, n, r, p, sal_b64, hash_b64 = armazenado.split("$")
        if esquema != "scrypt":
            return False
        esperado = base64.b64decode(hash_b64)
        derivada = hashlib.scrypt(
            senha.encode(), salt=base64.b64decode(sal_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(esperado),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derivada, esperado)


_hash_falso: str | None = None


def _gastar_tempo_de_verificacao(senha: str) -> None:
    """Iguala o tempo de resposta quando o e-mail não existe."""
    global _hash_falso
    if _hash_falso is None:
        _hash_falso = hash_senha("senha-que-ninguem-usa")
    verificar_senha(senha, _hash_falso)


# ── Validação ───────────────────────────────────────────────────────────────


def normalizar_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not _PADRAO_EMAIL.match(email):
        raise ErroConta("Informe um e-mail válido.")
    return email


def validar_nome(nome: str) -> str:
    nome = " ".join((nome or "").split())
    if not 2 <= len(nome) <= 120:
        raise ErroConta("Informe seu nome (entre 2 e 120 caracteres).")
    return nome


def validar_senha(senha: str) -> str:
    if not senha or len(senha) < MIN_SENHA:
        raise ErroConta(f"A senha precisa ter pelo menos {MIN_SENHA} caracteres.")
    if len(senha) > MAX_SENHA:
        raise ErroConta(f"A senha pode ter no máximo {MAX_SENHA} caracteres.")
    if not senha.strip():
        raise ErroConta("A senha não pode ser só espaços.")
    return senha


# ── Cadastro e login ────────────────────────────────────────────────────────


def criar_usuario(db: Session, nome: str, email: str, senha: str) -> Usuario:
    nome = validar_nome(nome)
    email = normalizar_email(email)
    senha = validar_senha(senha)

    if db.query(Usuario).filter(Usuario.email == email).first():
        raise ErroConta("Já existe uma conta com esse e-mail.", status=409)

    usuario = Usuario(nome=nome, email=email, senha_hash=hash_senha(senha))
    db.add(usuario)
    try:
        db.commit()
    except IntegrityError:  # dois cadastros simultâneos com o mesmo e-mail
        db.rollback()
        raise ErroConta("Já existe uma conta com esse e-mail.", status=409)
    db.refresh(usuario)
    return usuario


def autenticar(db: Session, email: str, senha: str) -> Usuario | None:
    email = (email or "").strip().lower()
    usuario = db.query(Usuario).filter(Usuario.email == email).first()
    if usuario is None:
        _gastar_tempo_de_verificacao(senha or "")
        return None
    if not verificar_senha(senha or "", usuario.senha_hash):
        return None
    return usuario


# ── Sessões ─────────────────────────────────────────────────────────────────


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def criar_sessao(db: Session, usuario: Usuario) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Sessao(
        token_hash=_hash_token(token),
        usuario_id=usuario.id,
        expira_em=agora_utc() + DURACAO_SESSAO,
    ))
    db.commit()
    return token


def usuario_da_sessao(db: Session, token: str | None) -> Usuario | None:
    if not token:
        return None
    sessao = (
        db.query(Sessao).filter(Sessao.token_hash == _hash_token(token)).first()
    )
    if sessao is None:
        return None
    if sessao.expira_em <= agora_utc():
        db.delete(sessao)
        db.commit()
        return None
    return db.get(Usuario, sessao.usuario_id)


def encerrar_sessao(db: Session, token: str | None) -> None:
    if not token:
        return
    db.query(Sessao).filter(Sessao.token_hash == _hash_token(token)).delete()
    db.commit()


# ── Limite de tentativas de login ───────────────────────────────────────────


class LimitadorTentativas:
    """Bloqueia a chave (e-mail + IP) após muitas falhas numa janela de tempo.

    Fica em memória: reiniciar o servidor zera a contagem. Para um TCC rodando
    num processo só isso basta; em produção com vários processos o certo é
    um armazenamento compartilhado.
    """

    def __init__(self, maximo: int = 5, janela_s: int = 600, relogio=time.monotonic):
        self.maximo, self.janela_s, self.relogio = maximo, janela_s, relogio
        self._falhas: dict[str, list[float]] = {}

    def _recentes(self, chave: str) -> list[float]:
        corte = self.relogio() - self.janela_s
        recentes = [t for t in self._falhas.get(chave, []) if t > corte]
        if recentes:
            self._falhas[chave] = recentes
        else:
            self._falhas.pop(chave, None)
        return recentes

    def bloqueado(self, chave: str) -> bool:
        return len(self._recentes(chave)) >= self.maximo

    def registrar_falha(self, chave: str) -> None:
        self._falhas.setdefault(chave, []).append(self.relogio())

    def limpar(self, chave: str) -> None:
        self._falhas.pop(chave, None)
