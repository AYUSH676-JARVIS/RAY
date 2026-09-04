"use client";

import React from "react";
import { ShieldCheck, Lock, Scale } from "lucide-react";

export function PolicyRulebook() {
  const rules = [
    {
      id: "FRAUD_ZERO_TOLERANCE_RULE",
      name: "Zero Tolerance for Suspected Fraud",
      action: "REJECT",
      priority: 1,
      condition: "failure_code == 'FRAUD_SUSPECTED' OR customer_risk > 0.90",
      description: "Any transaction flagged with terminal fraud heuristics or blacklisted card identifiers is permanently barred from automated recovery. Exactly zero gateway calls are dispatched.",
      riskLevel: "CRITICAL",
    },
    {
      id: "CARD_EXPIRED_TERMINAL_RULE",
      name: "Prohibit Retry on Expired Credentials",
      action: "REJECT",
      priority: 2,
      condition: "failure_code == 'CARD_EXPIRED'",
      description: "Retrying an expired card credential triggers payment network penalties and guaranteed decline. Automated execution is halted; customer payment method update is requested.",
      riskLevel: "HIGH",
    },
    {
      id: "VELOCITY_LIMIT_EXCEEDED_RULE",
      name: "Payment Lifecycle Velocity Limit",
      action: "REJECT",
      priority: 3,
      condition: "attempt_count >= 3",
      description: "Protects merchants and cardholders from issuer dispute penalties and runaway retry loops by strictly bounding maximum automated attempts to 3 per payment.",
      riskLevel: "HIGH",
    },
    {
      id: "UNKNOWN_STATE_HOLD_RULE",
      name: "Ambiguous Gateway Timeout Guard",
      action: "REJECT",
      priority: 4,
      condition: "payment_status == 'UNKNOWN'",
      description: "Enforces UNKNOWN ≠ FAILED. If a gateway drops connection or times out, blind retries are blocked until authoritative status inquiry or webhook reconciliation validates reality.",
      riskLevel: "CRITICAL",
    },
    {
      id: "CUSTOMER_RISK_ESCALATION_RULE",
      name: "Customer High Risk Escalation",
      action: "FLAG",
      priority: 5,
      condition: "customer_risk > 0.70",
      description: "When customer default history or calculated risk score exceeds 0.70, automated clearance is paused and escalated for human operations sign-off.",
      riskLevel: "MEDIUM",
    },
    {
      id: "STANDARD_DETERMINISTIC_CLEARANCE",
      name: "Standard Merchant Policy Clearance",
      action: "APPROVE",
      priority: 6,
      condition: "All guardrails passed, risk <= 0.70, attempt_count < 3",
      description: "Payment clears all deterministic risk, velocity, and credential boundaries. Authorizes downstream action layer execution.",
      riskLevel: "LOW",
    },
  ];

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-2.5">
        <div className="flex items-center gap-2">
          <Scale className="w-3.5 h-3.5 text-emerald-400" />
          <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Deterministic Governance</span>
        </div>
        <h3 className="text-base font-semibold text-slate-100 tracking-tight">Deterministic Policy Rulebook</h3>
        <p className="text-xs text-slate-400 max-w-3xl leading-relaxed">
          RAY enforces mathematical certainty in money movement. AI proposes recovery strategies, but ONLY deterministic
          code can authorize financial transactions. AI recommendations can NEVER override or bypass these policy rules.
        </p>

        <div className="pt-2.5 border-t border-slate-800/80 flex flex-wrap gap-4 text-xs font-mono">
          <div className="flex items-center gap-1.5 text-slate-400">
            <Lock className="w-3.5 h-3.5 text-amber-400" />
            <span>Stage 1 Safety Lock: <strong className="text-amber-300">ACTIVE</strong></span>
          </div>
          <div className="flex items-center gap-1.5 text-slate-400">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            <span>AI Direct Execution Authority: <strong className="text-rose-400">DENIED (ZERO DIRECT ACCESS)</strong></span>
          </div>
        </div>
      </div>

      {/* Rules Table */}
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 overflow-hidden">
        <div className="p-3.5 border-b border-slate-800/80 bg-slate-900/80 flex items-center justify-between">
          <h4 className="text-xs font-medium text-slate-300 uppercase tracking-wider">Active Evaluated Guardrails</h4>
          <span className="text-[11px] font-mono text-slate-400">6 Rules Active</span>
        </div>

        <div className="divide-y divide-slate-800/60">
          {rules.map((rule) => {
            const isReject = rule.action === "REJECT";
            const isFlag = rule.action === "FLAG";

            return (
              <div key={rule.id} className="p-4 hover:bg-slate-800/20 transition-colors space-y-2">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <span className="text-xs font-mono text-slate-500">#{rule.priority}</span>
                    <span className="font-semibold text-xs text-slate-100">{rule.name}</span>
                    <span className="text-[11px] font-mono text-slate-400 bg-slate-950 px-1.5 py-0.5 rounded border border-slate-800">
                      {rule.id}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-[10px] font-mono font-medium px-2 py-0.5 rounded border ${
                        isReject
                          ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                          : isFlag
                          ? "bg-amber-500/10 text-amber-400 border-amber-500/20"
                          : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                      }`}
                    >
                      {rule.action}
                    </span>
                  </div>
                </div>

                <div className="text-xs font-mono text-slate-300 bg-slate-950/70 p-2 rounded border border-slate-800/80">
                  <span className="text-slate-500 mr-2">COND:</span>{rule.condition}
                </div>

                <p className="text-xs text-slate-400 leading-relaxed">{rule.description}</p>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
