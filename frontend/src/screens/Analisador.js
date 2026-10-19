import { useState } from "react";
import { api } from "../api";
import { numero } from "../format";
import { Pill, Topline, corSentimento, rotuloSentimento } from "../components/ui";

export default function Analisador({ moeda, notify }) {
  const [texto, setTexto] = useState("");
  const [resultado, setResultado] = useState(null);
  const [analisando, setAnalisando] = useState(false);

  const analisar = async () => {
    if (!texto.trim()) return;
    setAnalisando(true);
    setResultado(null);
    try {
      setResultado(await api.analisarTexto(texto, moeda));
    } catch (e) {
      notify.erro(`Falha ao analisar o texto: ${e.message}`);
    } finally {
      setAnalisando(false);
    }
  };

  return (
    <section className="screen" aria-label="Analisador">
      <Topline titulo="Analisador" />

      <div className="card stack">
        <div>
          <h2>Análise de texto</h2>
          <p className="hint">
            Cole uma notícia, um tweet ou um comentário e o modelo de sentimento classifica o texto.
          </p>
        </div>
        <div className="field">
          <label htmlFor="texto-analise">Texto</label>
          <textarea
            id="texto-analise"
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            placeholder="Cole aqui uma notícia, tweet ou comentário do Reddit..."
            maxLength={10000}
          />
        </div>
        <div className="btns">
          <button className="btn primary" type="button" onClick={analisar}
            disabled={analisando || !texto.trim()}>
            {analisando ? "Analisando..." : "Analisar texto"}
          </button>
        </div>

        {resultado && (
          <div className="analise-res" aria-live="polite">
            <Pill tom={corSentimento(resultado.sentimento)}>
              {rotuloSentimento(resultado.sentimento)}
            </Pill>
            <div className="stats">
              <span>Índice <b>{resultado.indice != null ? numero(resultado.indice, 2) : "—"}</b></span>
              <span>Confiança <b>{resultado.score_bert != null ? numero(resultado.score_bert, 2) : "—"}</b></span>
              <span>Rótulo <b>{resultado.label_bert ?? "—"}</b></span>
              <span>Fala de cripto <b>{resultado.crypto_relevante ? "sim" : "não"}</b></span>
            </div>
          </div>
        )}
      </div>

      <div className="card dash">
        <Pill>Em breve</Pill>
        <h2>Analisador de gráficos</h2>
        <p className="hint">
          Espaço reservado para a próxima funcionalidade. Esta área fica pronta para receber o
          analisador assim que ele for definido.
        </p>
      </div>
    </section>
  );
}
