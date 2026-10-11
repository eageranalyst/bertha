"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { EnvSetting, Model, OfferedModel, Provider, ProvidersInfo } from "@/lib/types";
import { SaveBar, useUnsavedWarning } from "@/components/SaveBar";

type Editable = Pick<Model, "tier" | "description" | "enabled"> &
  Partial<Pick<Model, "context_length" | "price_in" | "price_out">>;
const FIELDS: (keyof Editable)[] = ["tier", "description", "enabled"];
// A local model's context is what its server reports, and it's free.
const HOSTED_FIELDS: (keyof Editable)[] = [...FIELDS, "context_length", "price_in", "price_out"];

function fieldsOf(m: Model): (keyof Editable)[] {
  return m.provider === "local" ? FIELDS : HOSTED_FIELDS;
}

function editable(m: Model): Editable {
  return Object.fromEntries(fieldsOf(m).map((f) => [f, m[f]])) as Editable;
}

const TIER_HINT: Record<Model["tier"], string> = {
  light: "Quick, cheap work: summaries, simple edits, lookups.",
  standard: "Everyday coding and writing.",
  strong: "Hard tasks: multi-file changes, careful reasoning.",
};

function formatContext(tokens: number | null): string {
  if (!tokens) return "context unknown";
  return tokens >= 1000 ? `${Math.round(tokens / 1000)}k context` : `${tokens} context`;
}

function formatPrice(m: Pick<Model, "price_in" | "price_out">): string | null {
  if (m.price_in == null && m.price_out == null) return null;
  const money = (v: number | null) => (v == null ? "?" : `$${v < 1 ? v.toFixed(2) : v.toFixed(v % 1 ? 2 : 0)}`);
  return `${money(m.price_in)} in · ${money(m.price_out)} out per M tokens`;
}

