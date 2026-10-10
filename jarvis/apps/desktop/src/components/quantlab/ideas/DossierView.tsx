import type { QmDossier } from "@jarvis/protocol";
import { useEffect, useState } from "react";

import { ApiError, api } from "../../../lib/api";
import { Button } from "../../ui/primitives";
import { Card } from "../ui";
import { Markdown, download } from "../research/common";

/** The research dossier: source facts, test facts and interpretation kept apart; sealed hashes. */
export function DossierView({ missionId }: { missionId: string }) {
  const [dossier, setDossier] = useState<QmDossier | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .qmDossier(missionId)
      .then((d) => live && setDossier(d))
      .catch((e: unknown) => live && setError(e instanceof ApiError ? e.message : "The dossier isn't available."));
    return () => {
      live = false;
    };
  }, [missionId]);

  return (
    <Card
      title="Research dossier"
      aside={
        dossier && (
          <span className="flex gap-2">
            <Button onClick={() => setOpen(!open)} data-testid="qm-dossier-toggle">
              {open ? "Hide" : "Read"}
            </Button>
            <Button onClick={() => download(`dossier-${missionId}.md`, dossier.markdown)} data-testid="qm-dossier-md">
              Markdown
            </Button>
            <Button
              onClick={() => download(`dossier-${missionId}.json`, JSON.stringify(dossier.json, null, 2), "application/json")}
              data-testid="qm-dossier-json"
            >
              JSON
            </Button>
          </span>
        )
      }
      testId="qm-dossier"
    >
      {error && <p className="text-[13px] text-fg-muted">{error}</p>}
      {dossier && !open && (
        <p className="text-[13px] text-fg-muted">
          What the source says, what was tested and what it means — three separate parts, every number traceable to a
          sealed run. Local file; nothing is shared.
        </p>
      )}
      {dossier && open && (
        <div className="max-h-[720px] overflow-y-auto pr-2" data-testid="qm-dossier-text">
          <Markdown text={dossier.markdown} />
        </div>
      )}
    </Card>
  );
}
