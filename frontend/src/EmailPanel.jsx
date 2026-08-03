import { useState } from "react";
import { generateEmail, reviseEmail } from "./api";
import { FLOWS } from "./flows";

export default function EmailPanel() {
  const [flowName, setFlowName] = useState(FLOWS[0].value);
  const [firstName, setFirstName] = useState("Marcus");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // versions[0] is the original AI draft; each later entry is a human-feedback
  // revision. The last entry is always "current".
  const [versions, setVersions] = useState([]);

  const [feedbackText, setFeedbackText] = useState("");
  const [revising, setRevising] = useState(false);
  const [reviseError, setReviseError] = useState(null);

  async function handleGenerate(e) {
    e.preventDefault();
    if (!firstName.trim()) return;
    setLoading(true);
    setError(null);
    setVersions([]);
    setFeedbackText("");
    setReviseError(null);
    try {
      const data = await generateEmail(flowName, firstName.trim());
      setVersions([{ kind: "initial", ...data }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleRevise(e) {
    e.preventDefault();
    if (!feedbackText.trim() || revising || versions.length === 0) return;
    setRevising(true);
    setReviseError(null);
    try {
      const latest = versions[versions.length - 1];
      const feedbackHistory = versions.filter((v) => v.kind === "revision").map((v) => v.feedback);
      const data = await reviseEmail(
        flowName,
        firstName.trim(),
        feedbackText.trim(),
        latest.rendered_text,
        feedbackHistory
      );
      setVersions((prev) => [...prev, { kind: "revision", ...data }]);
      setFeedbackText("");
    } catch (err) {
      setReviseError(err.message);
    } finally {
      setRevising(false);
    }
  }

  const current = versions.length > 0 ? versions[versions.length - 1] : null;
  const olderVersions = versions.length > 1 ? versions.slice(0, -1) : [];

  return (
    <div className="panel">
      <form className="email-form" onSubmit={handleGenerate}>
        <div className="field">
          <label htmlFor="flow">Flow</label>
          <select id="flow" value={flowName} onChange={(e) => setFlowName(e.target.value)}>
            {FLOWS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="firstName">Customer first name</label>
          <input
            id="firstName"
            type="text"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            placeholder="e.g. Marcus"
          />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? "Generating…" : "Generate"}
        </button>
      </form>

      {error && <div className="error-box">{error}</div>}

      {current && (
        <div className="result">
          <VersionStatus version={current} versionNumber={versions.length} />

          <EmailCard content={current.email} firstName={firstName} />

          {current.kind === "initial" && (
            <details className="sweeper-history" open>
              <summary>
                Sweeper pass/fail history ({current.attempts.length} attempt
                {current.attempts.length > 1 ? "s" : ""})
              </summary>
              <ol>
                {current.attempts.map((a) => (
                  <li key={a.attempt} className={a.sweeper_pass ? "attempt pass" : "attempt fail"}>
                    <div className="attempt-header">
                      <span className="attempt-num">Attempt {a.attempt}</span>
                      <span className={`badge ${a.sweeper_pass ? "badge-pass" : "badge-fail"}`}>
                        {a.sweeper_pass ? "PASS" : "FAIL"}
                      </span>
                      <span className="severity">severity: {a.sweeper_severity}</span>
                    </div>
                    <div className="attempt-changed">what changed: {a.what_changed}</div>
                    {a.sweeper_reasons.length > 0 && (
                      <ul className="reasons">
                        {a.sweeper_reasons.map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ol>
            </details>
          )}

          <form className="feedback-box" onSubmit={handleRevise}>
            <label htmlFor="feedback">Give feedback on this draft</label>
            <textarea
              id="feedback"
              rows={3}
              value={feedbackText}
              onChange={(e) => setFeedbackText(e.target.value)}
              placeholder="e.g. Make the tone a bit warmer, or mention the doctor by name…"
            />
            <button type="submit" disabled={revising || !feedbackText.trim()}>
              {revising ? "Regenerating…" : "Regenerate with feedback"}
            </button>
          </form>

          {reviseError && <div className="error-box">{reviseError}</div>}

          {olderVersions.length > 0 && (
            <details className="previous-versions" open>
              <summary>
                Previous draft{olderVersions.length > 1 ? "s" : ""} ({olderVersions.length})
              </summary>
              {[...olderVersions].reverse().map((v, i) => {
                const versionNumber = olderVersions.length - i;
                return (
                  <div className="version-block" key={versionNumber}>
                    <VersionStatus version={v} versionNumber={versionNumber} muted />
                    <EmailCard content={v.email} firstName={firstName} muted />
                  </div>
                );
              })}
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function VersionStatus({ version, versionNumber, muted }) {
  if (version.kind === "initial") {
    return (
      <div className={muted ? "version-status muted" : "version-status"}>
        <span className="version-label">Draft {versionNumber} — original AI draft</span>
        {version.needs_human_review && (
          <div className="warning-box">
            ⚠ Needs human review — the automatic revision loop used all {version.retries_used}{" "}
            retries and still didn't pass brand QA.
          </div>
        )}
        {!version.needs_human_review && version.retries_used > 0 && (
          <div className="info-box">
            ✓ Passed brand QA after {version.retries_used} automatic revision
            {version.retries_used > 1 ? "s" : ""}.
          </div>
        )}
        {!version.needs_human_review && version.retries_used === 0 && (
          <div className="info-box">✓ Passed brand QA on the first draft.</div>
        )}
      </div>
    );
  }

  return (
    <div className={muted ? "version-status muted" : "version-status"}>
      <span className="version-label">
        Draft {versionNumber} — revised based on your feedback: <em>“{version.feedback}”</em>
      </span>
      <div className={version.sweeper_pass ? "info-box" : "warning-box"}>
        {version.sweeper_pass ? "✓ Still passes brand QA." : "⚠ Brand QA check on this revision:"}
        {!version.sweeper_pass && (
          <ul className="reasons">
            {version.sweeper_reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function EmailCard({ content, firstName, muted }) {
  return (
    <div className={muted ? "email-card muted" : "email-card"}>
      {content.hero_image_url && (
        <div className="hero-block">
          <img className="hero-image" src={content.hero_image_url} alt={content.hero_headline || content.hero} />
          {content.hero_headline && <div className="hero-headline">{content.hero_headline}</div>}
        </div>
      )}
      <div className="email-card-meta">
        <div>
          <strong>Subject:</strong> {content.subject}
        </div>
        <div className="preheader">{content.preheader}</div>
      </div>
      <div className="email-card-body">
        <p>Hi {firstName},</p>
        {content.opening_lines.map((line, i) => (
          <p key={i}>{line.replace("[name]", firstName)}</p>
        ))}

        {content.what_happens_next && content.what_happens_next.length > 0 && (
          <div className="steps-block">
            <div className="steps-title">What happens next</div>
            <ol>
              {content.what_happens_next.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </div>
        )}

        {content.gentle_truth_line && <p>{content.gentle_truth_line}</p>}

        <div className="cta-row">
          <button type="button" className="cta-button" disabled>
            {content.cta_text}
          </button>
        </div>

        {content.trust_line && <div className="trust-line">{content.trust_line}</div>}

        <p className="signature">The andSons team</p>

        <div className="footer-block">
          <div>WhatsApp customer service</div>
          <div>andSons Pte. Ltd., 1 Fusionopolis Place, #17-10, Galaxis, Singapore 138522</div>
          <div>Unsubscribe</div>
        </div>
      </div>
    </div>
  );
}
