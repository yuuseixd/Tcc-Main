import { useState } from "react";
import { Seg } from "../components/ui";

const MODOS = [
  { valor: "entrar", rotulo: "Entrar" },
  { valor: "cadastro", rotulo: "Criar conta" },
];

/** Tela de entrada: aparece no lugar de qualquer área que exige conta. */
export default function Acesso({ motivo, modoInicial = "entrar", entrar, cadastrar }) {
  const [modo, setModo] = useState(modoInicial);
  const [nome, setNome] = useState("");
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [verSenha, setVerSenha] = useState(false);
  const [erro, setErro] = useState(null);
  const [enviando, setEnviando] = useState(false);

  const ehCadastro = modo === "cadastro";

  const enviar = async (e) => {
    e.preventDefault();
    setErro(null);
    setEnviando(true);
    try {
      if (ehCadastro) await cadastrar(nome, email, senha);
      else await entrar(email, senha);
    } catch (ex) {
      setErro(ex.message || "Não foi possível concluir. Tente de novo.");
    } finally {
      setEnviando(false);
    }
  };

  return (
    <section className="screen acesso" aria-label="Acesso à conta">
      <div className="card stack acesso-card">
        <div>
          <h1 className="acesso-titulo">
            {ehCadastro ? "Crie sua conta" : "Entre na sua conta"}
          </h1>
          <p className="hint">
            {motivo ||
              "A Visão geral é aberta a todos. Para simular, comparar as redes e gerar relatórios, é preciso ter uma conta."}
          </p>
        </div>

        <Seg label="Modo" value={modo} options={MODOS}
          onChange={(m) => { setModo(m); setErro(null); }} />

        <form className="stack" onSubmit={enviar} noValidate
          aria-label={ehCadastro ? "Formulário de cadastro" : "Formulário de login"}>
          {ehCadastro && (
            <div className="field">
              <label htmlFor="ac-nome">Nome</label>
              <input id="ac-nome" autoComplete="name" value={nome}
                onChange={(e) => setNome(e.target.value)} required />
            </div>
          )}
          <div className="field">
            <label htmlFor="ac-email">E-mail</label>
            <input id="ac-email" type="email" autoComplete="email" value={email}
              onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="ac-senha">Senha</label>
            <div className="senha-linha">
              <input id="ac-senha" type={verSenha ? "text" : "password"}
                autoComplete={ehCadastro ? "new-password" : "current-password"}
                value={senha} onChange={(e) => setSenha(e.target.value)} required />
              <button type="button" className="btn sm" onClick={() => setVerSenha((v) => !v)}
                aria-pressed={verSenha}>
                {verSenha ? "Ocultar" : "Mostrar"}
              </button>
            </div>
            {ehCadastro && <span className="hint">Use pelo menos 8 caracteres.</span>}
          </div>

          {erro && <div className="callout erro" role="alert">{erro}</div>}

          <div className="btns">
            <button className="btn primary" type="submit"
              disabled={enviando || !email || !senha || (ehCadastro && !nome)}>
              {enviando ? "Aguarde..." : ehCadastro ? "Criar conta" : "Entrar"}
            </button>
          </div>
        </form>

        <p className="hint">
          {ehCadastro
            ? "Sua senha é guardada de forma protegida (nunca em texto). Depois de criar a conta, você responde um questionário rápido para definirmos seu perfil de investimento."
            : "Ainda não tem conta? Escolha “Criar conta” acima."}
        </p>
      </div>
    </section>
  );
}
