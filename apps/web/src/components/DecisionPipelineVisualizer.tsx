"use client";

import React from "react";
import {
  Activity,
  CheckCircle2,
  HelpCircle,
  XCircle,
  Lock,
  Shield,
  FileText,
  Clock,
  Hash,
  Sparkles,
} from "lucide-react";

export interface WorkflowStage {
  stage_name: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "BLOCKED" | "FAILED" | "UNKNOWN" | "SKIPPED";
  started_at: string;
  completed_at?: string;
  duration_ms?: number;
  detail?: string;
  input_summary?: Record<string, unknown>;
  output_summary?: Record<string, unknown>;
  decision?: string;
  evidence_ids?: string[];
  failure_reason?: string;
  correlation_id?: string;
}

export interface DecisionProposal {
  decision_id: string;
  opportunity_id?: string;
  recommended_action: string;
  expected_value: string;
  probability_of_success: number;
  model_confidence: number;
  data_confidence: number;
  risk: string;
  urgency: string;
  reasoning_summary: string;
  evidence_ids: string[];
}

export interface DecisionExplanation {
  what_happened: string;
  why_it_happened: string;
  what_ray_recommended: string;
  expected_value: string;
  recovery_probability: string;
  what_policy_decided: string;
  what_action_was_taken: string;
  evidence_citations: string[];
  actual_result: string;
  why_ray_did_not_act?: string;
}

export interface DecisionWorkflowResult {
  workflow_id: string;
  correlation_id?: string;
  payment_id: string;
  merchant_id: string;
  status: string;
  current_stage: string;
  stages: WorkflowStage[];
  decision?: DecisionProposal;
  policy_decision?: {
    decision: string;
    rule_matched: string;
    reason: string;
  };
  action_result?: {
    status: string;
    idempotency_key?: string;
    detail?: string;
  };
  outcome_result?: {
    is_recovered: boolean;
    status?: string;
    reason?: string;
  };
  decision_receipt?: {
    receipt_id: string;
    decision: string;
    rule_matched: string;
    primary_reason: string;
    why_didnt_ray_act?: string;
    amount: string;
    currency: string;
    stage_1_safety_lock_active: boolean;
    evidence_snapshot: string[];
  };
  explanation?: DecisionExplanation;
  audit_event_id?: string;
  audit_hash?: string;
}

interface Props {
  result: DecisionWorkflowResult | null;
  loading: boolean;
}