/** A number input's text as a value: blank is null. */
function parseNumber(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

export default function ModelsPage() {
  const [models, setModels] = useState<Model[] | null>(null);
  const [info, setInfo] = useState<ProvidersInfo | null>(null);
  // Your edits, by model key, applied when you save.
  const [drafts, setDrafts] = useState<Record<string, Editable>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The provider whose model picker is open.
  const [adding, setAdding] = useState<string | null>(null);

  const show = useCallback((list: Model[]) => {
    setModels(list);
    setDrafts(Object.fromEntries(list.map((m) => [m.key, editable(m)])));
  }, []);

  /** A changed model list that keeps your unsaved edits to the models still in it. */
  const merge = useCallback((list: Model[]) => {
    setModels(list);
    setDrafts((d) => Object.fromEntries(list.map((m) => [m.key, d[m.key] ?? editable(m)])));
  }, []);

  const load = useCallback(async () => {
    try {
      const [list, providers] = await Promise.all([api.listModels(), api.listProviders()]);
      show(list);
      setInfo(providers);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [show]);

  useEffect(() => {
    load();
  }, [load]);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      merge(await api.refreshModels());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function add(provider: string, modelId: string) {
    setError(null);
    await api.addModel(provider, modelId);
    merge(await api.listModels());
  }

  async function remove(model: Model) {
    if (!window.confirm(`Remove ${model.key}? Sessions on it move to the default model.`)) return;
    setError(null);
    try {
      await api.removeModel(model.key);
      merge(await api.listModels());
    } catch (err) {
      setError((err as Error).message);
    }
  }

  /** What changed for each model: only the fields you edited. */
  const changes = (models ?? [])
    .map((m) => {
      const draft = drafts[m.key];
      const patch: Partial<Editable> = {};
      if (draft) for (const f of fieldsOf(m)) if (draft[f] !== m[f]) Object.assign(patch, { [f]: draft[f] });
      return [m.key, patch] as const;
    })
    .filter(([, patch]) => Object.keys(patch).length > 0);
  const dirty = changes.length > 0;
  useUnsavedWarning(dirty);

  function edit(key: string, patch: Partial<Editable>) {
    setDrafts((d) => ({ ...d, [key]: { ...d[key], ...patch } }));
  }

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      for (const [key, patch] of changes) await api.updateModel(key, patch);
      show(await api.listModels());
      setSaved(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const providers = info?.providers ?? [];
  const ready = providers.filter((p) => p.configured);
  const notSetUp = providers.filter((p) => !p.configured);

  return (
    <div className="models-page">
      <p className="skill-hint">
        Where Bertha&apos;s models come from, and which ones it uses. A provider&apos;s key and address
        live in <code>.env</code> and are only shown here; change them there and restart. Which
        models are used, their tier, and for hosted ones their context and price, are set here.
        Routing picks a model for each turn&apos;s tier: a loaded local model first, then the
        cheapest hosted one. Agents never see an API key; their calls to hosted models go through
        Bertha, which only allows the models enabled here.
      </p>

      {info && <EnvPanel title="Model settings in .env" settings={info.general} />}

      <SaveBar dirty={dirty} busy={busy} saved={saved} onSave={save} onDiscard={() => models && show(models)} />
      {error && <div className="wiki-error">{error}</div>}
      {(models === null || info === null) && !error && <div className="empty-state">Loading…</div>}

      {models &&
        ready.map((provider) => {
          const own = models.filter((m) => m.provider === provider.id);
          return (
            <section key={provider.id} className="provider-card">
              <ProviderHeader provider={provider}>
                {provider.hosted ? (
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setAdding(adding === provider.id ? null : provider.id)}
                  >
                    {adding === provider.id ? "Close" : "Add model"}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn-ghost"
                    disabled={busy}
                    onClick={refresh}
                  >
                    Refresh from model server
                  </button>
                )}
              </ProviderHeader>
              <EnvList settings={provider.env} />
              {adding === provider.id && (
                <ModelPicker
                  provider={provider}
                  added={new Set(own.map((m) => m.model_id))}
                  onAdd={(id) => add(provider.id, id)}
                />
              )}
              {own.length === 0 ? (
                <div className="provider-empty">
                  {provider.hosted
                    ? "No models added yet. Add the ones you want Bertha to use."
                    : "No models yet. Start the model server and refresh."}
                </div>
              ) : (
                <div className="models-list">
                  {own.map((model) => (
                    <ModelRow
                      key={model.key}
                      model={{ ...model, ...drafts[model.key] }}
                      hosted={provider.hosted}
                      onEdit={(patch) => edit(model.key, patch)}
                      onRemove={() => remove(model)}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}

      {models && notSetUp.length > 0 && (
        <section className="provider-card provider-off">
          <h3 className="provider-section-title">Not set up</h3>
          <p className="settings-help">
            Add a provider&apos;s key to <code>.env</code> and restart Bertha to use its models.
          </p>
          {notSetUp.map((provider) => (
            <div key={provider.id} className="provider-off-row">
              <div className="settings-main">
                <span className="settings-label">{provider.label}</span>
                <span className="settings-help">{provider.blurb}</span>
              </div>
              <code className="provider-env-name">{provider.env[0]?.name}</code>
            </div>
          ))}
        </section>
      )}

      {dirty && <SaveBar dirty={dirty} busy={busy} saved={saved} onSave={save} onDiscard={() => models && show(models)} />}
    </div>
  );
}

function ProviderHeader({ provider, children }: { provider: Provider; children?: React.ReactNode }) {
  return (
    <div className="provider-header">
      <div className="settings-main">
        <div className="provider-title">
          <span className="settings-label">{provider.label}</span>
          <span className={"status " + (provider.hosted ? "provider-hosted" : "provider-local")}>
            {provider.hosted ? "Hosted" : "Local · free"}
          </span>
        </div>
        <span className="settings-help">{provider.blurb}</span>
      </div>
      <div className="settings-actions">{children}</div>
    </div>
  );
}

/** .env settings, read-only. */
function EnvList({ settings }: { settings: EnvSetting[] }) {
  return (
    <dl className="provider-env">
      {settings.map((s) => (
        <div key={s.name} className="provider-env-row">
          <dt className="provider-env-name">{s.name}</dt>
          <dd className={s.set ? "" : "provider-env-unset"}>
            {s.value ?? (s.set ? "set" : "not set")}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function EnvPanel({ title, settings }: { title: string; settings: EnvSetting[] }) {
  return (
    <section className="provider-card">
      <h3 className="provider-section-title">{title}</h3>
      <EnvList settings={settings} />
    </section>
  );
}

function ModelRow({
  model: m,
  hosted,
  onEdit,
  onRemove,
}: {
  model: Model;
  hosted: boolean;
  onEdit: (patch: Partial<Editable>) => void;
  onRemove: () => void;
}) {
  const price = formatPrice(m);
  return (
    <div className={"models-row" + (m.enabled ? "" : " disabled")}>
      <label className="models-enabled">
        <input type="checkbox" checked={m.enabled} onChange={(e) => onEdit({ enabled: e.target.checked })} />
      </label>
      <div className="models-main">
        <div className="models-top">
          <span className="models-key">{m.key}</span>
          {m.model_id !== m.key && <span className="ref">{m.model_id}</span>}
        </div>
        <input
          className="models-description"
          type="text"
          value={m.description}
          placeholder="What is this model good at?"
          onChange={(e) => onEdit({ description: e.target.value })}
        />
        {hosted ? (
          <div className="models-numbers">
            <label>
              Context
              <input
                type="number"
                min={0}
                step={1000}
                value={m.context_length ?? ""}
                placeholder="unknown"
                onChange={(e) => onEdit({ context_length: parseNumber(e.target.value) })}
              />
            </label>
            <label>
              $ in / M
              <input
                type="number"
                min={0}
                step={0.01}
                value={m.price_in ?? ""}
                placeholder="?"
                onChange={(e) => onEdit({ price_in: parseNumber(e.target.value) })}
              />
            </label>
            <label>
              $ out / M
              <input
                type="number"
                min={0}
                step={0.01}
                value={m.price_out ?? ""}
                placeholder="?"
                onChange={(e) => onEdit({ price_out: parseNumber(e.target.value) })}
              />
            </label>
            <button type="button" className="btn-ghost models-remove" onClick={onRemove}>
              Remove
            </button>
          </div>
        ) : (
          <span className="models-facts">
            {formatContext(m.context_length)}
            {m.loaded === 1 ? " · loaded" : m.loaded === 0 ? " · not loaded" : ""}
          </span>
        )}
        {hosted && !price && (
          <span className="models-facts">No price set, so routing tries priced models first.</span>
        )}
      </div>
      <div className="models-tier">
        <select value={m.tier} onChange={(e) => onEdit({ tier: e.target.value as Model["tier"] })}>
          {(Object.keys(TIER_HINT) as Model["tier"][]).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="models-tier-hint">{TIER_HINT[m.tier]}</span>
      </div>
    </div>
  );
}

/** Choose models to add from what a hosted provider offers. */
function ModelPicker({
  provider,
  added,
  onAdd,
}: {
  provider: Provider;
  added: Set<string>;
  onAdd: (modelId: string) => Promise<void>;
}) {
  const [offered, setOffered] = useState<OfferedModel[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .providerModels(provider.id)
      .then((list) => live && setOffered(list))
      .catch((err) => live && setError((err as Error).message));
    return () => {
      live = false;
    };
  }, [provider.id]);

  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (offered ?? []).filter((m) =>
    words.every((w) => m.model_id.toLowerCase().includes(w) || m.name.toLowerCase().includes(w)),
  );
  const LIMIT = 60;

  async function add(modelId: string) {
    setPending(modelId);
    setError(null);
    try {
      await onAdd(modelId);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="model-picker">
      <input
        type="search"
        value={query}
        autoFocus
        placeholder={`Search ${provider.label} models`}
        onChange={(e) => setQuery(e.target.value)}
      />
      {error && <div className="wiki-error">{error}</div>}
      {offered === null && !error && <div className="provider-empty">Asking {provider.label}…</div>}
      {offered && (
        <div className="model-picker-list">
          {matches.slice(0, LIMIT).map((m) => {
            const price = formatPrice(m);
            return (
              <div key={m.model_id} className="model-picker-row">
                <div className="models-main">
                  <span className="models-key">{m.model_id}</span>
                  <span className="models-facts">
                    {m.name !== m.model_id && `${m.name} · `}
                    {formatContext(m.context_length)}
                    {price && ` · ${price}`}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={added.has(m.model_id) || pending !== null}
                  onClick={() => add(m.model_id)}
                >
                  {added.has(m.model_id) ? "Added" : pending === m.model_id ? "Adding…" : "Add"}
                </button>
              </div>
            );
          })}
          {matches.length === 0 && <div className="provider-empty">No models match.</div>}
          {matches.length > LIMIT && (
            <div className="provider-empty">
              {matches.length - LIMIT} more. Search to narrow them down.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
