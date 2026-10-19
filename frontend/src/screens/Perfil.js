import { useEffect, useState } from "react";
import { api } from "../api";
import { Pill, Spinner, Topline, Vazio } from "../components/ui";

const AVISO_EDUCATIVO =
  "Este questionário é um instrumento educacional do projeto, inspirado na análise de perfil " +
  "(suitability) da Resolução CVM 30. Não é recomendação de investimento nem substitui a " +
  "avaliação de uma instituição financeira.";

function Fontes({ fontes }) {
  if (!fontes?.length) return null;
  return (
    <div>
      <h3>Fundamentação</h3>
      <ul className="lista">
        {fontes.map((f) => (
          <li key={f.url}>
            <a href={f.url} target="_blank" rel="noopener noreferrer">{f.titulo}</a>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function Perfil({ usuario, atualizarUsuario, notify }) {
  const [quest, setQuest] = useState(null);
  const [respostas, setRespostas] = useState({});
  const [sugestao, setSugestao] = useState(null);
  const [escolhido, setEscolhido] = useState(null);
  const [refazendo, setRefazendo] = useState(false);
  const [enviando, setEnviando] = useState(false);

  useEffect(() => {
    api
      .questionario()
      .then(setQuest)
      .catch((e) => notify.erro(`Não foi possível carregar o questionário: ${e.message}`));
  }, [notify]);

  if (!quest) {
    return (
      <section className="screen" aria-label="Meu perfil">
        <Topline titulo="Meu perfil" />
        <div className="card"><Vazio titulo="Carregando..." /></div>
      </section>
    );
  }

  const descricao = (nome) => quest.perfis.find((p) => p.nome === nome)?.descricao;
  const total = quest.perguntas.length;
  const respondidas = quest.perguntas.filter((p) => respostas[p.id] != null).length;

  const enviar = async (e) => {
    e.preventDefault();
    setEnviando(true);
    try {
      const r = await api.enviarQuestionario(respostas);
      setSugestao(r);
      setEscolhido(r.perfil);
    } catch (ex) {
      notify.erro(`Não foi possível calcular o perfil: ${ex.message}`);
    } finally {
      setEnviando(false);
    }
  };

  const confirmar = async () => {
    setEnviando(true);
    try {
      atualizarUsuario(await api.confirmarPerfil(escolhido));
      setSugestao(null);
      setRefazendo(false);
      setRespostas({});
      notify.ok(`Perfil definido: ${escolhido}.`);
    } catch (ex) {
      notify.erro(`Não foi possível salvar o perfil: ${ex.message}`);
    } finally {
      setEnviando(false);
    }
  };

  /* 1. Resultado do questionário: a pessoa confirma ou ajusta. */
  if (sugestao) {
    return (
      <section className="screen" aria-label="Meu perfil">
        <Topline titulo="Meu perfil" />
        <div className="card stack">
          <div>
            <h2>Seu perfil sugerido</h2>
            <p className="hint">
              Com base nas suas respostas ({sugestao.pontuacao} de {sugestao.pontuacao_maxima} pontos).
            </p>
          </div>
          <div className="perfil-destaque">
            <span className="v">{sugestao.perfil}</span>
            <p>{sugestao.descricao}</p>
          </div>
          {sugestao.por_eixo && (
            <div className="eixos">
              {Object.entries(sugestao.por_eixo).map(([chave, e]) => (
                <div className="eixo" key={chave}>
                  <div className="row-between">
                    <span>{e.nome}</span>
                    <b className="num">{e.pontos}/{e.maximo}</b>
                  </div>
                  <div className="progresso"><i style={{ width: `${(e.pontos / e.maximo) * 100}%` }} /></div>
                </div>
              ))}
            </div>
          )}
          {sugestao.travas.map((t) => (
            <div className="callout" key={t}>{t}</div>
          ))}
          <div className="field">
            <span className="lab">Confirme ou escolha outro perfil</span>
            <div className="opcoes" role="radiogroup" aria-label="Perfil">
              {quest.perfis.map((p) => (
                <button key={p.nome} type="button" role="radio" aria-checked={escolhido === p.nome}
                  className="opcao" onClick={() => setEscolhido(p.nome)}>
                  <b>{p.nome}</b>
                  <span>{p.descricao}</span>
                  {p.nome === sugestao.perfil && <Pill tom="acc">Sugerido</Pill>}
                </button>
              ))}
            </div>
          </div>
          <div className="btns">
            <button className="btn primary" type="button" onClick={confirmar} disabled={enviando}>
              {enviando ? "Salvando..." : `Confirmar perfil ${escolhido}`}
            </button>
            <button className="btn" type="button" onClick={() => setSugestao(null)} disabled={enviando}>
              Refazer respostas
            </button>
          </div>
          <Fontes fontes={quest.fontes} />
          <p className="hint">{AVISO_EDUCATIVO}</p>
        </div>
      </section>
    );
  }

  /* 2. Perfil já definido. */
  if (usuario.perfil && !refazendo) {
    const ajustou = usuario.perfil_sugerido && usuario.perfil_sugerido !== usuario.perfil;
    return (
      <section className="screen" aria-label="Meu perfil">
        <Topline titulo="Meu perfil" />
        <div className="card stack">
          <div className="row-between">
            <h2>Perfil de investimento</h2>
            <Pill tom="acc">{usuario.perfil}</Pill>
          </div>
          <div className="perfil-destaque">
            <span className="v">{usuario.perfil}</span>
            <p>{descricao(usuario.perfil)}</p>
          </div>
          {ajustou && (
            <p className="hint">
              O questionário sugeriu {usuario.perfil_sugerido}
              {usuario.pontuacao != null && ` (${usuario.pontuacao} pontos)`}, e você escolheu {usuario.perfil}.
            </p>
          )}
          <div>
            <h3>Como o perfil muda o app</h3>
            <ul className="lista">
              <li>O Simulador abre com meta de lucro, queda para comprar e stop-loss do seu perfil.</li>
              <li>Você recebe um aviso ao escolher moedas de risco acima do habitual para o perfil.</li>
              <li>A leitura rápida da Visão geral ganha uma orientação adaptada ao perfil.</li>
            </ul>
          </div>
          <div className="btns">
            <button className="btn" type="button" onClick={() => setRefazendo(true)}>
              Refazer questionário
            </button>
          </div>
          <Fontes fontes={quest.fontes} />
          <p className="hint">{AVISO_EDUCATIVO}</p>
        </div>
      </section>
    );
  }

  /* 3. Questionário. */
  return (
    <section className="screen" aria-label="Meu perfil">
      <Topline titulo="Meu perfil">
        {refazendo && (
          <button className="btn" type="button" onClick={() => setRefazendo(false)}>Cancelar</button>
        )}
      </Topline>
      <form className="stack" onSubmit={enviar}>
        <div className="card stack">
          <div>
            <h2>Vamos definir seu perfil de investimento</h2>
            <p className="hint">
              São {total} perguntas rápidas. Com elas sugerimos um perfil, e você confirma ou ajusta no fim.
            </p>
          </div>
          <div className="progresso" role="progressbar" aria-valuemin={0} aria-valuemax={total}
            aria-valuenow={respondidas} aria-label="Perguntas respondidas">
            <i style={{ width: `${(respondidas / total) * 100}%` }} />
          </div>
          <span className="hint">{respondidas} de {total} respondidas</span>
        </div>

        {quest.perguntas.map((p, i) => (
          <fieldset className="card pergunta" key={p.id}>
            <legend>{i + 1}. {p.texto}</legend>
            {p.base && <span className="hint base">Base: {p.base}</span>}
            <div className="opcoes">
              {p.opcoes.map((texto, idx) => (
                <label key={idx} className="opcao" data-marcada={respostas[p.id] === idx}>
                  <input type="radio" name={p.id} checked={respostas[p.id] === idx}
                    onChange={() => setRespostas((r) => ({ ...r, [p.id]: idx }))} />
                  <span>{texto}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ))}

        <div className="btns">
          <button className="btn primary" type="submit" disabled={respondidas < total || enviando}>
            {enviando ? <Spinner /> : "Ver meu perfil sugerido"}
          </button>
          {respondidas < total && (
            <span className="hint">Responda todas as perguntas para continuar.</span>
          )}
        </div>
        <Fontes fontes={quest.fontes} />
        <p className="hint">{AVISO_EDUCATIVO}</p>
      </form>
    </section>
  );
}
