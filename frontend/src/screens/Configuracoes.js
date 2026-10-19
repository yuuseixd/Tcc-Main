import { useState } from "react";
import { api } from "../api";
import { INTERVALOS_COLETA } from "../constants";
import { Pill, Seg, Switch, Topline } from "../components/ui";

const TEMAS = [
  { valor: "light", rotulo: "Claro" },
  { valor: "dark", rotulo: "Escuro" },
  { valor: "system", rotulo: "Sistema" },
];

export default function Configuracoes({
  moeda, notify, tema, setTema, autoRefresh, setAutoRefresh, autoColeta,
  setAutoColeta, perfisX, setPerfisX, twitterOk, abrirLogin, redditOauth,
  usuario, irParaPerfil, sair,
}) {
  const [sincronizando, setSincronizando] = useState(false);

  const sincronizar = async () => {
    setSincronizando(true);
    try {
      const dados = await api.syncBinance(moeda, 7);
      notify.ok(dados.mensagem);
    } catch (e) {
      notify.erro(`Erro ao sincronizar: ${e.message}`);
    } finally {
      setSincronizando(false);
    }
  };

  return (
    <section className="screen" aria-label="Configurações">
      <Topline titulo="Configurações" />
      <div className="two">
        <div className="card stack">
          <h2>Minha conta</h2>
          <div className="stack" style={{ gap: 4 }}>
            <b>{usuario.nome}</b>
            <span className="hint">{usuario.email}</span>
          </div>
          <div className="row-between">
            <span className="hint">Perfil de investimento</span>
            {usuario.perfil ? <Pill tom="acc">{usuario.perfil}</Pill> : <Pill tom="warn">Pendente</Pill>}
          </div>
          <div className="btns">
            <button className="btn" type="button" onClick={irParaPerfil}>
              {usuario.perfil ? "Refazer questionário" : "Definir perfil"}
            </button>
            <button className="btn" type="button" onClick={sair}>Sair da conta</button>
          </div>
        </div>

        <div className="card stack">
          <h2>Aparência</h2>
          <div className="field">
            <span className="lab">Tema</span>
            <Seg label="Tema" value={tema} options={TEMAS} onChange={setTema} />
          </div>
        </div>

        <div className="card stack">
          <h2>Atualização dos dados</h2>
          <Switch
            ligado={autoRefresh}
            onChange={setAutoRefresh}
            titulo="Atualizar a cada 60 segundos"
            descricao="Recarrega o gráfico da Visão geral."
          />
          <div className="field">
            <label htmlFor="cfg-auto">Coleta automática</label>
            <select id="cfg-auto" value={autoColeta}
              onChange={(e) => setAutoColeta(parseInt(e.target.value, 10))}>
              {INTERVALOS_COLETA.map((i) => (
                <option key={i.valor} value={i.valor}>{i.rotulo}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="card stack">
          <div className="row-between">
            <h2>X (Twitter)</h2>
            <Pill tom={twitterOk ? "warn" : "neg"}>
              {twitterOk ? "Coleta ao vivo instável" : "Cookies não configurados"}
            </Pill>
          </div>
          <p className="hint">
            {twitterOk
              ? "Cookies da sessão salvos. A coleta ao vivo está instável no momento; as análises usam os posts já salvos no banco."
              : "Sem os cookies, o X não pode ser coletado. As análises usam os posts já salvos no banco."}
          </p>
          <div className="field">
            <label htmlFor="cfg-perfis">Perfis do X (separados por vírgula)</label>
            <input id="cfg-perfis" value={perfisX} onChange={(e) => setPerfisX(e.target.value)} />
          </div>
          <div className="btns">
            <button className="btn" type="button" onClick={abrirLogin}>Configurar cookies</button>
          </div>
        </div>

        <div className="card stack">
          <div className="row-between">
            <h2>Reddit</h2>
            <Pill tom={redditOauth ? "pos" : "warn"}>
              {redditOauth ? "Chaves configuradas" : "Sem chaves"}
            </Pill>
          </div>
          <p className="hint">
            {redditOauth
              ? "A coleta usa o acesso oficial do Reddit."
              : "Sem as chaves de acesso, o Reddit costuma recusar a coleta (erro 403). Crie as chaves em reddit.com/prefs/apps e preencha REDDIT_CLIENT_ID e REDDIT_CLIENT_SECRET no arquivo .env do servidor."}
          </p>
        </div>

        <div className="card stack">
          <h2>Preços da Binance</h2>
          <p className="hint">
            Baixa os últimos 7 dias de candles horários de {moeda} para o histórico do banco.
          </p>
          <div className="btns">
            <button className="btn" type="button" onClick={sincronizar} disabled={sincronizando}>
              {sincronizando ? "Sincronizando..." : "Sincronizar Binance (7 dias)"}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