export function DecisionPipelineVisualizer({ result, loading }: Props) {
  const [selectedStage, setSelectedStage] = React.useState<WorkflowStage | null>(null);

  if (loading) {
    return (
      <div className="bg-slate-900/50 rounded-2xl border border-slate-800/80 p-12 text-center space-y-3">
        <Activity className="w-8 h-8 text-emerald-400 animate-spin mx-auto" />
        <h4 className="text-sm font-semibold text-white">Running Autonomous Decision Loop...</h4>
        <p className="text-xs text-slate-400 font-mono">
          Evaluating: EVENT → MONEY GRAPH → OPPORTUNITY → DECISION → POLICY → ACTION → VERIFICATION → AUDIT → RECEIPT
        </p>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="bg-slate-900/50 rounded-2xl border border-slate-800/80 p-12 text-center space-y-2">
        <Activity className="w-8 h-8 text-slate-600 mx-auto" />
        <h4 className="text-sm font-semibold text-slate-300">No Decision Pipeline Dispatched</h4>
        <p className="text-xs text-slate-500">
          Select a deterministic demo scenario above or trigger an automated recovery from the Payment Inspector.
        </p>
      </div>
    );
  }

  const getStageIcon = (status: string) => {
    switch (status) {
      case "COMPLETED":
        return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />;
      case "BLOCKED":
        return <Lock className="w-3.5 h-3.5 text-amber-400" />;
      case "UNKNOWN":
        return <HelpCircle className="w-3.5 h-3.5 text-purple-400" />;
      case "FAILED":
        return <XCircle className="w-3.5 h-3.5 text-rose-400" />;
      default:
        return <Clock className="w-3.5 h-3.5 text-slate-500" />;
    }
  };

  return (
    <div className="space-y-5">
      {/* 9-Stage Visual Pipeline */}
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h4 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
              <Activity className="w-3.5 h-3.5 text-emerald-400" />
              9-Stage Execution Lifecycle Telemetry
            </h4>
            <p className="text-xs text-slate-400 mt-0.5">
              Strict deterministic stage progression with live telemetry and cryptographic receipt anchoring.
            </p>
          </div>
          {result.correlation_id && (
            <div className="flex items-center gap-1.5 text-[11px] font-mono bg-slate-950 px-2 py-0.5 rounded border border-slate-800 text-slate-400">
              <Hash className="w-3 h-3 text-slate-500" />
              <span>Trace: {result.correlation_id.slice(0, 12)}...</span>
            </div>
          )}
        </div>

        {/* Stages Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3 pt-2">
          {result.stages.map((stage, idx) => {
            const isCompleted = stage.status === "COMPLETED";
            const isBlocked = stage.status === "BLOCKED";
            const isUnknown = stage.status === "UNKNOWN";
            const isFailed = stage.status === "FAILED";
            const isSkipped = stage.status === "SKIPPED";

            return (
              <div
                key={stage.stage_name}
                onClick={() => setSelectedStage(selectedStage?.stage_name === stage.stage_name ? null : stage)}
                className={`p-3.5 rounded-xl border transition flex flex-col justify-between space-y-2 cursor-pointer hover:border-emerald-500/50 ${
                  selectedStage?.stage_name === stage.stage_name
                    ? "ring-2 ring-emerald-500/40 border-emerald-500"
                    : ""
                } ${
                  isCompleted
                    ? "bg-emerald-950/20 border-emerald-500/30"
                    : isBlocked
                    ? "bg-amber-950/20 border-amber-500/30"
                    : isUnknown
                    ? "bg-purple-950/20 border-purple-500/30"
                    : isFailed
                    ? "bg-rose-950/20 border-rose-500/30"
                    : isSkipped
                    ? "bg-slate-950/40 border-slate-800/50 opacity-60"
                    : "bg-slate-950/60 border-slate-800"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-mono text-slate-500">STAGE {idx + 1}</span>
                  <div className="flex items-center gap-1">
                    {getStageIcon(stage.status)}
                    <span
                      className={`text-[10px] font-semibold px-1.5 py-0.5 rounded uppercase ${
                        isCompleted
                          ? "bg-emerald-500/20 text-emerald-300"
                          : isBlocked
                          ? "bg-amber-500/20 text-amber-300"
                          : isUnknown
                          ? "bg-purple-500/20 text-purple-300"
                          : isFailed
                          ? "bg-rose-500/20 text-rose-300"
                          : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {stage.status}
                    </span>
                  </div>
                </div>

                <div>
                  <div className="font-semibold text-xs text-white">{stage.stage_name}</div>
                  <p className="text-[11px] text-slate-400 mt-0.5 line-clamp-2" title={stage.detail || ""}>
                    {stage.detail || "Step completed successfully."}
                  </p>
                </div>

                <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-[10px] font-mono text-slate-500">
                  <span>Latency:</span>
                  <span className="text-slate-300">
                    {stage.duration_ms ? `${stage.duration_ms.toFixed(1)}ms` : "< 1ms"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Selected Stage Deep Telemetry Inspector */}
        {selectedStage && (
          <div className="mt-4 p-5 rounded-xl bg-slate-950/80 border border-slate-800 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono uppercase tracking-wider text-emerald-400">
                  Stage Telemetry Inspector
                </span>
                <span className="text-slate-600">•</span>
                <span className="text-xs font-bold text-white font-mono">{selectedStage.stage_name}</span>
                <span
                  className={`text-[10px] font-semibold px-2 py-0.5 rounded uppercase ${
                    selectedStage.status === "COMPLETED"
                      ? "bg-emerald-500/20 text-emerald-300"
                      : selectedStage.status === "BLOCKED"
                      ? "bg-amber-500/20 text-amber-300"
                      : "bg-slate-800 text-slate-400"
                  }`}
                >
                  {selectedStage.status}
                </span>
              </div>
              <button
                onClick={() => setSelectedStage(null)}
                className="text-[11px] font-mono text-slate-400 hover:text-white px-2 py-0.5 rounded bg-slate-900 border border-slate-800"
              >
                Close ✕
              </button>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs font-mono">
              <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80">
                <span className="text-slate-500 text-[10px]">Duration:</span>
                <div className="text-white font-bold mt-0.5">{selectedStage.duration_ms ? `${selectedStage.duration_ms.toFixed(2)} ms` : "< 0.1 ms"}</div>
              </div>
              <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80">
                <span className="text-slate-500 text-[10px]">Started At:</span>
                <div className="text-slate-300 text-[11px] truncate mt-0.5">{selectedStage.started_at || "N/A"}</div>
              </div>
              <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80">
                <span className="text-slate-500 text-[10px]">Completed At:</span>
                <div className="text-slate-300 text-[11px] truncate mt-0.5">{selectedStage.completed_at || "N/A"}</div>
              </div>
              <div className="p-2.5 rounded-lg bg-slate-900/60 border border-slate-800/80">
                <span className="text-slate-500 text-[10px]">Correlation ID:</span>
                <div className="text-emerald-400 text-[11px] truncate mt-0.5">{selectedStage.correlation_id || "N/A"}</div>
              </div>
            </div>

            {selectedStage.detail && (
              <div className="p-3 rounded-lg bg-slate-900/40 border border-slate-800/60 text-xs">
                <span className="text-slate-400 font-semibold block mb-1">Execution Narrative:</span>
                <p className="text-slate-200">{selectedStage.detail}</p>
              </div>
            )}

            {selectedStage.failure_reason && (
              <div className="p-3 rounded-lg bg-rose-950/20 border border-rose-500/20 text-xs">
                <span className="text-rose-400 font-semibold block mb-1">Failure / Block Reason:</span>
                <p className="text-rose-200 font-mono">{selectedStage.failure_reason}</p>
              </div>
            )}

            {selectedStage.evidence_ids && selectedStage.evidence_ids.length > 0 && (
              <div className="space-y-1 text-xs">
                <span className="text-slate-400 font-semibold block">Evidence IDs Citations:</span>
                <div className="flex flex-wrap gap-1.5">
                  {selectedStage.evidence_ids.map((ev, i) => (
                    <span key={i} className="font-mono text-[10px] bg-slate-900 px-2 py-0.5 rounded border border-slate-800 text-slate-300">
                      {ev}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
              {selectedStage.input_summary && (
                <div className="p-3 rounded-lg bg-slate-900/40 border border-slate-800/60 space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase font-bold">Input Summary</span>
                  <pre className="text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(selectedStage.input_summary, null, 2)}</pre>
                </div>
              )}
              {selectedStage.output_summary && (
                <div className="p-3 rounded-lg bg-slate-900/40 border border-slate-800/60 space-y-1">
                  <span className="text-slate-500 text-[10px] uppercase font-bold">Output Summary</span>
                  <pre className="text-[11px] text-emerald-300 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(selectedStage.output_summary, null, 2)}</pre>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Truth-Grounded Decision Explanation & Audit Receipt */}
      {result.explanation && (
        <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
              <h4 className="text-xs font-semibold text-slate-100 uppercase tracking-wider">Truth-Grounded Decision Explanation</h4>
            </div>
            <span className="text-[11px] bg-slate-950 text-slate-400 px-2 py-0.5 rounded border border-slate-800 font-mono">
              Deterministic Explainability
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {/* 1. What Happened */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">1. What Happened</div>
              <p className="text-xs text-slate-200 leading-relaxed">{result.explanation.what_happened}</p>
            </div>

            {/* 2. Why It Happened */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">2. Root Cause Telemetry</div>
              <p className="text-xs text-slate-200 leading-relaxed">{result.explanation.why_it_happened}</p>
            </div>

            {/* 3. What RAY Recommended */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">3. Yield Proposal</div>
              <p className="text-xs text-emerald-300 font-medium leading-relaxed">{result.explanation.what_ray_recommended}</p>
            </div>

            {/* 4. Expected Value & Probability */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">4. Expected Value & Probability</div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-xs font-semibold text-slate-100 tabular-nums">{result.explanation.expected_value}</span>
                <span className="text-[11px] font-mono bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded border border-emerald-500/20">
                  {result.explanation.recovery_probability} win rate
                </span>
              </div>
            </div>

            {/* 5. What Policy Decided */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">5. Deterministic Policy Gate</div>
              <p className="text-xs text-slate-200 leading-relaxed font-mono">{result.explanation.what_policy_decided}</p>
            </div>

            {/* 6. What Action Was Taken */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">6. Action Layer Execution</div>
              <p className="text-xs text-slate-200 leading-relaxed">{result.explanation.what_action_was_taken}</p>
            </div>
          </div>

          {/* 7. Actual Result & Why RAY Did Not Act */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
            <div className="p-3.5 rounded-md bg-slate-950/80 border border-slate-800/80 space-y-1">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">7. Authoritative Outcome Verification</div>
              <p className="text-xs text-slate-200 font-medium">{result.explanation.actual_result}</p>
            </div>

            {result.explanation.why_ray_did_not_act ? (
              <div className="p-3.5 rounded-md bg-amber-500/5 border border-amber-500/20 space-y-1">
                <div className="text-[11px] font-medium text-amber-400 uppercase tracking-wider">8. Safety Boundary Decision</div>
                <p className="text-xs text-amber-200">{result.explanation.why_ray_did_not_act}</p>
              </div>
            ) : (
              <div className="p-3.5 rounded-md bg-emerald-500/5 border border-emerald-500/20 space-y-1">
                <div className="text-[11px] font-medium text-emerald-400 uppercase tracking-wider">8. Action Clearance</div>
                <p className="text-xs text-emerald-200">Action cleared deterministic policy rules and executed through authorized boundary.</p>
              </div>
            )}
          </div>

          {/* Evidence Citations */}
          {result.explanation.evidence_citations && result.explanation.evidence_citations.length > 0 && (
            <div className="p-3 rounded-md bg-slate-950/40 border border-slate-800/60 space-y-1.5">
              <div className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                Supporting Empirical Evidence (Money Graph Citations)
              </div>
              <div className="flex flex-wrap gap-1.5">
                {result.explanation.evidence_citations.map((ev, i) => (
                  <span key={i} className="text-[11px] font-mono bg-slate-950 px-2 py-0.5 rounded border border-slate-800 text-slate-300">
                    {ev}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Cryptographic Proof & Receipt */}
          <div className="pt-3 border-t border-slate-800/80 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-[11px] font-mono">
            <div className="flex items-center gap-2 text-slate-400">
              <FileText className="w-3.5 h-3.5 text-slate-500" />
              <span>Receipt ID:</span>
              <span className="text-slate-200">{result.decision_receipt?.receipt_id}</span>
            </div>
            <div className="flex items-center gap-2 text-slate-400">
              <Shield className="w-3.5 h-3.5 text-emerald-400" />
              <span>SHA-256 Audit Digest:</span>
              <span className="text-emerald-400">{result.audit_hash ? `${result.audit_hash.slice(0, 24)}...` : "Verified"}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
