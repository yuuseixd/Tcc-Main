import { useCallback, useEffect, useMemo, useState } from "react";
import "./App.css";
import { aoPerderSessaoFazer, api, definirToken, temToken } from "./api";
import { Pill, Toasts, Vazio } from "./components/ui";
import Acesso from "./screens/Acesso";
import Analisador from "./screens/Analisador";
import Configuracoes from "./screens/Configuracoes";
import Geral from "./screens/Geral";
import Perfil from "./screens/Perfil";
import Redes from "./screens/Redes";
import Simulador from "./screens/Simulador";

/** `restrita`: exige conta. Só a Visão geral é aberta a quem não entrou. */
const TELAS = [
  { id: "geral", rotulo: "Visão geral", restrita: false, icone: "M4 13h6V4H4zM14 20h6V4h-6zM4 20h6v-4H4z" },
  { id: "redes", rotulo: "X e Reddit", restrita: true, icone: "M7 7h11M7 7l3-3M7 7l3 3M17 17H6M17 17l-3-3M17 17l-3 3" },
  { id: "sim", rotulo: "Simulador", restrita: true, icone: "M3 17l6-6 4 4 8-9M15 6h6v6" },
  { id: "analisador", rotulo: "Analisador", restrita: true, icone: "M4 19V9M10 19V5M16 19v-8M22 19H2" },
  { id: "perfil", rotulo: "Meu perfil", restrita: true, icone: "M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0" },
  {
    id: "config",
    rotulo: "Configurações",
    restrita: true,
    icone: "M12 15a3 3 0 100-6 3 3 0 000 6zM12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1",
  },
];

const PROXIMO_TEMA = { system: "light", light: "dark", dark: "system" };
const ROTULO_TEMA = { system: "Sistema", light: "Claro", dark: "Escuro" };

/** Lê e grava uma preferência simples no navegador. */
function usePreferencia(chave, inicial, converter = (v) => v) {
  const [valor, setValor] = useState(() => {
    try {
      const salvo = localStorage.getItem(chave);
      return salvo == null ? inicial : converter(salvo);
    } catch {
      return inicial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(chave, String(valor));
    } catch {
      /* navegador sem armazenamento: a preferência vale só nesta sessão */
    }
  }, [chave, valor]);
  return [valor, setValor];
}

