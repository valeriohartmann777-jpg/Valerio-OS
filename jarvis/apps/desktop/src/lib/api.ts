import type {
  AiStatus,
  AiUsage,
  BotDetail,
  BotLabStatus,
  BotSettings,
  BrainStatus,
  BriefingStatus,
  JarvisEvent,
  LearningNote,
  LearningRound,
  LearningStatus,
  LearningStudy,
  LearningTest,
  MemoryItem,
  MemoryKind,
  VoiceStatus,
  ChatAccepted,
  Mission,
  PermissionRequest,
  QmAgent,
  QmDossier,
  QmEvent,
  QmMission,
  QmMissionRow,
  QmTrial,
  QsAudit,
  QsSource,
  QsSourceRow,
  QsSpeechModel,
  QlDataset,
  QlEquity,
  QlExperiment,
  QlExperimentRow,
  QlImportRequest,
  QlLedger,
  QlOverview,
  QlReproduction,
  QlSpecCheck,
  QlStrategy,
  QlStrategySpec,
  QhAudit,
  QhCacheRow,
  QhCaps,
  QhCatalog,
  QhDataset,
  QhJob,
  QhQuote,
  QhRequest,
  QhResolution,
  QhStatus,
  QrChart,
  QrCheck,
  QrCompare,
  QrDraft,
  QrOverview,
  QrRun,
  QrRunRow,
  QrSpec,
  QrStrategy,
  QrStrategyRow,
  QrTemplate,
  QrTradeDetail,
  QrTrades,
  SettingsView,
  TrainingStatus,
  UlAgent,
  UlApproval,
  UlArtifactContent,
  UlConfig,
  UlEvent,
  UlKnowledge,
  UlMission,
  UlMissionRow,
  UlOverview,
  UlProjectKind,
} from "@jarvis/protocol";

import { BACKEND_URL } from "./config";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly suggestion: string | null = null,
    readonly code: string | null = null,
  ) {
    super(message);
  }
}

interface ErrorDetail {
  code?: string;
  message?: string;
  suggestion?: string | null;
}

