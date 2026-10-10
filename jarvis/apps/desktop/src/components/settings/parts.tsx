import { type FormEvent, useState } from "react";

import { ApiError } from "../../lib/api";
import { Button, cx } from "../ui/primitives";

/** Building blocks shared by the Settings groups. */

export interface KeyFormProps {
  connect: (key: string) => Promise<unknown>;
  label: string;
  placeholder: string;
  provider: { name: string; url: string; host: string };
  /** Where the key is kept, shown under the form. */
  storage: React.ReactNode;
  testid: string;
  onDone: () => void;
  onCancel?: () => void;
}

export function KeyForm({ connect, label, placeholder, provider, storage, testid, onDone, onCancel }: KeyFormProps) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; suggestion: string | null } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await connect(key);
      setKey("");
      onDone();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { message: err.message, suggestion: err.suggestion }
          : { message: "Something went wrong.", suggestion: null },
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="min-w-0 space-y-2.5" data-testid={`${testid}-form`}>
      <div className="flex gap-2">
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          aria-label={label}
          data-testid={`${testid}-input`}
          className={cx(
            "no-drag h-8 min-w-0 flex-1 rounded-lg border bg-surface px-3 font-mono text-xs text-fg",
            "placeholder:text-fg-faint focus:border-accent/50 focus:outline-none",
            error ? "border-danger/40" : "border-hairline-strong",
          )}
        />
        <Button type="submit" variant="primary" disabled={busy || !key.trim()} data-testid={`${testid}-connect`}>
          {busy ? "Checking…" : "Connect"}
        </Button>
        {onCancel && (
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        )}
      </div>
      {error ? (
        <p className="text-xs leading-relaxed text-danger" data-testid={`${testid}-error`}>
          {error.message}
          {error.suggestion && <span className="text-fg-muted"> {error.suggestion}</span>}
        </p>
      ) : (
        <p className="text-xs leading-relaxed text-fg-faint">
          Create one at{" "}
          <a
            href={provider.url}
            target="_blank"
            rel="noreferrer"
            className="no-drag text-fg-muted underline decoration-hairline-strong underline-offset-2 hover:text-fg"
          >
            {provider.host}
          </a>
          . JARVIS checks it with {provider.name}, then keeps it only {storage}. No restart needed.
        </p>
      )}
    </form>
  );
}

export function Toggle({
  checked,
  onChange,
  testid,
  children,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <span className="flex items-center gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        data-testid={testid}
        className={cx(
          "no-drag relative h-4 w-7 shrink-0 rounded-full transition-colors",
          checked ? "bg-accent/80" : "bg-hairline-strong",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 size-3 rounded-full bg-fg transition-transform",
            checked ? "translate-x-3.5" : "translate-x-0.5",
          )}
        />
      </button>
      <span className="truncate text-fg-muted">{children}</span>
    </span>
  );
}

export function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="label mb-3">{title}</h2>
      <dl className="divide-y divide-hairline border-y border-hairline">{children}</dl>
    </section>
  );
}

export function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-4 py-2.5 text-[13px]">
      <dt className="text-fg-faint capitalize">{label}</dt>
      <dd className="selectable truncate text-fg">{children}</dd>
    </div>
  );
}
