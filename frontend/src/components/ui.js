import { MOEDAS } from "../constants";

/** Controle segmentado: um botão marcado por vez. */
export function Seg({ value, options, onChange, label }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.valor}
          type="button"
          aria-pressed={value === o.valor}
          onClick={() => onChange(o.valor)}
        >
          {o.rotulo}
        </button>
      ))}
    </div>
  );
}

export function Pill({ tom, children }) {
  return <span className={`pill ${tom || ""}`}>{children}</span>;
}

export function Switch({ ligado, onChange, titulo, descricao }) {
  return (
    <div className="sw">
      <div className="t">
        <b>{titulo}</b>
        {descricao && <span>{descricao}</span>}
      </div>
      <button
        type="button"
        aria-pressed={ligado}
        aria-label={titulo}
        onClick={() => onChange(!ligado)}
      />
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Carregando" />;
}

/** Cabeçalho de cada tela: título, seletor de moeda e ações à direita. */
export function Topline({ titulo, moeda, setMoeda, children }) {
  return (
    <div className="topline">
      <div className="coin">
        <h1>{titulo}</h1>
        {setMoeda && (
          <select
            className="sel"
            aria-label="Moeda"
            value={moeda}
            onChange={(e) => setMoeda(e.target.value)}
          >
            {MOEDAS.map((m) => (
              <option key={m.sigla} value={m.sigla}>
                {m.nome} ({m.sigla})
              </option>
            ))}
          </select>
        )}
      </div>
      {children && <div className="tools">{children}</div>}
    </div>
  );
}

export function Toasts({ erro, ok, aviso, fechar }) {
  const itens = [
    ["erro", erro],
    ["ok", ok],
    ["aviso", aviso],
  ].filter(([, texto]) => texto);
  if (itens.length === 0) return null;
  return (
    <div className="toasts" role="status">
      {itens.map(([tipo, texto]) => (
        <div key={tipo} className={`toast ${tipo}`}>
          <span>{texto}</span>
          <button type="button" aria-label="Fechar aviso" onClick={() => fechar(tipo)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

/** Seção recolhível (tabelas longas). */
export function Recolhivel({ titulo, aberto = false, children }) {
  return (
    <details open={aberto}>
      <summary>{titulo}</summary>
      <div className="body">{children}</div>
    </details>
  );
}

export function Vazio({ titulo, texto }) {
  return (
    <div className="vazio">
      <b>{titulo}</b>
      {texto && <span>{texto}</span>}
    </div>
  );
}

/** Tooltip dos gráficos, no mesmo estilo do resto da interface. */
export function Dica({ active, payload, label, formatar }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="tip">
      <span className="hint">{label}</span>
      {payload.map((p, i) => (
        <span key={i}>
          {p.name}: <b>{formatar ? formatar(p.value, p) : p.value}</b>
        </span>
      ))}
    </div>
  );
}

export const corSentimento = (s) =>
  s === "positivo" ? "pos" : s === "negativo" ? "neg" : "";

export const rotuloSentimento = (s) =>
  s === "positivo"
    ? "Positivo"
    : s === "negativo"
      ? "Negativo"
      : s === "nulo"
        ? "Fora de cripto"
        : "Neutro";