async function requestText(path: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}${path}`);
  } catch {
    throw new ApiError(0, "JARVIS backend is not reachable.");
  }
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return response.text();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "JARVIS backend is not reachable.");
  }
  if (!response.ok) {
    let detail: string | ErrorDetail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail && typeof body.detail === "object" && "message" in body.detail) {
        detail = body.detail as ErrorDetail;
      }
    } catch {
      /* keep status text */
    }
    if (typeof detail === "string") throw new ApiError(response.status, detail);
    throw new ApiError(
      response.status,
      detail.message ?? response.statusText,
      detail.suggestion ?? null,
      detail.code ?? null,
    );
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  chat: (text: string) => post<ChatAccepted>("/chat", { text }),
  pauseMission: (id: string) => post<Mission>(`/missions/${id}/pause`),
  resumeMission: (id: string) => post<Mission>(`/missions/${id}/resume`),
  stopMission: (id: string) => post<Mission>(`/missions/${id}/stop`),
  approve: (id: string, strongConfirmation = false) =>
    post<PermissionRequest>(`/permissions/${id}/approve`, { strong_confirmation: strongConfirmation }),
  reject: (id: string) => post<PermissionRequest>(`/permissions/${id}/reject`, {}),
  settings: () => request<SettingsView>("/settings"),
  conversation: (limit: number, before?: string) =>
    request<JarvisEvent[]>(
      `/conversation?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`,
    ),
  connectBrain: (apiKey: string) => post<BrainStatus>("/brain/key", { api_key: apiKey }),
  briefing: () => request<BriefingStatus>("/briefing"),
  briefingPreferences: (prefs: { enabled?: boolean; time?: string }) =>
    post<BriefingStatus>("/briefing/preferences", prefs),
  sendBriefing: () => post<{ text: string }>("/briefing/send"),
  addMemory: (text: string, kind: MemoryKind = "fact") => post<MemoryItem[]>("/memory", { text, kind }),
  forgetMemory: (number: number) => request<MemoryItem[]>(`/memory/${number}`, { method: "DELETE" }),
  startLearning: () => post<LearningStatus>("/learning/start"),
  stopLearning: () => post<LearningStatus>("/learning/stop"),
  learningTests: (limit = 50) => request<LearningTest[]>(`/learning/tests?limit=${limit}`),
  learningFindings: () => request<LearningTest[]>("/learning/tests?status=validated&limit=50"),
  learningNotes: () => request<LearningNote[]>("/learning/notes"),
  learningStudies: (limit = 30) => request<LearningStudy[]>(`/learning/studies?limit=${limit}`),
  setLearningFocus: (text: string) => post<LearningStatus>("/learning/focus", { text }),
  learningRounds: (limit = 20) => request<LearningRound[]>(`/learning/rounds?limit=${limit}`),
  trainNow: () => post<TrainingStatus>("/training/run"),
  bots: () => request<BotLabStatus>("/bots"),
  refreshBots: () => post<BotLabStatus>("/bots/refresh"),
  setUpBots: () => post<BotLabStatus>("/bots/setup"),
  openTestTerminal: () => post<{ opened: boolean }>("/bots/test-terminal"),
  bot: (name: string) => request<BotDetail>(`/bots/${encodeURIComponent(name)}`),
  importBot: (name: string) => post<BotDetail>(`/bots/${encodeURIComponent(name)}/import`),
  botSettings: (name: string, settings: Partial<BotSettings>) =>
    post<BotSettings>(`/bots/${encodeURIComponent(name)}/settings`, settings),
  botBacktest: (name: string, version: number, inputs: Record<string, string> = {}) =>
    post<{ started: boolean }>(`/bots/${encodeURIComponent(name)}/backtest`, { version, inputs }),
  botSource: (name: string, version: number) =>
    request<{ number: number; source: string; diff: string }>(
      `/bots/${encodeURIComponent(name)}/versions/${version}/source`,
    ),
  installBot: (name: string, version: number) =>
    post<{ path: string; hint: string }>(`/bots/${encodeURIComponent(name)}/versions/${version}/install`),
  improveBot: (name: string) => post<BotLabStatus>(`/bots/${encodeURIComponent(name)}/improve`),
  stopImproving: () => post<BotLabStatus>("/bots/improve/stop"),
  setTraining: (enabled: boolean) => post<TrainingStatus>("/training/preferences", { enabled }),
  connectVoice: (apiKey: string) => post<VoiceStatus>("/voice/key", { api_key: apiKey }),
  voicePreferences: (prefs: { wake_word?: boolean; speak_replies?: boolean }) =>
    post<VoiceStatus>("/voice/preferences", prefs),
  voiceListen: () => post<VoiceStatus>("/voice/listen"),
  voiceStop: () => post<VoiceStatus>("/voice/stop"),
  voiceTest: () => post<VoiceStatus>("/voice/test"),
  // QuantLab — research only; there is no order or broker endpoint.
  qlOverview: () => request<QlOverview>("/quantlab/overview"),
  qlStrategies: () => request<QlStrategy[]>("/quantlab/strategies"),
  qlValidate: (spec: QlStrategySpec) => post<QlSpecCheck>("/quantlab/strategies/validate", { spec }),
  qlCreateStrategy: (spec: QlStrategySpec) => post<QlStrategy>("/quantlab/strategies", { spec }),
  qlAddVersion: (id: string, spec: QlStrategySpec) =>
    post<QlStrategy>(`/quantlab/strategies/${encodeURIComponent(id)}/versions`, { spec }),
  qlDatasets: () => request<QlDataset[]>("/quantlab/datasets"),
  qlImport: (body: QlImportRequest) => post<QlDataset>("/quantlab/datasets/import", body),
  qlFixture: (name: string) => post<QlDataset>("/quantlab/datasets/fixture", { name }),
  qlExperiments: () => request<QlExperimentRow[]>("/quantlab/experiments"),
  qlRun: (strategyVersionId: string, datasetId: string) =>
    post<QlExperiment>("/quantlab/experiments", {
      strategy_version_id: strategyVersionId,
      dataset_id: datasetId,
    }),
  qlExperiment: (id: string) => request<QlExperiment>(`/quantlab/experiments/${encodeURIComponent(id)}`),
  qlLedger: (id: string) => request<QlLedger>(`/quantlab/experiments/${encodeURIComponent(id)}/trades`),
  qlEquity: (id: string, points = 800) =>
    request<QlEquity>(`/quantlab/experiments/${encodeURIComponent(id)}/equity?points=${points}`),
  qlCancel: (id: string) => post<QlExperiment>(`/quantlab/experiments/${encodeURIComponent(id)}/cancel`),
  // ULTRON — every control changes real backend state.
  ulOverview: () => request<UlOverview>("/ultron/overview"),
  ulMissions: () => request<UlMissionRow[]>("/ultron/missions"),
  ulMission: (id: string) => request<UlMission>(`/ultron/missions/${encodeURIComponent(id)}`),
  ulCreate: (goal: string, project: UlProjectKind, budgetUsd?: number) =>
    post<UlMission>("/ultron/missions", { goal, project, budget_usd: budgetUsd ?? null }),
  ulControl: (id: string, action: "pause" | "resume" | "cancel") =>
    post<UlMission>(`/ultron/missions/${encodeURIComponent(id)}/${action}`),
  ulAnswer: (id: string, text: string) =>
    post<UlMission>(`/ultron/missions/${encodeURIComponent(id)}/answer`, { text }),
  ulBudget: (id: string, usd: number) =>
    post<UlMission>(`/ultron/missions/${encodeURIComponent(id)}/budget`, { usd }),
  ulActivity: (missionId?: string, limit = 300) =>
    request<UlEvent[]>(
      `/ultron/activity?limit=${limit}${missionId ? `&mission=${encodeURIComponent(missionId)}` : ""}`,
    ),
  ulArtifact: (id: string) => request<UlArtifactContent>(`/ultron/artifacts/${encodeURIComponent(id)}`),
  ulAgents: () => request<UlAgent[]>("/ultron/agents"),
  ulKnowledge: () => request<UlKnowledge>("/ultron/knowledge"),
  ulDecide: (id: string, decision: "approve" | "reject") =>
    post<UlApproval>(`/ultron/approvals/${encodeURIComponent(id)}/${decision}`),
  ulPauseAll: () => post<{ paused: number }>("/ultron/pause-all"),
  ulStopAll: () => post<{ cancelled: number }>("/ultron/stop-all"),
  ulConfig: (values: Partial<Pick<UlConfig, "budget_usd_per_mission" | "max_parallel_workers" | "max_attempts">>) =>
    post<UlConfig>("/ultron/config", values),
  qlReproduce: (id: string) =>
    post<QlReproduction>(`/quantlab/experiments/${encodeURIComponent(id)}/reproduce`),

  // QuantLab Data Hub (Databento). The key goes in once and never comes back.
  qhStatus: () => request<QhStatus>("/quantlab/connections/databento/status"),
  qhConnect: (apiKey: string) => post<QhStatus>("/quantlab/connections/databento", { api_key: apiKey }),
  qhTest: () => post<QhStatus>("/quantlab/connections/databento/test"),
  qhDisconnect: () => request<QhStatus>("/quantlab/connections/databento", { method: "DELETE" }),
  qhCatalog: (dataset?: string) =>
    request<QhCatalog>(`/quantlab/data/catalog${dataset ? `?dataset=${encodeURIComponent(dataset)}` : ""}`),
  qhResolve: (dataset: string, symbols: string[], stypeIn: string, start: string, end: string) =>
    request<QhResolution>(
      `/quantlab/data/instruments?dataset=${encodeURIComponent(dataset)}&symbols=${encodeURIComponent(symbols.join(","))}` +
        `&stype_in=${encodeURIComponent(stypeIn)}&start=${start}&end=${end}`,
    ),
  qhQuote: (body: QhRequest) => post<QhQuote>("/quantlab/data/quote", body),
  qhQuotes: () => request<QhQuote[]>("/quantlab/data/quotes"),
  qhRejectQuote: (id: string) => post<QhQuote>(`/quantlab/data/quotes/${encodeURIComponent(id)}/reject`),
  qhApprove: (quoteId: string, maxBudgetUsd: number) =>
    post<QhJob>("/quantlab/data/requests", { quote_id: quoteId, max_budget_usd: maxBudgetUsd, confirm: true }),
  qhJobs: () => request<QhJob[]>("/quantlab/data/jobs"),
  qhJob: (id: string) => request<QhJob>(`/quantlab/data/jobs/${encodeURIComponent(id)}`),
  qhCancelJob: (id: string) => post<QhJob>(`/quantlab/data/jobs/${encodeURIComponent(id)}/cancel`),
  qhCache: () => request<QhCacheRow[]>("/quantlab/data/cache"),
  qhDatasets: () => request<QhDataset[]>("/quantlab/data/datasets"),
  qhDataset: (id: string) => request<QhDataset>(`/quantlab/data/datasets/${encodeURIComponent(id)}`),
  qhBuild: (body: { dataset: string; schema: string; stype_in: string; symbol: string; start: string; end: string }) =>
    post<QhDataset>("/quantlab/data/datasets", body),
  qhPreview: (id: string) =>
    request<{ points: { ts: number; close: number; instrument_id: number }[]; records: number }>(
      `/quantlab/data/datasets/${encodeURIComponent(id)}/preview`,
    ),
  qhCaps: () => request<QhCaps>("/quantlab/data/settings"),
  qhSetCaps: (caps: QhCaps) => post<QhCaps>("/quantlab/data/settings", caps),
  qhAudit: () => request<QhAudit[]>("/quantlab/data/audit"),

  // QuantLab futures research. Results come only from the deterministic engine.
  qrOverview: () => request<QrOverview>("/quantlab/research/overview"),
  qrTemplates: () => request<QrTemplate[]>("/quantlab/research/templates"),
  qrCheck: (spec: QrSpec) => post<QrCheck>("/quantlab/research/strategies/check", { spec }),
  qrInterpret: (text: string, current?: QrSpec) =>
    post<QrDraft>("/quantlab/research/interpret", { text, current: current ?? null }),
  qrStrategies: () => request<QrStrategyRow[]>("/quantlab/research/strategies"),
  qrStrategy: (id: string) => request<QrStrategy>(`/quantlab/research/strategies/${encodeURIComponent(id)}`),
  qrCreate: (spec: QrSpec, note?: string) => post<QrStrategy>("/quantlab/research/strategies", { spec, note }),
  qrCreateFromAi: (spec: QrSpec, note: string, parentId?: string) =>
    post<QrStrategy>("/quantlab/research/strategies/ai", { spec, note, parent_id: parentId ?? null }),
  qrAddVersion: (id: string, spec: QrSpec, note?: string, parentId?: string) =>
    post<QrStrategy>(`/quantlab/research/strategies/${encodeURIComponent(id)}/versions`, {
      spec,
      note,
      parent_id: parentId ?? null,
    }),
  qrNote: (id: string, kind: "note" | "decision", text: string, runId?: string) =>
    post<QrStrategy>(`/quantlab/research/strategies/${encodeURIComponent(id)}/notes`, {
      kind,
      text,
      run_id: runId ?? null,
    }),
  qrRuns: () => request<QrRunRow[]>("/quantlab/research/runs"),
  qrRun: (id: string) => request<QrRun>(`/quantlab/research/runs/${encodeURIComponent(id)}`),
  qrStart: (body: {
    version_id: string;
    dataset_id: string;
    kind: "backtest" | "validation";
    include_holdout?: boolean;
    confirm_holdout?: boolean;
  }) => post<QrRun>("/quantlab/research/runs", body),
  qrCancel: (id: string) => post<QrRun>(`/quantlab/research/runs/${encodeURIComponent(id)}/cancel`),
  qrReproduce: (id: string) =>
    post<{ identical: boolean; trades: number; results_sha256: string }>(
      `/quantlab/research/runs/${encodeURIComponent(id)}/reproduce`,
    ),
  qrTrades: (id: string, offset = 0, limit = 200, segment?: string) =>
    request<QrTrades>(
      `/quantlab/research/runs/${encodeURIComponent(id)}/trades?offset=${offset}&limit=${limit}` +
        (segment ? `&segment=${segment}` : ""),
    ),
  qrTrade: (id: string, number: number, segment?: string) =>
    request<QrTradeDetail>(
      `/quantlab/research/runs/${encodeURIComponent(id)}/trades/${number}${segment ? `?segment=${segment}` : ""}`,
    ),
  qrChart: (id: string) => request<QrChart>(`/quantlab/research/runs/${encodeURIComponent(id)}/chart`),
  qrReport: (id: string) => requestText(`/quantlab/research/runs/${encodeURIComponent(id)}/report`),
  qrCompare: (ids: string[]) =>
    request<QrCompare>(`/quantlab/research/compare?ids=${encodeURIComponent(ids.join(","))}`),
  qlStopAll: () => post<{ runs: string[]; downloads: string[] }>("/quantlab/stop-all"),
  qsSources: () => request<QsSourceRow[]>("/quantlab/sources"),
  qsSource: (id: string) => request<QsSource>(`/quantlab/sources/${encodeURIComponent(id)}`),
  qsIntakeText: (text: string, note?: string) =>
    post<QsSourceRow>("/quantlab/sources/intake", { text, note: note || null }),
  qsIntakeLink: (url: string, note?: string) =>
    post<QsSourceRow>("/quantlab/sources/intake", { url, note: note || null }),
  qsUpload: (file: File, opts: { note?: string; language?: string; linkSource?: string } = {}) => {
    // Raw body: the backend streams it to disk with a size cap; header values are percent-encoded.
    const headers: Record<string, string> = {
      "content-type": "application/octet-stream",
      "x-filename": encodeURIComponent(file.name || "upload"),
    };
    if (opts.note) headers["x-note"] = encodeURIComponent(opts.note);
    if (opts.language) headers["x-language"] = encodeURIComponent(opts.language);
    if (opts.linkSource) headers["x-link-source"] = encodeURIComponent(opts.linkSource);
    return request<QsSourceRow>("/quantlab/sources/intake/file", { method: "POST", body: file, headers });
  },
  qsExtract: (id: string) => post<QsSourceRow>(`/quantlab/sources/${encodeURIComponent(id)}/extract`),
  qsCancel: (id: string) => post<QsSourceRow>(`/quantlab/sources/${encodeURIComponent(id)}/cancel`),
  qsNote: (id: string, text: string) =>
    post<{ id: string; text: string }>(`/quantlab/sources/${encodeURIComponent(id)}/notes`, { text }),
  qsDeleteMedia: (id: string) =>
    request<QsSourceRow>(`/quantlab/sources/${encodeURIComponent(id)}/media`, { method: "DELETE" }),
  qsDelete: (id: string) =>
    request<{ deleted: string }>(`/quantlab/sources/${encodeURIComponent(id)}`, { method: "DELETE" }),
  qsAudit: () => request<QsAudit[]>("/quantlab/sources/audit"),
  qsSpeechModel: () => request<QsSpeechModel>("/quantlab/sources/speech-model"),
  qsInstallSpeechModel: () => post<QsSpeechModel>("/quantlab/sources/speech-model"),
  qsFrameUrl: (id: string, frameId: string) =>
    `${BACKEND_URL}/quantlab/sources/${encodeURIComponent(id)}/frames/${encodeURIComponent(frameId)}`,
  qsMediaUrl: (id: string) => `${BACKEND_URL}/quantlab/sources/${encodeURIComponent(id)}/media`,
  qmMissions: () => request<QmMissionRow[]>("/quantlab/research/missions"),
  qmMission: (id: string) => request<QmMission>(`/quantlab/research/missions/${encodeURIComponent(id)}`),
  qmCreate: (sourceId: string, budget?: Record<string, number>) =>
    post<QmMissionRow>("/quantlab/research/missions", { source_id: sourceId, budget: budget ?? null }),
  qmAgents: () => request<QmAgent[]>("/quantlab/research/agents"),
  qmActivity: () => request<QmEvent[]>("/quantlab/research/activity"),
  qmTrials: (id: string) => request<QmTrial[]>(`/quantlab/research/missions/${encodeURIComponent(id)}/trials`),
  qmAnswer: (
    id: string,
    body: { accept_defaults: boolean; choices: Record<string, string>; values: Record<string, unknown> },
  ) => post<QmMissionRow>(`/quantlab/research/missions/${encodeURIComponent(id)}/answers`, body),
  qmApproveData: (id: string, maxUsd: number) =>
    post<QmMissionRow>(`/quantlab/research/missions/${encodeURIComponent(id)}/data-approval`, {
      max_usd: maxUsd,
      confirm: true,
    }),
  qmDeclineData: (id: string) =>
    post<QmMissionRow>(`/quantlab/research/missions/${encodeURIComponent(id)}/data-decline`),
  qmControl: (id: string, action: "pause" | "resume" | "cancel") =>
    post<QmMissionRow>(`/quantlab/research/missions/${encodeURIComponent(id)}/${action}`),
  qmEvolve: (id: string, budget?: Record<string, number>) =>
    post<QmMissionRow>(`/quantlab/research/missions/${encodeURIComponent(id)}/evolution`, {
      budget: budget ?? null,
    }),
  qmLock: (id: string, versionId: string) =>
    post<QmMissionRow>(`/quantlab/research/missions/${encodeURIComponent(id)}/holdout-lock`, {
      version_id: versionId,
      confirm: true,
    }),
  qmDossier: (id: string) => request<QmDossier>(`/quantlab/research/missions/${encodeURIComponent(id)}/dossier`),
  aiStatus: () => request<AiStatus>("/ai/status"),
  aiUsage: () => request<AiUsage>("/ai/usage"),
  aiStrategy: (strategy: AiStatus["strategy"], profile?: AiStatus["profile"]) =>
    post<AiStatus>("/ai/strategy", { strategy, profile: profile ?? null }),
  aiPlan: (enabled: boolean, personalUse = false) =>
    post<AiStatus>("/ai/plan", { enabled, personal_use: personalUse }),
  aiPlanCheck: () => post<AiStatus>("/ai/plan/check"),
  aiPlanTest: () =>
    post<{ result: { ok: boolean; message: string; latency_ms?: number; failure?: string }; status: AiStatus }>(
      "/ai/plan/test",
    ),
  aiTerminal: (action: "login" | "install") => post<{ command: string }>("/ai/plan/terminal", { action }),
  aiPaid: (terms: {
    monthly_budget_usd: number;
    per_mission_cap_usd: number;
    warn_at: number[];
    max_concurrent: number;
    stop_at_budget: boolean;
  }) => post<AiStatus>("/ai/paid", { ...terms, confirm: true }),
  aiPaidDisable: () => post<AiStatus>("/ai/paid/disable"),
  aiKey: (apiKey: string) => post<AiStatus>("/ai/api-key", { api_key: apiKey }),
  aiKeyDelete: () => request<AiStatus>("/ai/api-key", { method: "DELETE" }),
  aiApiCheck: () => post<AiStatus>("/ai/api/check"),
  aiLocal: (enabled: boolean, baseUrl: string, model: string) =>
    post<AiStatus>("/ai/local", { enabled, base_url: baseUrl, model }),
  aiLocalCheck: () => post<AiStatus>("/ai/local/check"),
  aiCurrency: (display: "USD" | "CHF", usdToChf: number | null) =>
    post<AiStatus>("/ai/currency", { display, usd_to_chf: usdToChf }),
};
