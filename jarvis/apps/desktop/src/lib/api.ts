import type {
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
};
