"use client";

import React, { useState } from "react";
import {
  Zap,
  Play,
  HelpCircle,
} from "lucide-react";


interface ChaosMode {
  id: string;
  name: string;
  category: "NETWORK" | "SECURITY" | "INTEGRITY" | "AI_SAFETY";
  scenarioId?: string;
  simulateFlags?: {
    simulate_gateway?: boolean;
    simulate_timeout?: boolean;
    simulate_decline?: string;
    simulate_ai_failure?: boolean;
    simulate_malformed_ai?: boolean;
  };
  beforeState: string;
  duringState: string;
  afterState: string;
  invariantEnforced: string;
  description: string;
}

const CHAOS_MODES: ChaosMode[] = [
  {
    id: "mode_1",
    name: "1. Transient Gateway Timeout -> Recovery",
    category: "NETWORK",
    scenarioId: "scenario_a",
    beforeState: "Payment FAILED due to 3DS timeout",
    duringState: "AI proposes SMART_RETRY -> Policy clears -> Dispatches retry",
    afterState: "Outcome verified SETTLED -> Money Graph updated to SUCCESS",
    invariantEnforced: "Temporary network failures can be safely recovered once policy cleared.",
    description: "Acquiring bank connection timed out during 3DS presentment. Smart retry recovers ₹2,500 without manual intervention.",
  },
  {
    id: "mode_2",
    name: "2. Terminal Hard Decline -> No Recovery",
    category: "NETWORK",
    scenarioId: "scenario_d",
    simulateFlags: { simulate_gateway: true, simulate_decline: "CARD_EXPIRED" },
    beforeState: "Payment FAILED with CARD_EXPIRED",
    duringState: "Policy REJECTS automated retry attempt",
    afterState: "Outcome marked FAILED; exactly zero further retries dispatched",
    invariantEnforced: "Terminal cardholder declines are never blindly retried, avoiding scheme fees.",
    description: "Customer card is expired. Automated execution is halted by policy engine to prevent issuer penalties.",
  },
  {
    id: "mode_3",
    name: "3. Fraud Flag -> Policy Block",
    category: "SECURITY",
    scenarioId: "scenario_b",
    beforeState: "Payment flagged with FRAUD_SUSPECTED",
    duringState: "Policy Engine REJECTS -> Action stage SKIPPED",
    afterState: "Zero gateway calls; Tamper-evident Policy Block Receipt generated",
    invariantEnforced: "AI recommendation NEVER bypasses deterministic fraud guardrails.",
    description: "Transaction exhibits fraud velocity. Policy Engine halts execution before any money movement attempt.",
  },
  {
    id: "mode_4",
    name: "4. Card Expired -> Policy Block",
    category: "SECURITY",
    scenarioId: "scenario_d",
    beforeState: "Card credential expired",
    duringState: "CARD_EXPIRED_TERMINAL_RULE matches and blocks retry",
    afterState: "Customer prompted for new payment method; 0 gateway retries",
    invariantEnforced: "Credential validity must be deterministically proven before action.",
    description: "Expired card credential strictly barred from network retries by merchant policy.",
  },
  {
    id: "mode_5",
    name: "5. Gateway Ambiguity -> UNKNOWN (UNKNOWN != FAILED)",
    category: "NETWORK",
    scenarioId: "scenario_c",
    beforeState: "Payment FAILED -> Retry dispatched to gateway",
    duringState: "Gateway socket times out mid-flight; no HTTP response received",
    afterState: "Entered UNKNOWN state; blind retries blocked; authoritative recon required",
    invariantEnforced: "UNKNOWN != FAILED. Blind retry prohibited until status inquiry confirms outcome.",
    description: "Gateway network disconnects during presentment. System holds in UNKNOWN state without double charging.",
  },
  {
    id: "mode_6",
    name: "6. High Risk Customer -> Escalation",
    category: "SECURITY",
    scenarioId: "scenario_b",
    beforeState: "Customer risk score is 0.92 (> 0.70 threshold)",
    duringState: "Policy Engine flags transaction; automated clearance paused",
    afterState: "Escalated to human review ledger; zero autonomous money moved",
    invariantEnforced: "Autonomous execution strictly bounded by customer risk tolerance.",
    description: "High risk profile pauses automatic recovery and mandates staff sign-off.",
  },
  {
    id: "mode_7",
    name: "7. Network Partition -> Safe Circuit Fallback",
    category: "NETWORK",
    scenarioId: "scenario_c",
    beforeState: "Downstream bank pipe partitioned",
    duringState: "Network request raises timeout; caught by ActionExecutor boundary",
    afterState: "Action transitioned to UNKNOWN safely without thread starvation",
    invariantEnforced: "Crashes at gateway boundary never leak into corrupt financial state.",
    description: "Complete network dropout handled safely within structured state machine.",
  },
  {
    id: "mode_8",
    name: "8. Duplicate Callback -> Idempotent Deduplication",
    category: "INTEGRITY",
    scenarioId: "scenario_e",
    beforeState: "Gateway dispatches 2 identical execution requests concurrently",
    duringState: "Unique idempotency key lock enforces exactly-once gateway execution",
    afterState: "First request executes; second request returns cached receipt",
    invariantEnforced: "Idempotency key strictly deduplicates external financial transactions.",
    description: "Multiple duplicate triggers return the identical receipt without dual execution.",
  },
  {
    id: "mode_9",
    name: "9. Webhook Replay -> Signature Validation Failure",
    category: "SECURITY",
    beforeState: "Adversary re-submits captured webhook payload with stale timestamp",
    duringState: "HMAC-SHA256 signature and timestamp tolerance evaluated",
    afterState: "Rejected with HTTP 401; zero database state mutated",
    invariantEnforced: "Webhooks require fresh cryptographic HMAC signature verification.",
    description: "Expired or replayed inbound webhook payload rejected at API perimeter.",
  },
  {
    id: "mode_10",
    name: "10. Audit Record Tampering -> Hash Chain Alert",
    category: "INTEGRITY",
    beforeState: "Database record altered out-of-band by unauthorized actor",
    duringState: "SHA-256 audit hash chain verified against previous event hash",
    afterState: "Chain verification fails; tamper alarm raised on merchant console",
    invariantEnforced: "All financial decisions are cryptographically chained in append-only log.",
    description: "Cryptographic SHA-256 verification detects any modification to past audit events.",
  },
  {
    id: "mode_11",
    name: "11. Malformed AI Recommendation -> Schema Rejection",
    category: "AI_SAFETY",
    simulateFlags: { simulate_gateway: true, simulate_malformed_ai: true },
    beforeState: "AI model produces hallucinatory strategy with fabricated evidence citation",
    duringState: "AIReasoningValidator checks evidence against Money Graph truth -> REJECTS",
    afterState: "Safely falls back to deterministic heuristic without crashing",
    invariantEnforced: "AI output is untrusted input. Ground-truth validation is strictly mandatory.",
    description: "Hallucinated or schema-violating AI response rejected; falls back to deterministic rules.",
  },
  {
    id: "mode_12",
    name: "12. AI Service Outage -> Deterministic Heuristic Fallback",
    category: "AI_SAFETY",
    simulateFlags: { simulate_gateway: true, simulate_ai_failure: true },
    beforeState: "External AI reasoning model is offline or unreachable",
    duringState: "Workflow catches AI failure cleanly; logs fallback event",
    afterState: "Deterministic opportunity heuristics compute recovery plan safely",
    invariantEnforced: "Core recovery operations survive total AI reasoning service outages.",
    description: "AI reasoning engine unavailability does not take down the financial pipeline.",
  },
];

