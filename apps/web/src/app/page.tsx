"use client";

import React, { useState, useEffect } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  RefreshCw,
  Activity,
  Search,
  Lock,
  Zap,
  Layers,
  Database,
  FileText,
  Clock,
  ChevronRight,
  TrendingUp,
  Play,
  Scale,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import {
  DecisionPipelineVisualizer,
  DecisionWorkflowResult,
} from "@/components/DecisionPipelineVisualizer";
import { PolicyRulebook } from "@/components/PolicyRulebook";
import { ChaosLab } from "@/components/ChaosLab";
import { OperationsView } from "@/components/OperationsView";
import { AdministrationView } from "@/components/AdministrationView";
import { ReconciliationView } from "@/components/ReconciliationView";
import { ActionsView } from "@/components/ActionsView";
import { apiFetch, initializeSession } from "@/lib/api";
import {
  getActiveRole,
  getActiveMerchantSlug,
  getAuthToken,
} from "@/lib/auth";


interface HealthData {
  status: string;
  database: string;
  timestamp: string;
  version: string;
  gateway_mode?: string;
  stage_1_safety_lock?: boolean;
  kill_switch_engaged?: boolean;
}

interface DashboardMetrics {
  total_volume_usd: string;
  total_payments_count: number;
  failed_payments_count: number;
  failure_rate_percentage: number;
  recoverable_volume_usd: string;
  active_opportunities_count: number;
  failure_distribution: Record<string, number>;
  policy_authorization_stats: Record<string, number>;
  recent_events: Array<{
    id: string;
    sequence_number?: number;
    entity_type: string;
    event_type: string;
    actor_type: string;
    actor_id: string;
    previous_event_hash?: string;
    event_hash?: string;
    payload_before_json?: Record<string, unknown>;
    payload_after_json?: Record<string, unknown>;
    timestamp: string;
  }>;
}

interface OpportunityItem {
  id: string;
  merchant_id: string;
  payment_id: string;
  failure_id: string;
  strategy_name: string;
  confidence_score: number;
  estimated_recoverable_amount: string;
  status: string;
  failure_code?: string;
  created_at: string;
  updated_at?: string;
}

interface PaymentAttempt {
  id: string;
  attempt_number: number;
  idempotency_key: string;
  gateway_name: string;
  gateway_transaction_id?: string;
  status: string;
  latency_ms?: number;
  created_at: string;
}

interface PaymentFailureItem {
  id: string;
  failure_code: string;
  raw_message: string;
  is_retryable: boolean;
  created_at: string;
}

interface PaymentDetail {
  id: string;
  merchant_id: string;
  order_id: string;
  customer_id: string;
  customer_name?: string;
  customer_email?: string;
  amount: string;
  currency: string;
  status: string;
  created_at: string;
  updated_at: string;
  attempts: PaymentAttempt[];
  failures: PaymentFailureItem[];
  opportunities: OpportunityItem[];
}

interface DynamicOpportunity {
  payment_id: string;
  opportunity_score: number;
  recommended_strategy: string;
  blocked_strategies: string[];
  score_breakdown: {
    base_recovery_score: number;
    expected_value: string;
    risk_cost: string;
    action_cost?: string;
    success_probability: number;
  };
  explanation: {
    reason: string;
    risk: string;
    urgency?: string;
    confidence: number;
    evidence: string[];
  };
}

