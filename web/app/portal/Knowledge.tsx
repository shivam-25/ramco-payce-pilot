"use client";

import { useEffect, useState } from "react";

type Doc = { type: string; id: string; chunks: number; usable: number;
             from: string; to: string };
type Rule = { id: string; name: string; value: string; from: string };
type KB = { documents: Doc[]; indexed_chunks: number;
            empty_kb_articles: number; rules: Rule[]; strategy: string };

const TYPE_LABEL: Record<string, string> = {
  process: "Process document", statutory: "Statutory rule",
  kb: "Knowledge-base article", known_issue: "Known issue",
};

export default function Knowledge() {
  const [kb, setKb] = useState<KB | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    fetch("/api/portal", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: "knowledge" }) })
      .then((r) => r.json())
      .then((d) => d.error ? setErr(d.error)
        : d.documents ? setKb(d)
        : setErr("The server replied without a corpus. Check the portal route."))
      .catch(() => setErr("Could not load the knowledge base."));
  }, []);

  if (err) return <p className="perr">{err}</p>;
  if (!kb) return <p className="pmuted">Loading the corpus…</p>;

  const byType = kb.documents.reduce<Record<string, Doc[]>>((acc, d) => {
    (acc[d.type] ||= []).push(d); return acc;
  }, {});

  return (
    <div className="kwrap">
      <p className="pmuted">
        Everything the pilot is allowed to answer from. If a question is not
        covered here it is refused out loud rather than guessed at.
      </p>

      <div className="ktiles">
        <div className="ktile">
          <p className="knum">{kb.indexed_chunks}</p>
          <p className="klab">indexed chunks<br />
            <span className="mono">{kb.strategy}</span></p>
        </div>
        <div className="ktile">
          <p className="knum">{kb.documents.length}</p>
          <p className="klab">sources with text<br />
            <span className="mono">retrievable</span></p>
        </div>
        <div className="ktile warn">
          <p className="knum">{kb.empty_kb_articles}</p>
          <p className="klab">articles with no body<br />
            <span className="mono">pointers to Confluence</span></p>
        </div>
      </div>

      {kb.empty_kb_articles > 0 && (
        <p className="knote">
          <b>The gap is in the corpus, not the pipeline.</b>{" "}
          {kb.empty_kb_articles} knowledge-base articles exist as index entries
          whose bodies live in Confluence, so there is no text to retrieve.
          They are shown rather than hidden, because the coverage ceiling is
          a property of what is written down.
        </p>
      )}

      {Object.entries(byType).map(([type, docs]) => (
        <section key={type}>
          <p className="dh">{TYPE_LABEL[type] || type}</p>
          <ul className="klist">
            {docs.map((d) => (
              <li key={d.id}>
                <span className="kid">{d.id}</span>
                <span className="kch">{d.chunks} chunk{d.chunks === 1 ? "" : "s"}</span>
                {d.from && <span className="kdate">
                  in force from {d.from}{d.to ? ` to ${d.to}` : ""}</span>}
                {d.usable === 0 && <span className="kstub">no usable text</span>}
              </li>
            ))}
          </ul>
        </section>
      ))}

      {!!kb.rules.length && (
        <section>
          <p className="dh">Statutory rules, with the date each took effect</p>
          <ul className="klist">
            {kb.rules.map((r) => (
              <li key={r.id}>
                <span className="kid">{r.id}</span>
                <span className="kname">{r.name}</span>
                <span className="kval">{r.value}</span>
                <span className="kdate">from {r.from}</span>
              </li>
            ))}
          </ul>
          <p className="knote">
            Every answer is filtered against these dates before ranking, which
            is why a June question gets June&rsquo;s rule and a September
            question gets September&rsquo;s.
          </p>
        </section>
      )}
    </div>
  );
}