interface Props {
  onRunSimulation: (scenarioId: string, flags?: Record<string, unknown>) => Promise<void>;
  loading: boolean;
}

export function ChaosLab({ onRunSimulation, loading }: Props) {
  const [activeModeId, setActiveModeId] = useState<string>("mode_5");
  const [runningId, setRunningId] = useState<string | null>(null);

  const selectedMode = CHAOS_MODES.find((m) => m.id === activeModeId) || CHAOS_MODES[0];

  const handleTrigger = async (mode: ChaosMode) => {
    setRunningId(mode.id);
    try {
      if (mode.scenarioId) {
        await onRunSimulation(mode.scenarioId, mode.simulateFlags);
      } else if (mode.simulateFlags) {
        await onRunSimulation("scenario_a", mode.simulateFlags);
      } else {
        await onRunSimulation("scenario_e");
      }
    } finally {
      setRunningId(null);
    }
  };

  return (
    <div className="space-y-5">
      {/* Header Banner */}
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-2.5">
        <div className="flex items-center gap-2">
          <Zap className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Adversarial Defense Lab</span>
        </div>
        <h3 className="text-base font-semibold text-slate-100 tracking-tight">Adversarial Resilience & Failure Simulation Lab</h3>
        <p className="text-xs text-slate-400 max-w-3xl leading-relaxed">
          Interactive verification for all 12 operational failure modes. Prove empirically that
          RAY preserves core financial invariants under network partitions, gateway ambiguity, duplicate submissions,
          audit tampering, and adversarial injection.
        </p>

        {/* Highlight Banner: UNKNOWN != FAILED */}
        <div className="p-3 rounded-md bg-slate-950/80 border border-slate-800/80 flex items-start gap-2.5 mt-2">
          <HelpCircle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <div className="text-xs space-y-0.5">
            <span className="font-medium text-slate-200">Core Invariant: UNKNOWN ≠ FAILED</span>
            <p className="text-slate-400 text-[11px] leading-relaxed">
              When an external payment gateway times out mid-flight, the outcome is ambiguous. Treating UNKNOWN as FAILED and
              blindly retrying risks catastrophic duplicate debits. RAY strictly locks UNKNOWN payments until authoritative
              reconciliation validates settlement ground truth.
            </p>
          </div>
        </div>
      </div>

      {/* Interactive Mode Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {CHAOS_MODES.map((mode) => {
          const isSelected = mode.id === activeModeId;
          const isRunning = runningId === mode.id || (loading && isSelected);

          const getBadgeColor = (cat: string) => {
            switch (cat) {
              case "NETWORK":
                return "bg-sky-500/10 text-sky-300 border-sky-500/20";
              case "SECURITY":
                return "bg-rose-500/10 text-rose-300 border-rose-500/20";
              case "INTEGRITY":
                return "bg-emerald-500/10 text-emerald-300 border-emerald-500/20";
              case "AI_SAFETY":
                return "bg-purple-500/10 text-purple-300 border-purple-500/20";
              default:
                return "bg-slate-800 text-slate-300 border-slate-700";
            }
          };

          return (
            <div
              key={mode.id}
              onClick={() => setActiveModeId(mode.id)}
              className={`p-3.5 rounded-lg border transition-colors cursor-pointer flex flex-col justify-between space-y-3 ${
                isSelected
                  ? "bg-slate-900/80 border-emerald-500/60 ring-1 ring-emerald-500/30"
                  : "bg-slate-900/40 border-slate-800/80 hover:bg-slate-800/30"
              }`}
            >
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className={`text-[10px] font-medium px-1.5 py-0.2 rounded border uppercase font-mono ${getBadgeColor(mode.category)}`}>
                    {mode.category}
                  </span>
                  {isSelected && (
                    <span className="text-[10px] font-mono text-emerald-400 bg-emerald-500/10 px-1.5 py-0.2 rounded border border-emerald-500/20">
                      Active
                    </span>
                  )}
                </div>

                <h4 className="font-semibold text-xs text-slate-100 leading-snug">{mode.name}</h4>
                <p className="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">{mode.description}</p>
              </div>

              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleTrigger(mode);
                }}
                disabled={loading}
                className="w-full h-7 flex items-center justify-center gap-1.5 px-3 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700 transition-colors disabled:opacity-50"
              >
                <Play className={`w-3 h-3 fill-current text-emerald-400 ${isRunning ? "animate-spin" : ""}`} />
                <span>{isRunning ? "Simulating..." : "Simulate Failure"}</span>
              </button>
            </div>
          );
        })}
      </div>

      {/* Selected Mode Detailed Lifecycle Inspector */}
      {selectedMode && (
        <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-800/80 gap-3">
            <div>
              <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Lifecycle State Machine</span>
              <h4 className="text-sm font-semibold text-slate-100 mt-0.5">{selectedMode.name}</h4>
            </div>
            <button
              onClick={() => handleTrigger(selectedMode)}
              disabled={loading}
              className="inline-flex items-center justify-center gap-1.5 h-8 px-3.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs transition-colors disabled:opacity-50 self-start sm:self-auto"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{loading ? "Executing Pipeline..." : "Execute in Live Pipeline"}</span>
            </button>
          </div>

          <div className="p-3 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-0.5">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Invariant Enforced</span>
            <p className="text-xs text-emerald-300 font-mono">{selectedMode.invariantEnforced}</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-1">
            {/* Before State */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Before State</span>
                <span className="text-[10px] font-mono text-slate-500">Initial Input</span>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed">{selectedMode.beforeState}</p>
            </div>

            {/* During State */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium text-amber-400 uppercase tracking-wider">During Pipeline</span>
                <span className="text-[10px] font-mono text-amber-400">Enforcement</span>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed">{selectedMode.duringState}</p>
            </div>

            {/* After State */}
            <div className="p-3.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium text-emerald-400 uppercase tracking-wider">After State</span>
                <span className="text-[10px] font-mono text-emerald-400">Resolved State</span>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed">{selectedMode.afterState}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