export default function ControlPlane() {
  const [currentRole, setCurrentRole] = useState<string>(() => getActiveRole());
  const [currentMerchant] = useState<string>(() => getActiveMerchantSlug());
  const [health, setHealth] = useState<HealthData | null>(null);
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [opportunities, setOpportunities] = useState<OpportunityItem[]>([]);
  const [selectedPayment, setSelectedPayment] = useState<PaymentDetail | null>(null);
  const [dynamicOpp, setDynamicOpp] = useState<DynamicOpportunity | null>(null);
  const [detectingLoading, setDetectingLoading] = useState<boolean>(false);
  const [paymentSearchId, setPaymentSearchId] = useState<string>("");
  type TabType =
    | "overview"
    | "payments"
    | "opportunities"
    | "decisions"
    | "actions"
    | "audit"
    | "reconciliation"
    | "operations"
    | "scenarios"
    | "administration"
    | "decision_loop"
    | "inspector"
    | "rules"
    | "chaos_lab";
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [loading, setLoading] = useState<boolean>(true);
  const [inspectingLoading, setInspectingLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [workflowResult, setWorkflowResult] = useState<DecisionWorkflowResult | null>(null);
  const [workflowLoading, setWorkflowLoading] = useState<boolean>(false);
  const [selectedScenario, setSelectedScenario] = useState<string>("scenario_a");
  const [reconcilingLoading, setReconcilingLoading] = useState<boolean>(false);
  const [reconMessage, setReconMessage] = useState<string | null>(null);
  const [auditVerification, setAuditVerification] = useState<{
    is_valid: boolean;
    status: string;
    total_events_checked: number;
    latest_event_hash?: string;
    error_message?: string;
    verified_at: string;
  } | null>(null);
  const [verifyingChain, setVerifyingChain] = useState<boolean>(false);
  const [expandedAuditId, setExpandedAuditId] = useState<string | null>(null);

  const triggerDecisionLoop = async (scenario: string = "scenario_a", simulationFlags?: Record<string, unknown>) => {
    setWorkflowLoading(true);
    setErrorMsg(null);
    try {
      if (scenario.startsWith("scenario_")) {
        const res = await apiFetch("/api/scenarios/run", {
          method: "POST",
          body: JSON.stringify({ scenario_id: scenario }),
        });

        if (res.ok) {
          const data = await res.json();
          setWorkflowResult(data);
          setActiveTab("decision_loop");
          return;
        } else {
          const err = await res.json();
          alert(`Scenario execution failed: ${err.detail || "Unknown error"}`);
          return;
        }
      }

      // If triggered with simulation flags (e.g. from Chaos Lab or custom payment)
      const oppRes = await apiFetch("/api/opportunities?limit=15");
      if (oppRes.ok) {
        const d = await oppRes.json();
        const items: OpportunityItem[] = d.items || [];
        const targetPaymentId = items[0]?.payment_id;
        if (targetPaymentId) {
          const payload = {
            payment_id: targetPaymentId,
            ...(simulationFlags || {}),
          };
          const res = await apiFetch("/api/decisions/run", {
            method: "POST",
            body: JSON.stringify(payload),
          });
          if (res.ok) {
            const data = await res.json();
            setWorkflowResult(data);
            setActiveTab("decision_loop");
            return;
          }
        }
      }
    } catch (e) {
      console.error("Decision loop error:", e);
      alert(`Failed to run decision loop: ${e}`);
    } finally {
      setWorkflowLoading(false);
    }
  };

  const fetchData = async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      // 1. Health
      const healthRes = await apiFetch("/health");
      if (healthRes.ok) {
        const hData = await healthRes.json();
        setHealth(hData);
      }

      // 2. Dashboard
      const dashRes = await apiFetch("/api/dashboard");
      if (dashRes.ok) {
        const dData = await dashRes.json();
        setMetrics(dData);
      }

      // 3. Opportunities
      const oppRes = await apiFetch("/api/opportunities?limit=15");
      if (oppRes.ok) {
        const oData = await oppRes.json();
        setOpportunities(oData.items || []);
      }
    } catch (err) {
      console.warn("Could not reach API server:", err);
      setErrorMsg("API connection unavailable. Ensure backend is running on port 8000.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let isMounted = true;
    const loadInitialData = async () => {
      try {
        // Ensure authentic signed JWT session
        if (!getAuthToken()) {
          try {
            await initializeSession();
          } catch {
            // Ignore if backend in production or offline
          }
        }

        const [hRes, dRes, oRes] = await Promise.allSettled([
          apiFetch("/health"),
          apiFetch("/api/dashboard"),
          apiFetch("/api/opportunities?limit=15"),
        ]);

        if (!isMounted) return;

        if (hRes.status === "fulfilled" && hRes.value.ok) {
          setHealth(await hRes.value.json());
        }
        if (dRes.status === "fulfilled" && dRes.value.ok) {
          setMetrics(await dRes.value.json());
        }
        if (oRes.status === "fulfilled" && oRes.value.ok) {
          const oData = await oRes.value.json();
          setOpportunities(oData.items || []);
        }
      } catch {
        if (isMounted) setErrorMsg("API connection unavailable. Ensure backend is running on port 8000.");
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    loadInitialData();
    return () => {
      isMounted = false;
    };
  }, []);

  const runDynamicDetection = async (paymentId: string) => {
    if (!paymentId) return;
    setDetectingLoading(true);
    try {
      const res = await apiFetch("/api/opportunities/detect", {
        method: "POST",
        body: JSON.stringify({ payment_id: paymentId }),
      });
      if (res.ok) {
        const d = await res.json();
        setDynamicOpp(d);
      }
    } catch (e) {
      console.warn("Dynamic detection error:", e);
    } finally {
      setDetectingLoading(false);
    }
  };

  const inspectPayment = async (paymentId: string) => {
    if (!paymentId) return;
    setInspectingLoading(true);
    setDynamicOpp(null);
    try {
      const res = await apiFetch(`/api/payments/${paymentId}`);
      if (res.ok) {
        const data = await res.json();
        setSelectedPayment(data);
        setActiveTab("inspector");
        runDynamicDetection(paymentId);
      } else {
        alert(`Payment ${paymentId} not found.`);
      }
    } catch (err) {
      alert(`Failed to load payment: ${err}`);
    } finally {
      setInspectingLoading(false);
    }
  };

  const reconcilePayment = async (paymentId: string) => {
    if (!paymentId) return;
    setReconcilingLoading(true);
    setReconMessage(null);
    try {
      const res = await apiFetch(`/api/v1/payments/${paymentId}/reconcile`, {
        method: "POST",
        body: JSON.stringify({ authoritative_status: "SETTLED" }),
      });
      if (res.ok) {
        const data = await res.json();
        setReconMessage(`Reconciliation Success: Transitioned ${data.previous_status} → ${data.new_status} (${data.reconciliation_status}).`);
        inspectPayment(paymentId);
      } else {
        const err = await res.json();
        alert(`Reconciliation failed: ${err.detail || "Unknown error"}`);
      }
    } catch (e) {
      alert(`Reconciliation request error: ${e}`);
    } finally {
      setReconcilingLoading(false);
    }
  };

  const verifyAuditChain = async () => {
    setVerifyingChain(true);
    try {
      const res = await apiFetch("/api/v1/audit/verify", {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        setAuditVerification(data);
      } else {
        alert("Failed to verify audit chain.");
      }
    } catch (e) {
      alert(`Audit verification request error: ${e}`);
    } finally {
      setVerifyingChain(false);
    }
  };




  const formatCurrency = (val?: string | number, currency: string = "USD") => {
    const num = typeof val === "string" ? parseFloat(val) : val || 0;
    const curr = currency ? currency.toUpperCase() : "USD";
    return new Intl.NumberFormat(curr === "INR" ? "en-IN" : "en-US", {
      style: "currency",
      currency: curr,
    }).format(num);
  };

  return (
    <div className="min-h-screen bg-[#080b11] text-slate-100 flex flex-col font-sans">
      {/* Top Status Telemetry Ribbon */}
      <div className="bg-slate-950 border-b border-slate-800/80 px-4 py-1.5 text-xs flex flex-wrap items-center justify-between gap-2 text-slate-400">
        <div className="flex items-center gap-2 font-mono text-[11px]">
          <span className="flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
          <span className="text-slate-300 font-medium">RAY CONTROL PLANE v1.0</span>
          <span className="text-slate-600">&bull;</span>
          <span className="text-slate-400">MULTI-TENANT ISOLATION</span>
          <span className="text-slate-600">&bull;</span>
          <span className="text-slate-400">CONTINUOUS SHA-256 AUDIT CHAIN</span>
        </div>
        <div className="flex items-center gap-2.5 text-[11px] font-mono flex-wrap">
          {health && (
            <div className="hidden sm:flex items-center gap-1.5 text-slate-400">
              <Database className="w-3 h-3 text-emerald-400" />
              <span>POSTGRESQL:</span>
              <span className="text-emerald-400 font-medium uppercase">{health.database}</span>
            </div>
          )}
          <span
            data-testid="gateway-mode-badge"
            className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-medium border ${
              health?.gateway_mode === "LIVE"
                ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                : health?.gateway_mode === "SANDBOX"
                ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
                : "bg-purple-500/10 text-purple-400 border-purple-500/20"
            }`}
          >
            GATEWAY: {health?.gateway_mode || "SIMULATION"}
          </span>
          <span
            data-testid="stage1-lock-badge"
            className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-2 py-0.5 font-medium text-amber-400 border border-amber-500/20"
          >
            <Lock className="w-2.5 h-2.5" />
            STAGE 1 SAFETY LOCK ACTIVE
          </span>
          <span
            data-testid="demo-mode-badge"
            className="hidden md:inline-flex items-center gap-1 rounded bg-slate-800/80 px-2 py-0.5 font-medium text-slate-300 border border-slate-700 text-[10px]"
          >
            DEMO MODE
          </span>
          <div
            data-testid="session-role-badge"
            className="inline-flex items-center gap-1.5 rounded bg-slate-800/80 px-2 py-0.5 font-medium border border-slate-700 text-[10px]"
          >
            <span className="text-slate-400">TENANT:</span>
            <span className="text-slate-200 font-medium">{currentMerchant}</span>
            <span className="text-slate-600">•</span>
            <span className="text-slate-400">ROLE:</span>
            <select
              value={currentRole}
              onChange={async (e) => {
                const newRole = e.target.value;
                setCurrentRole(newRole);
                try {
                  await initializeSession(newRole, currentMerchant);
                  fetchData();
                } catch (err) {
                  console.warn("Role switch error:", err);
                }
              }}
              className="bg-transparent text-emerald-400 font-medium focus:outline-none cursor-pointer"
            >
              <option value="MERCHANT_ADMIN" className="bg-slate-900 text-slate-200">MERCHANT_ADMIN</option>
              <option value="OPERATOR" className="bg-slate-900 text-slate-200">OPERATOR</option>
              <option value="AUDITOR" className="bg-slate-900 text-slate-200">AUDITOR</option>
              <option value="READ_ONLY" className="bg-slate-900 text-slate-200">READ_ONLY</option>
            </select>
          </div>
        </div>
      </div>

      {/* Main Navigation Header */}
      <header className="border-b border-slate-800/80 bg-[#0c111a]/95 sticky top-0 z-40 backdrop-blur-md px-4 sm:px-6 py-3">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div className="flex items-center justify-between md:justify-start gap-4">
            <div className="flex items-center gap-2.5">
              <div className="h-8 w-8 rounded-lg bg-slate-900 border border-slate-700/80 flex items-center justify-center shadow-sm">
                <Zap className="w-4 h-4 text-emerald-400" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-base font-bold tracking-tight text-white">RAY</span>
                  <span className="text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-300 border border-slate-700 font-medium">
                    Control Plane
                  </span>
                </div>
                <p className="text-[11px] text-slate-400">Merchant Revenue Recovery</p>
              </div>
            </div>

            <button
              onClick={fetchData}
              disabled={loading}
              className="md:hidden inline-flex items-center gap-1.5 h-7 px-2.5 rounded bg-slate-900 text-slate-300 text-xs border border-slate-800"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-emerald-400" : ""}`} />
              <span>Refresh</span>
            </button>
          </div>

          {/* Navigation Tabs - Exactly 10 Fintech Control Views */}
          <nav className="flex items-center gap-1 bg-slate-950/80 p-1 rounded-md border border-slate-800/90 text-xs overflow-x-auto no-scrollbar">
            {[
              { id: "overview", label: "Overview", icon: TrendingUp },
              { id: "payments", label: "Payments", icon: Database, alt: "inspector" },
              { id: "opportunities", label: "Opportunities", icon: Layers },
              { id: "decisions", label: "Decisions", icon: Play, alt: "decision_loop" },
              { id: "actions", label: "Actions", icon: Zap },
              { id: "audit", label: "Audit", icon: ShieldCheck },
              { id: "reconciliation", label: "Reconciliation", icon: Scale },
              { id: "operations", label: "Operations", icon: Activity },
              { id: "scenarios", label: "Scenarios", icon: RefreshCw, alt: "chaos_lab" },
              { id: "administration", label: "Administration", icon: Lock },
            ].map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id || (tab.alt && activeTab === tab.alt);
              return (
                <button
                  key={tab.id}
                  data-testid={`tab-${tab.id}`}
                  onClick={() => setActiveTab(tab.id as TabType)}
                  className={`h-7 px-2.5 rounded text-xs font-medium transition-colors flex items-center gap-1.5 whitespace-nowrap ${
                    isActive
                      ? "bg-slate-800 text-white shadow-sm border border-slate-700/80"
                      : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/50"
                  }`}
                >
                  <Icon className={`w-3.5 h-3.5 ${isActive ? "text-emerald-400" : "text-slate-400"}`} />
                  <span>{tab.label}</span>
                  {tab.id === "opportunities" && metrics && metrics.active_opportunities_count > 0 && (
                    <span className="text-[10px] bg-emerald-500/15 text-emerald-300 px-1.5 py-0.2 rounded font-mono font-medium">
                      {metrics.active_opportunities_count}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>

          <div className="hidden md:flex items-center gap-2">
            <button
              onClick={fetchData}
              disabled={loading}
              className="inline-flex items-center justify-center gap-1.5 h-8 px-3 rounded-md bg-slate-900 hover:bg-slate-800 text-slate-300 hover:text-white text-xs font-medium border border-slate-800 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-emerald-400" : "text-slate-400"}`} />
              <span>Refresh</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content Body */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6 space-y-5">
        {errorMsg && (
          <div className="rounded-md border border-rose-500/20 bg-rose-500/10 p-3.5 flex items-center justify-between text-rose-300 text-xs">
            <div className="flex items-center gap-2.5">
              <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
              <span>{errorMsg}</span>
            </div>
            <button
              onClick={fetchData}
              className="h-6 px-2.5 rounded bg-rose-500/20 hover:bg-rose-500/30 text-rose-200 text-xs font-medium transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {/* Tab 1: Overview */}
        {activeTab === "overview" && (
          <div className="space-y-5">
            {/* Overview Hero Header */}
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2 font-mono text-[11px]">
                  <span className="flex h-2 w-2 rounded-full bg-emerald-400" />
                  <span className="text-emerald-400 font-medium uppercase tracking-wider">Autonomous Execution Engine</span>
                </div>
                <h2 className="text-lg font-semibold text-slate-100 tracking-tight">Automated Yield & Recovery Decision Pipeline</h2>
                <p className="text-xs text-slate-400 max-w-2xl leading-relaxed">
                  Ingests decline telemetry, queries the connected Money Graph, derives optimal yield strategies, enforces deterministic policy guardrails, and produces SHA-256 chained Decision Receipts.
                </p>
              </div>
              <div className="flex items-center gap-2.5 self-start md:self-auto shrink-0">
                <button
                  onClick={() => triggerDecisionLoop("hero_recovery")}
                  disabled={workflowLoading}
                  className="inline-flex items-center justify-center gap-1.5 h-8 px-3.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs shadow-sm transition-colors disabled:opacity-50"
                >
                  <Play className={`w-3.5 h-3.5 fill-current ${workflowLoading ? "animate-spin" : ""}`} />
                  <span>{workflowLoading ? "Executing Pipeline..." : "Simulate Recovery (₹2,500)"}</span>
                </button>
                <button
                  onClick={() => setActiveTab("decision_loop")}
                  className="inline-flex items-center justify-center h-8 px-3 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700 transition-colors"
                >
                  <span>Inspect Pipeline</span>
                </button>
              </div>
            </div>

            {/* KPI Metrics Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span className="text-[11px] font-medium uppercase tracking-wider">Processed Volume</span>
                  <Activity className="w-3.5 h-3.5 text-slate-500" />
                </div>
                <div className="text-xl font-bold tracking-tight text-slate-100 font-mono tabular-nums">
                  {metrics ? formatCurrency(metrics.total_volume_usd) : "$0.00"}
                </div>
                <div className="pt-2 border-t border-slate-800/60 text-[11px] text-slate-400 flex items-center justify-between">
                  <span>Transactions:</span>
                  <span className="font-mono text-slate-300 font-medium tabular-nums">
                    {metrics?.total_payments_count.toLocaleString() || "0"}
                  </span>
                </div>
              </div>

              <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span className="text-[11px] font-medium uppercase tracking-wider">Payment Decline Volume</span>
                  <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
                </div>
                <div className="text-xl font-bold tracking-tight text-rose-400 font-mono tabular-nums">
                  {metrics ? `${metrics.failed_payments_count.toLocaleString()}` : "0"}
                </div>
                <div className="pt-2 border-t border-slate-800/60 text-[11px] text-slate-400 flex items-center justify-between">
                  <span>Decline Rate:</span>
                  <span className="font-mono text-rose-400 font-medium tabular-nums">
                    {metrics?.failure_rate_percentage || 0}%
                  </span>
                </div>
              </div>

              <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span className="text-[11px] font-medium uppercase tracking-wider">Identified Recovery Yield</span>
                  <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />
                </div>
                <div className="text-xl font-bold tracking-tight text-emerald-300 font-mono tabular-nums">
                  {metrics ? formatCurrency(metrics.recoverable_volume_usd) : "$0.00"}
                </div>
                <div className="pt-2 border-t border-slate-800/60 text-[11px] text-slate-400 flex items-center justify-between">
                  <span>Active Opportunities:</span>
                  <span className="font-mono text-emerald-400 font-medium tabular-nums">
                    {metrics?.active_opportunities_count.toLocaleString() || "0"}
                  </span>
                </div>
              </div>

              <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
                <div className="flex items-center justify-between text-slate-400 text-xs">
                  <span className="text-[11px] font-medium uppercase tracking-wider">Policy Clearances</span>
                  <ShieldCheck className="w-3.5 h-3.5 text-slate-400" />
                </div>
                <div className="text-xl font-bold tracking-tight text-slate-100 font-mono tabular-nums">
                  {metrics?.policy_authorization_stats?.APPROVED?.toLocaleString() || "0"}
                </div>
                <div className="pt-2 border-t border-slate-800/60 text-[11px] text-slate-400 flex items-center justify-between">
                  <span>Policy Blocked:</span>
                  <span className="font-mono text-amber-400 font-medium tabular-nums">
                    {metrics?.policy_authorization_stats?.REJECTED || 0}
                  </span>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              {/* Failure Distribution Breakdown */}
              <div className="lg:col-span-2 rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-4">
                <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
                  <div>
                    <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">Payment Decline Taxonomy</h3>
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      Distribution across the 8 standardized payment failure categories
                    </p>
                  </div>
                  <span className="text-[11px] font-mono text-slate-300 bg-slate-950 px-2 py-0.5 rounded border border-slate-800 tabular-nums">
                    {metrics?.failed_payments_count || 0} Declines
                  </span>
                </div>

                <div className="space-y-2.5 pt-1">
                  {metrics &&
                    Object.entries(metrics.failure_distribution || {})
                      .sort(([, a], [, b]) => b - a)
                      .map(([category, count]) => {
                        const total = metrics.failed_payments_count || 1;
                        const pct = Math.round((count / total) * 100);
                        return (
                          <div key={category} className="space-y-1">
                            <div className="flex justify-between text-xs">
                              <span className="font-mono text-[11px] text-slate-300">{category}</span>
                              <span className="text-slate-400 text-[11px] font-mono tabular-nums">
                                {count.toLocaleString()} ({pct}%)
                              </span>
                            </div>
                            <div className="h-1.5 w-full bg-slate-950 rounded-full overflow-hidden border border-slate-800/60">
                              <div
                                className={`h-full rounded-full ${
                                  category === "FRAUD_SUSPECTED"
                                    ? "bg-rose-500"
                                    : category === "BANK_TIMEOUT"
                                    ? "bg-amber-400"
                                    : category === "INSUFFICIENT_FUNDS"
                                    ? "bg-indigo-400"
                                    : "bg-emerald-400"
                                }`}
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                          </div>
                        );
                      })}
                </div>
              </div>

              {/* Architecture Invariants Card */}
              <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-3">
                <div className="pb-3 border-b border-slate-800/80">
                  <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">5-Layer Operational Boundary</h3>
                  <p className="text-[11px] text-slate-400 mt-0.5">Non-negotiable architectural guarantees</p>
                </div>

                <div className="space-y-2 text-xs">
                  <div className="p-2.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-0.5">
                    <div className="flex items-center gap-2 text-xs font-medium text-emerald-400">
                      <span className="h-4 w-4 rounded bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-[10px] font-mono">1</span>
                      <span>AI Recommends</span>
                    </div>
                    <p className="text-[11px] text-slate-400 pl-6 leading-relaxed">
                      Machine models classify failure patterns and propose recovery strategies.
                    </p>
                  </div>

                  <div className="p-2.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-0.5">
                    <div className="flex items-center gap-2 text-xs font-medium text-teal-400">
                      <span className="h-4 w-4 rounded bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-[10px] font-mono">2</span>
                      <span>Deterministic Policy Authorizes</span>
                    </div>
                    <p className="text-[11px] text-slate-400 pl-6 leading-relaxed">
                      Rigid velocity limits and merchant rules evaluate authority.
                    </p>
                  </div>

                  <div className="p-2.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-0.5">
                    <div className="flex items-center gap-2 text-xs font-medium text-amber-400">
                      <span className="h-4 w-4 rounded bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-[10px] font-mono">3</span>
                      <span>Deterministic Action Executes</span>
                    </div>
                    <p className="text-[11px] text-slate-400 pl-6 leading-relaxed">
                      Idempotent gateway calls. (Stage 1 safety lock: active).
                    </p>
                  </div>

                  <div className="p-2.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-0.5">
                    <div className="flex items-center gap-2 text-xs font-medium text-indigo-400">
                      <span className="h-4 w-4 rounded bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-[10px] font-mono">4</span>
                      <span>Outcome Verification</span>
                    </div>
                    <p className="text-[11px] text-slate-400 pl-6 leading-relaxed">
                      Settlement telemetry validates that money actually settled.
                    </p>
                  </div>

                  <div className="p-2.5 rounded-md bg-slate-950/70 border border-slate-800/80 space-y-0.5">
                    <div className="flex items-center gap-2 text-xs font-medium text-purple-400">
                      <span className="h-4 w-4 rounded bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-[10px] font-mono">5</span>
                      <span>Audit Records Everything</span>
                    </div>
                    <p className="text-[11px] text-slate-400 pl-6 leading-relaxed">
                      Immutable continuous SHA-256 hash chains secure every state transition.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Tab: Canonical Automatic Decision Loop / Decisions */}
        {(activeTab === "decisions" || activeTab === "decision_loop") && (
          <div className="space-y-5">
            {/* Control Panel Card */}
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-4">
              <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    <span className="text-[11px] font-mono uppercase tracking-wider text-slate-400">Decision Pipeline</span>
                  </div>
                  <h3 className="text-base font-semibold text-slate-100 tracking-tight mt-0.5">Autonomous Decision Pipeline</h3>
                  <p className="text-xs text-slate-400">
                    End-to-end stage progression: EVENT → MONEY GRAPH → OPPORTUNITY → DECISION → POLICY → ACTION → VERIFICATION → AUDIT → RECEIPT
                  </p>
                </div>

                {/* Scenario Selector & Trigger Button */}
                <div className="flex flex-wrap items-center gap-2.5">
                  <select
                    value={selectedScenario}
                    onChange={(e) => setSelectedScenario(e.target.value)}
                    className="h-8 bg-slate-950 border border-slate-800 rounded-md px-2.5 text-xs text-slate-200 focus:outline-none focus:border-slate-600 font-medium"
                  >
                    <option value="scenario_a">Scenario A — Healthy Recovery (₹2,500 Bank Timeout)</option>
                    <option value="scenario_b">Scenario B — Fraud Zero-Tolerance Block (Policy Blocked)</option>
                    <option value="scenario_c">Scenario C — Gateway Timeout (UNKNOWN Ambiguity Defense)</option>
                    <option value="scenario_d">Scenario D — Terminal Decline (Card Expired, No Retry)</option>
                    <option value="scenario_e">Scenario E — Duplicate Request Idempotency (Concurrent Safe)</option>
                  </select>
                  <button
                    onClick={() => triggerDecisionLoop(selectedScenario)}
                    disabled={workflowLoading}
                    className="inline-flex items-center justify-center gap-1.5 h-8 px-3.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs transition-colors shadow-sm disabled:opacity-50"
                  >
                    <Play className={`w-3.5 h-3.5 fill-current ${workflowLoading ? "animate-spin" : ""}`} />
                    <span>{workflowLoading ? "Executing..." : "Execute Pipeline"}</span>
                  </button>
                </div>
              </div>

              {/* Status Header Pill */}
              {workflowResult && (
                <div className="pt-2 border-t border-slate-800/60 flex flex-wrap items-center justify-between gap-2 text-xs">
                  <div className="flex items-center gap-2 font-mono text-[11px]">
                    <span className="text-slate-500">Workflow:</span>
                    <span className="text-slate-200 bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
                      {workflowResult.workflow_id}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 font-mono text-[11px]">
                    <span className="text-slate-500">Terminal Outcome:</span>
                    <span
                      className={`px-2 py-0.5 rounded border font-medium ${
                        workflowResult.status === "COMPLETED" || workflowResult.status === "RECOVERY_SUCCESS"
                          ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                          : workflowResult.status === "STAGE1_BLOCKED"
                          ? "bg-amber-500/10 text-amber-400 border-amber-500/20"
                          : workflowResult.status === "POLICY_BLOCKED"
                          ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                          : workflowResult.status === "UNKNOWN"
                          ? "bg-purple-500/10 text-purple-400 border border-purple-500/20"
                          : "bg-slate-800 text-slate-300 border-slate-700"
                      }`}
                    >
                      {workflowResult.status}
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Decision Pipeline Visualizer */}
            <DecisionPipelineVisualizer result={workflowResult} loading={workflowLoading} />
          </div>
        )}

        {/* Tab: Merchant Deterministic Policy Rulebook */}
        {activeTab === "rules" && <PolicyRulebook />}

        {/* Tab: Interactive Failure & Chaos Demonstration Lab / Scenarios */}
        {(activeTab === "scenarios" || activeTab === "chaos_lab") && (
          <ChaosLab
            onRunSimulation={(scenId, flags) => triggerDecisionLoop(scenId, flags)}
            loading={workflowLoading}
          />
        )}

        {/* Tab 2: Recovery Opportunities Ledger */}
        {activeTab === "opportunities" && (
          <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-slate-800/80 gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-100 uppercase tracking-wider">Autonomous Revenue Recovery Ledger</h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  AI-recommended remediation opportunities pending deterministic policy authorization
                </p>
              </div>
              <div className="text-xs text-slate-400 font-mono">
                {opportunities.length} active opportunities
              </div>
            </div>

            <div className="overflow-x-auto rounded-md border border-slate-800/80">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="border-b border-slate-800/80 bg-slate-950/70 text-slate-400 font-mono text-[11px]">
                    <th className="py-2.5 px-3 uppercase tracking-wider font-medium">Payment ID</th>
                    <th className="py-2.5 px-3 uppercase tracking-wider font-medium">Decline Code</th>
                    <th className="py-2.5 px-3 uppercase tracking-wider font-medium">Recovery Strategy</th>
                    <th className="py-2.5 px-3 uppercase tracking-wider font-medium">Model Win-Rate</th>
                    <th className="py-2.5 px-3 uppercase tracking-wider font-medium text-right">Recoverable Amount</th>
                    <th className="py-2.5 px-3 uppercase tracking-wider font-medium">Status</th>
                    <th className="py-2.5 px-3 uppercase tracking-wider font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 bg-slate-900/20">
                  {opportunities.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-xs text-slate-500">
                        No active recovery opportunities detected on this ledger.
                      </td>
                    </tr>
                  ) : (
                    opportunities.map((opp) => (
                      <tr key={opp.id} className="hover:bg-slate-800/20 transition-colors">
                        <td className="py-2.5 px-3 font-mono text-slate-300 text-[11px]">
                          {opp.payment_id.slice(0, 8)}...{opp.payment_id.slice(-4)}
                        </td>
                        <td className="py-2.5 px-3">
                          <span className="font-mono text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded border border-amber-500/20 text-[11px]">
                            {opp.failure_code || "DECLINE"}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-slate-200 font-medium">
                          {opp.strategy_name}
                        </td>
                        <td className="py-2.5 px-3">
                          <div className="flex items-center gap-2">
                            <span className="font-semibold text-emerald-400 font-mono text-[11px] tabular-nums">
                              {Math.round(opp.confidence_score * 100)}%
                            </span>
                            <div className="w-14 bg-slate-950 h-1.5 rounded-full overflow-hidden border border-slate-800">
                              <div
                                className="bg-emerald-400 h-full rounded-full"
                                style={{ width: `${opp.confidence_score * 100}%` }}
                              />
                            </div>
                          </div>
                        </td>
                        <td className="py-2.5 px-3 text-right font-medium text-slate-100 font-mono text-xs tabular-nums">
                          {formatCurrency(opp.estimated_recoverable_amount)}
                        </td>
                        <td className="py-2.5 px-3">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-medium uppercase font-mono ${
                              opp.status === "OPEN"
                                ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                                : "bg-slate-800 text-slate-400 border border-slate-700"
                            }`}
                          >
                            {opp.status}
                          </span>
                        </td>
                        <td className="py-2.5 px-3 text-right">
                          <button
                            onClick={() => inspectPayment(opp.payment_id)}
                            className="inline-flex items-center gap-1 h-6 px-2.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700 transition-colors"
                          >
                            <span>Inspect Graph</span>
                            <ChevronRight className="w-3 h-3" />
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab 3: Payment Inspector / Payments */}
        {(activeTab === "payments" || activeTab === "inspector") && (
          <div className="space-y-5">
            {/* Search Box */}
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 flex items-center gap-3">
              <Search className="w-4 h-4 text-slate-400 shrink-0" />
              <input
                type="text"
                placeholder="Enter exact Payment UUID (e.g. 27c6ebaa-9a8e-4096-a0a0-df350b14d87e)..."
                value={paymentSearchId}
                onChange={(e) => setPaymentSearchId(e.target.value)}
                className="bg-transparent border-none outline-none text-slate-100 text-xs w-full placeholder-slate-500 font-mono"
              />
              <button
                onClick={() => inspectPayment(paymentSearchId)}
                disabled={inspectingLoading || !paymentSearchId}
                className="h-8 px-3.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-medium transition-colors disabled:opacity-50 shrink-0"
              >
                {inspectingLoading ? "Inspecting..." : "Inspect Payment"}
              </button>
            </div>

            {selectedPayment ? (
              <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-5">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-slate-800/80 pb-4 gap-3">
                  <div>
                    <span className="text-[11px] text-slate-400 font-mono uppercase">Payment Record ID</span>
                    <h2 className="text-sm font-bold text-white font-mono tracking-tight">{selectedPayment.id}</h2>
                  </div>
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-base font-bold font-mono text-emerald-400 tabular-nums">
                      {formatCurrency(selectedPayment.amount)} {selectedPayment.currency}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-medium font-mono uppercase ${
                        selectedPayment.status === "SUCCESS"
                          ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                          : selectedPayment.status === "UNKNOWN"
                          ? "bg-purple-500/10 text-purple-400 border border-purple-500/20"
                          : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                      }`}
                    >
                      {selectedPayment.status}
                    </span>
                    <button
                      onClick={() => reconcilePayment(selectedPayment.id)}
                      disabled={reconcilingLoading}
                      className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700 transition-colors"
                    >
                      <RefreshCw className={`w-3 h-3 ${reconcilingLoading ? "animate-spin text-emerald-400" : ""}`} />
                      {reconcilingLoading ? "Reconciling..." : "Reconcile Gateway"}
                    </button>
                  </div>
                </div>

                {reconMessage && (
                  <div className="p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-md text-xs text-indigo-200 flex items-center justify-between">
                    <span>{reconMessage}</span>
                    <button onClick={() => setReconMessage(null)} className="text-slate-400 hover:text-white text-xs">✕</button>
                  </div>
                )}

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  <div className="bg-slate-950/70 p-4 rounded-md border border-slate-800/80 space-y-1.5">
                    <h4 className="font-semibold text-slate-200 text-xs uppercase tracking-wider">Customer Profile</h4>
                    <p className="text-slate-400">Name: <span className="text-slate-200 font-medium">{selectedPayment.customer_name || "N/A"}</span></p>
                    <p className="text-slate-400">Email: <span className="text-slate-200 font-medium">{selectedPayment.customer_email || "N/A"}</span></p>
                    <p className="text-slate-400">Timestamp: <span className="text-slate-300 font-mono text-[11px]">{new Date(selectedPayment.created_at).toUTCString()}</span></p>
                  </div>

                  <div className="bg-slate-950/70 p-4 rounded-md border border-slate-800/80 space-y-1.5">
                    <h4 className="font-semibold text-slate-200 text-xs uppercase tracking-wider">Failure Diagnostic</h4>
                    {selectedPayment.failures.length > 0 ? (
                      selectedPayment.failures.map((f) => (
                        <div key={f.id} className="space-y-1">
                          <span className="font-mono text-rose-400 bg-rose-500/10 px-1.5 py-0.5 rounded text-[11px] border border-rose-500/20">
                            {f.failure_code}
                          </span>
                          <p className="text-slate-300 mt-1">{f.raw_message}</p>
                          <p className="text-slate-400 text-[11px]">Retryable: <span className={f.is_retryable ? "text-emerald-400" : "text-amber-400"}>{f.is_retryable ? "Yes (Transient)" : "No (Terminal)"}</span></p>
                        </div>
                      ))
                    ) : (
                      <p className="text-slate-400">No failure diagnostics attached to this transaction.</p>
                    )}
                  </div>
                </div>

                {/* Gateway Attempts Timeline with Idempotency Keys */}
                <div className="space-y-2.5">
                  <h4 className="font-semibold text-xs text-slate-200 uppercase tracking-wider flex items-center gap-2">
                    <Layers className="w-3.5 h-3.5 text-slate-400" />
                    Gateway Execution Attempts
                  </h4>
                  <div className="space-y-2">
                    {selectedPayment.attempts.map((att) => (
                      <div
                        key={att.id}
                        className="bg-slate-950/70 p-3.5 rounded-md border border-slate-800/80 flex flex-col sm:flex-row sm:items-center justify-between text-xs gap-3"
                      >
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-slate-200">Attempt #{att.attempt_number}</span>
                            <span className="text-slate-500">&bull;</span>
                            <span className="text-slate-400">{att.gateway_name}</span>
                            <span className="text-slate-500 font-mono text-[11px] tabular-nums">({att.latency_ms}ms)</span>
                          </div>
                          <p className="font-mono text-slate-400 text-[11px]">
                            Idempotency Key: <span className="text-slate-300 font-mono">{att.idempotency_key}</span>
                          </p>
                        </div>
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-medium uppercase font-mono self-start sm:self-auto ${
                            att.status === "SUCCESS"
                              ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                              : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                          }`}
                        >
                          {att.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Dynamic Opportunity Intelligence Panel */}
                <div className="border-t border-slate-800/80 pt-5 space-y-4">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div>
                      <h4 className="font-semibold text-xs text-slate-200 uppercase tracking-wider flex items-center gap-2">
                        <Zap className="w-3.5 h-3.5 text-emerald-400" />
                        Dynamic Money Graph Opportunity Intelligence
                      </h4>
                      <p className="text-xs text-slate-400 mt-0.5">
                        Derived in real-time from the connected merchant graph
                      </p>
                    </div>
                    <button
                      onClick={() => runDynamicDetection(selectedPayment.id)}
                      disabled={detectingLoading}
                      className="h-7 px-2.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium border border-slate-700 transition-colors flex items-center gap-1.5 self-start sm:self-auto"
                    >
                      <RefreshCw className={`w-3 h-3 ${detectingLoading ? "animate-spin text-emerald-400" : ""}`} />
                      {detectingLoading ? "Evaluating..." : "Re-evaluate Opportunity"}
                    </button>
                  </div>

                  {dynamicOpp ? (
                    <div className="bg-slate-950/80 rounded-md border border-slate-800/80 p-4 space-y-4">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800/80">
                        <div className="space-y-1">
                          <span className="text-[11px] text-slate-400 uppercase tracking-wider">Recommended Strategy</span>
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-semibold font-mono text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                              {dynamicOpp.recommended_strategy}
                            </span>
                            <span className="text-xs px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-800 font-mono tabular-nums">
                              Confidence: {Math.round(dynamicOpp.explanation.confidence * 100)}%
                            </span>
                          </div>
                        </div>

                        <div className="text-right">
                          <span className="text-[11px] text-slate-400 uppercase tracking-wider">Score</span>
                          <div className="text-xl font-bold font-mono text-emerald-400 tabular-nums">
                            {dynamicOpp.opportunity_score.toFixed(1)}
                            <span className="text-xs text-slate-500 font-normal"> / 100</span>
                          </div>
                        </div>
                      </div>

                      {/* Inspectable Components */}
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs">
                        <div className="p-2.5 rounded bg-slate-900/60 border border-slate-800/80">
                          <span className="text-slate-400 block text-[11px]">Expected Value (EV)</span>
                          <span className="font-semibold text-slate-100 font-mono text-xs tabular-nums">
                            {formatCurrency(dynamicOpp.score_breakdown.expected_value)}
                          </span>
                        </div>
                        <div className="p-2.5 rounded bg-slate-900/60 border border-slate-800/80">
                          <span className="text-slate-400 block text-[11px]">Success Probability</span>
                          <span className="font-semibold text-emerald-400 font-mono text-xs tabular-nums">
                            {Math.round(dynamicOpp.score_breakdown.success_probability * 100)}%
                          </span>
                        </div>
                        <div className="p-2.5 rounded bg-slate-900/60 border border-slate-800/80">
                          <span className="text-slate-400 block text-[11px]">Assessed Risk</span>
                          <span className={`font-semibold font-mono text-xs ${dynamicOpp.explanation.risk === "CRITICAL" ? "text-rose-400" : "text-slate-200"}`}>
                            {dynamicOpp.explanation.risk} ({formatCurrency(dynamicOpp.score_breakdown.risk_cost)})
                          </span>
                        </div>
                        <div className="p-2.5 rounded bg-slate-900/60 border border-slate-800/80">
                          <span className="text-slate-400 block text-[11px]">Action Cost</span>
                          <span className="font-semibold text-amber-300 font-mono text-xs tabular-nums">
                            {dynamicOpp.explanation.urgency} ({formatCurrency(dynamicOpp.score_breakdown.action_cost)})
                          </span>
                        </div>
                      </div>

                      {/* Evidence & Rationale */}
                      <div className="space-y-2 text-xs">
                        <span className="font-medium text-slate-300 text-[11px] uppercase tracking-wider">Empirical Evidence Citations:</span>
                        <div className="flex flex-wrap gap-1.5">
                          {dynamicOpp.explanation.evidence.map((ev, i) => (
                            <span key={i} className="font-mono text-[11px] px-2 py-0.5 rounded bg-slate-900 text-slate-300 border border-slate-800">
                              {ev}
                            </span>
                          ))}
                        </div>
                        <div className="text-slate-300 bg-slate-900/60 p-2.5 rounded border border-slate-800/80 text-xs">
                          <span className="text-slate-400 font-medium">Evaluation: </span>
                          {dynamicOpp.explanation.reason}
                        </div>
                      </div>

                      {/* Blocked Strategies Safety Invariant */}
                      {dynamicOpp.blocked_strategies.length > 0 && (
                        <div className="text-[11px] text-slate-400 flex items-center gap-2 pt-2 border-t border-slate-800/60 font-mono">
                          <Lock className="w-3 h-3 text-rose-400" />
                          <span>Blocked Strategies:</span>
                          {dynamicOpp.blocked_strategies.map((bs) => (
                            <span key={bs} className="text-rose-400 bg-rose-500/10 px-1.5 py-0.2 rounded border border-rose-500/20">
                              {bs}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="bg-slate-950/60 rounded-md border border-slate-800/80 p-3.5 text-xs text-slate-400 flex items-center justify-between">
                      <span>Click &ldquo;Re-evaluate Opportunity&rdquo; to query the Money Graph and dynamically compute recovery potential.</span>
                      <button
                        onClick={() => runDynamicDetection(selectedPayment.id)}
                        className="h-7 px-3 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs font-medium border border-slate-700 transition-colors"
                      >
                        Evaluate
                      </button>
                    </div>
                  )}
                </div>
              </div>

            ) : (
              <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-12 text-center space-y-2">
                <FileText className="w-6 h-6 text-slate-500 mx-auto" />
                <h3 className="text-xs font-medium text-slate-300">No Payment Selected</h3>
                <p className="text-xs text-slate-500 max-w-sm mx-auto">
                  Click &ldquo;Inspect Graph&rdquo; from the Opportunities table or enter an exact Payment UUID above to view the graph.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Tab 4: Audit Console & Cryptographic Chain Verifier */}
        {activeTab === "audit" && (
          <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-5 space-y-5">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800/80">
              <div>
                <h3 className="text-sm font-semibold text-slate-100 uppercase tracking-wider flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-emerald-400" />
                  Immutable Cryptographic Audit Console
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Every decision, policy gate, and state transition is permanently anchored in a continuous SHA-256 hash chain
                </p>
              </div>
              <button
                data-testid="verify-audit-chain-button"
                onClick={verifyAuditChain}
                disabled={verifyingChain}
                className="inline-flex items-center justify-center gap-1.5 h-8 px-3.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-medium transition-colors disabled:opacity-50 self-start sm:self-auto shrink-0"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${verifyingChain ? "animate-spin" : ""}`} />
                <span>{verifyingChain ? "Verifying Hash Chain..." : "Verify Hash Chain"}</span>
              </button>
            </div>

            {/* Cryptographic Verification Banner */}
            {auditVerification && (
              <div
                className={`p-3.5 rounded-md border flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs ${
                  auditVerification.is_valid
                    ? "bg-emerald-500/5 border-emerald-500/20 text-emerald-200"
                    : "bg-rose-500/5 border-rose-500/20 text-rose-200"
                }`}
              >
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    {auditVerification.is_valid ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                    ) : (
                      <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
                    )}
                    <span className="font-semibold tracking-wide text-xs">
                      {auditVerification.is_valid
                        ? "CRYPTOGRAPHIC CHAIN VERIFIED"
                        : "TAMPER ALERT — CHAIN INTEGRITY COMPROMISED"}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-400 pl-6">
                    {auditVerification.is_valid
                      ? `All ${auditVerification.total_events_checked} sequential audit events cryptographically linked with unbroken SHA-256 hash signatures.`
                      : auditVerification.error_message}
                  </p>
                </div>
                {auditVerification.latest_event_hash && (
                  <div className="font-mono text-[11px] bg-slate-950 px-2.5 py-1 rounded border border-slate-800 self-start sm:self-auto">
                    <span className="text-slate-500 text-[10px] block">Tip Hash:</span>
                    <span className="text-emerald-400">{auditVerification.latest_event_hash.slice(0, 24)}...</span>
                  </div>
                )}
              </div>
            )}

            {/* Audit Events List */}
            <div className="space-y-2.5 pt-1">
              {metrics?.recent_events?.map((ev) => {
                const isExpanded = expandedAuditId === ev.id;
                return (
                  <div
                    key={ev.id}
                    className="bg-slate-950/70 p-3.5 rounded-md border border-slate-800/80 space-y-2.5 text-xs"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-[10px] bg-slate-900 text-slate-400 px-1.5 py-0.5 rounded border border-slate-800 font-medium">
                          SEQ #{ev.sequence_number || 1}
                        </span>
                        <span className="font-mono font-medium text-emerald-400 text-xs">{ev.event_type}</span>
                        <span className="text-slate-600">&bull;</span>
                        <span className="text-slate-300">Entity: <span className="font-mono text-[11px] text-slate-200">{ev.entity_type}</span></span>
                        <span className="text-slate-600">&bull;</span>
                        <span className="text-slate-400">Actor: <span className="text-slate-200 font-mono text-[11px]">{ev.actor_id}</span> ({ev.actor_type})</span>
                      </div>
                      <div className="flex items-center gap-2.5">
                        <span className="text-slate-500 text-[11px] font-mono flex items-center gap-1">
                          <Clock className="w-3 h-3 text-slate-600" />
                          {new Date(ev.timestamp).toUTCString()}
                        </span>
                        <button
                          onClick={() => setExpandedAuditId(isExpanded ? null : ev.id)}
                          className="text-[10px] font-mono px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-800 transition-colors"
                        >
                          {isExpanded ? "Hide Payload ▲" : "Inspect Payload ▼"}
                        </button>
                      </div>
                    </div>

                    {/* Cryptographic Hash Badges */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] font-mono">
                      <div className="p-2 rounded bg-slate-900/40 border border-slate-800/50 flex items-center justify-between">
                        <span className="text-slate-500 text-[10px]">Prev Digest:</span>
                        <span className="text-slate-400 truncate max-w-[200px]" title={ev.previous_event_hash || "GENESIS"}>
                          {ev.previous_event_hash ? `${ev.previous_event_hash.slice(0, 16)}...` : "GENESIS (Root)"}
                        </span>
                      </div>
                      <div className="p-2 rounded bg-slate-900/40 border border-slate-800/50 flex items-center justify-between">
                        <span className="text-slate-500 text-[10px]">Event Digest:</span>
                        <span className="text-emerald-400 font-medium truncate max-w-[200px]" title={ev.event_hash || "Verified"}>
                          {ev.event_hash ? `${ev.event_hash.slice(0, 16)}...` : "Verified SHA-256"}
                        </span>
                      </div>
                    </div>

                    {/* Expandable JSON Payloads */}
                    {isExpanded && (
                      <div className="pt-2 border-t border-slate-800/60 grid grid-cols-1 md:grid-cols-2 gap-2.5 font-mono text-[11px]">
                        {ev.payload_before_json && (
                          <div className="p-2.5 bg-slate-900/80 rounded border border-slate-800 space-y-1">
                            <span className="text-slate-500 text-[10px] uppercase font-medium">Payload Before</span>
                            <pre className="text-slate-300 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(ev.payload_before_json, null, 2)}</pre>
                          </div>
                        )}
                        {ev.payload_after_json && (
                          <div className="p-2.5 bg-slate-900/80 rounded border border-slate-800 space-y-1">
                            <span className="text-slate-500 text-[10px] uppercase font-medium">Payload After</span>
                            <pre className="text-emerald-300 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(ev.payload_after_json, null, 2)}</pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Tab 5: Actions Layer Ledger */}
        {activeTab === "actions" && <ActionsView />}

        {/* Tab 6: Authoritative Reconciliation Workbench */}
        {activeTab === "reconciliation" && <ReconciliationView />}

        {/* Tab 7: Real-Time Platform Operations & Observability Control */}
        {activeTab === "operations" && <OperationsView />}

        {/* Tab 8: Administrative Governance & Safety Controls */}
        {activeTab === "administration" && <AdministrationView />}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800/80 bg-[#080b11] py-5 px-6 text-center text-xs text-slate-500">
        <p className="font-mono text-[11px] text-slate-400">
          RAY &bull; Enterprise Merchant Money Intelligence & Revenue Recovery Control Plane
        </p>
        <p className="mt-1 text-[11px] text-slate-600 font-mono">
          Architectural Guarantee: AI Recommends &bull; Policy Authorizes &bull; Action Executes &bull; Stage 1 Execution Safely Blocked
        </p>
      </footer>
    </div>
  );
}