function App() {
  const [tela, setTela] = usePreferencia("senty_tela", "geral");
  const [moeda, setMoeda] = usePreferencia("senty_moeda", "BTC");
  const [tema, setTema] = usePreferencia("senty_tema", "system");
  const [autoRefresh, setAutoRefresh] = usePreferencia(
    "senty_autorefresh", true, (v) => v === "true",
  );
  const [autoColeta, setAutoColeta] = usePreferencia(
    "senty_autocoleta", 0, (v) => parseInt(v, 10) || 0,
  );
  const [perfisX, setPerfisX] = usePreferencia(
    "sentcrypto_perfisX", "whale_alert, cabortopcripto",
  );

  const [usuario, setUsuario] = useState(null);
  const [verificando, setVerificando] = useState(temToken());
  const [modoAcesso, setModoAcesso] = useState("entrar");

  const [saude, setSaude] = useState({ online: null, twitter: false, reddit: false });
  const [riscos, setRiscos] = useState(null);
  const [avisos, setAvisos] = useState({ erro: null, ok: null, aviso: null });
  const [modalLogin, setModalLogin] = useState(false);
  const [authToken, setAuthToken] = useState("");
  const [ct0, setCt0] = useState("");
  const [salvando, setSalvando] = useState(false);

  // Funções estáveis: as telas as usam como dependência de efeitos.
  const notify = useMemo(
    () => ({
      erro: (t) => setAvisos((a) => ({ ...a, erro: t })),
      ok: (t) => setAvisos((a) => ({ ...a, ok: t })),
      aviso: (t) => setAvisos((a) => ({ ...a, aviso: t })),
    }),
    [],
  );

  useEffect(() => {
    if (tema === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = tema;
  }, [tema]);

  // Ao abrir o app, confere se o login guardado ainda vale.
  useEffect(() => {
    if (!temToken()) return;
    api
      .eu()
      .then(setUsuario)
      .catch(() => definirToken(null))
      .finally(() => setVerificando(false));
  }, []);

  useEffect(() => {
    aoPerderSessaoFazer(() => {
      definirToken(null);
      setUsuario(null);
      notify.aviso("Sua sessão expirou. Entre de novo para continuar.");
    });
    return () => aoPerderSessaoFazer(null);
  }, [notify]);

  // Já está logado e a tela guardada era a de acesso: volta para a Visão geral.
  useEffect(() => {
    if (usuario && tela === "acesso") setTela("geral");
  }, [usuario, tela, setTela]);

  const checarSaude = useCallback(async () => {
    try {
      const d = await api.saude();
      setSaude({
        online: d.status === "ok",
        twitter: Boolean(d.twitter_cookies),
        reddit: Boolean(d.reddit_oauth),
      });
    } catch {
      setSaude((s) => ({ ...s, online: false }));
    }
  }, []);

  useEffect(() => {
    checarSaude();
    // Risco medido de cada moeda; sem ele os avisos de risco simplesmente não aparecem.
    api.riscoMoedas().then(setRiscos).catch(() => setRiscos(null));
  }, [checarSaude]);

  /* ── Conta ───────────────────────────────────────────────────────── */

  const iniciarSessao = (resposta) => {
    definirToken(resposta.token);
    setUsuario(resposta.usuario);
    // Quem ainda não tem perfil vai direto ao questionário.
    setTela(resposta.usuario.perfil ? (tela === "acesso" ? "geral" : tela) : "perfil");
  };

  const entrar = async (email, senha) => iniciarSessao(await api.entrar(email, senha));
  const cadastrar = async (nome, email, senha) =>
    iniciarSessao(await api.cadastrar(nome, email, senha));

  const sair = async () => {
    try {
      await api.sair();
    } catch {
      /* se o servidor não respondeu, a sessão local é apagada do mesmo jeito */
    }
    definirToken(null);
    setUsuario(null);
    setTela("geral");
    notify.ok("Você saiu da conta.");
  };

  const abrirAcesso = useCallback((modo = "entrar") => {
    setModoAcesso(modo);
    setTela("acesso");
  }, [setTela]);

  const salvarCookies = async () => {
    setSalvando(true);
    try {
      await api.loginX(authToken, ct0);
      setSaude((s) => ({ ...s, twitter: true }));
      setModalLogin(false);
      setAuthToken("");
      setCt0("");
      notify.ok("Cookies do X salvos com sucesso.");
    } catch (e) {
      notify.erro(`Falha ao salvar os cookies: ${e.message}`);
    } finally {
      setSalvando(false);
    }
  };

  const abrirLogin = useCallback(() => setModalLogin(true), []);
  const comuns = { moeda, setMoeda, notify };

  const definicao = TELAS.find((t) => t.id === tela);
  const bloqueada = (definicao?.restrita && !usuario) || tela === "acesso";

  return (
    <div className="app">
      <aside className="side">
        <div className="brand">
          <i>
            <svg viewBox="0 0 24 24" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 16l5-5 4 4 7-8" />
            </svg>
          </i>
          SentyCrypto
        </div>
        <nav className="nav" role="tablist" aria-label="Seções">
          {TELAS.map((t) => (
            <button key={t.id} type="button" role="tab" aria-selected={tela === t.id}
              onClick={() => setTela(t.id)}>
              <svg viewBox="0 0 24 24"><path d={t.icone} /></svg>
              {t.rotulo}
              {t.restrita && !usuario && <span className="cadeado" title="Exige conta" aria-label="exige conta">●</span>}
            </button>
          ))}
        </nav>

        <div className="conta">
          {usuario ? (
            <>
              <div className="conta-info">
                <span className="av" aria-hidden="true">{usuario.nome[0].toUpperCase()}</span>
                <div className="conta-texto">
                  <b title={usuario.email}>{usuario.nome}</b>
                  {usuario.perfil ? <Pill tom="acc">{usuario.perfil}</Pill> : <Pill tom="warn">Perfil pendente</Pill>}
                </div>
              </div>
              <button className="btn sm" type="button" onClick={sair}>Sair</button>
            </>
          ) : (
            <>
              <button className="btn primary sm" type="button" onClick={() => abrirAcesso("entrar")}>Entrar</button>
              <button className="btn sm" type="button" onClick={() => abrirAcesso("cadastro")}>Criar conta</button>
            </>
          )}
        </div>

        <div className="foot">
          <span>
            <i className={`dot ${saude.online ? "" : "err"}`} />
            API {saude.online ? "online" : "offline"}
          </span>
          <span><i className="dot warn" />X: coleta ao vivo instável</span>
          <span>
            <i className={`dot ${saude.reddit ? "" : "warn"}`} />
            Reddit: {saude.reddit ? "chaves configuradas" : "configurar chaves"}
          </span>
          <button className="link" type="button" onClick={() => setTema(PROXIMO_TEMA[tema])}
            title="Alternar o tema">
            Tema: {ROTULO_TEMA[tema]}
          </button>
        </div>
      </aside>

      <main className="work">
        <Toasts {...avisos} fechar={(tipo) => setAvisos((a) => ({ ...a, [tipo]: null }))} />

        {usuario && !usuario.perfil && tela !== "perfil" && (
          <div className="callout">
            <span>Defina seu perfil de investimento para personalizar o app.</span>
            <button className="btn sm" type="button" onClick={() => setTela("perfil")}>Definir agora</button>
          </div>
        )}

        {bloqueada && verificando && <div className="card"><Vazio titulo="Carregando..." /></div>}
        {bloqueada && !verificando && (
          <Acesso key={modoAcesso} modoInicial={modoAcesso} entrar={entrar} cadastrar={cadastrar}
            motivo={definicao?.restrita ? `Entre na sua conta para usar ${definicao.rotulo}.` : undefined} />
        )}

        {!bloqueada && tela === "geral" && (
          <Geral {...comuns} autoRefresh={autoRefresh} usuario={usuario} abrirAcesso={abrirAcesso} riscos={riscos} />
        )}
        {!bloqueada && tela === "redes" && (
          <Redes {...comuns} perfisX={perfisX} setPerfisX={setPerfisX}
            twitterOk={saude.twitter} abrirLogin={abrirLogin} redditOauth={saude.reddit}
            autoColeta={autoColeta} setAutoColeta={setAutoColeta} />
        )}
        {!bloqueada && tela === "sim" && (
          <Simulador {...comuns} perfisX={perfisX} perfilUsuario={usuario?.perfil} riscos={riscos} />
        )}
        {!bloqueada && tela === "analisador" && <Analisador {...comuns} />}
        {!bloqueada && tela === "perfil" && (
          <Perfil usuario={usuario} atualizarUsuario={setUsuario} notify={notify} />
        )}
        {!bloqueada && tela === "config" && (
          <Configuracoes {...comuns} tema={tema} setTema={setTema}
            autoRefresh={autoRefresh} setAutoRefresh={setAutoRefresh}
            autoColeta={autoColeta} setAutoColeta={setAutoColeta}
            perfisX={perfisX} setPerfisX={setPerfisX} usuario={usuario}
            irParaPerfil={() => setTela("perfil")} sair={sair}
            twitterOk={saude.twitter} abrirLogin={abrirLogin} redditOauth={saude.reddit} />
        )}
      </main>

      {modalLogin && (
        <div className="modal-back" onClick={() => setModalLogin(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="Configurar cookies do X"
            onClick={(e) => e.stopPropagation()}>
            <h2>Configurar cookies do X</h2>
            <p className="hint">Para coletar tweets, informe os cookies de autenticação do X:</p>
            <ol>
              <li>Abra <strong>x.com</strong> no Chrome e faça login</li>
              <li>Pressione <strong>F12</strong> e abra a aba <strong>Application</strong></li>
              <li>Em <strong>Cookies</strong>, escolha <strong>https://x.com</strong></li>
              <li>Copie <strong>auth_token</strong> e <strong>ct0</strong></li>
            </ol>
            <div className="field">
              <label htmlFor="c-auth">auth_token</label>
              <input id="c-auth" value={authToken} onChange={(e) => setAuthToken(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="c-ct0">ct0</label>
              <input id="c-ct0" value={ct0} onChange={(e) => setCt0(e.target.value)} />
            </div>
            <div className="btns">
              <button className="btn primary" type="button" onClick={salvarCookies}
                disabled={!authToken || !ct0 || salvando}>
                {salvando ? "Salvando..." : "Salvar cookies"}
              </button>
              <button className="btn" type="button" onClick={() => setModalLogin(false)}>Cancelar</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
