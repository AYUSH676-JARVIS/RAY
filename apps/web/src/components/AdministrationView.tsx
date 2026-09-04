"use client";

import React, { useEffect, useState } from "react";
import {
  ShieldCheck,
  Power,
  Lock,
  Sliders,
  AlertOctagon,
  RefreshCw,
  Save,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import { apiFetch } from "../lib/api";

interface Stage2Status {
  mode: string;
  is_live_authorized: boolean;
  activated_at?: string;
  activated_by?: string;
  activation_reason?: string;
  kill_switch_engaged: boolean;
  kill_switch_engaged_at?: string;
  kill_switch_engaged_by?: string;
  kill_switch_reason?: string;
}

interface MerchantConfig {
  source: string;
  max_retries: number;
  cooldown_seconds: number;
  risk_threshold: number;
  enabled_strategies: string[];
  notification_preferences: {
    email_alerts: boolean;
    slack_webhook?: string | null;
    notify_on_recovery: boolean;
    notify_on_fraud: boolean;
  };
}

export function AdministrationView() {
  const [stage2, setStage2] = useState<Stage2Status | null>(null);
  const [config, setConfig] = useState<MerchantConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [killSuccess, setKillSuccess] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Form states
  const [killReason, setKillReason] = useState("");
  const [showKillModal, setShowKillModal] = useState(false);

  const [maxRetries, setMaxRetries] = useState(3);
  const [cooldownSeconds, setCooldownSeconds] = useState(30);
  const [riskThreshold, setRiskThreshold] = useState(0.65);
  const [enabledStrategies, setEnabledStrategies] = useState<string[]>([]);

  const fetchAdminData = async () => {
    try {
      setLoading(true);
      setErrorMessage(null);
      const [s2Res, cfgRes] = await Promise.all([
        apiFetch("/api/v1/admin/stage2/status"),
        apiFetch("/api/v1/merchants/config"),
      ]);

      if (s2Res.ok) {
        const s2Data = await s2Res.json();
        setStage2(s2Data);
      }

      if (cfgRes.ok) {
        const cfgData: MerchantConfig = await cfgRes.json();
        setConfig(cfgData);
        setMaxRetries(cfgData.max_retries);
        setCooldownSeconds(cfgData.cooldown_seconds);
        setRiskThreshold(cfgData.risk_threshold);
        setEnabledStrategies(cfgData.enabled_strategies || []);
      }
    } catch (e: unknown) {
      setErrorMessage(e instanceof Error ? e.message : "Unable to load administration telemetry.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let isMounted = true;
    const load = async () => {
      try {
        const [s2Res, cfgRes] = await Promise.all([
          apiFetch("/api/v1/admin/stage2/status"),
          apiFetch("/api/v1/merchants/config"),
        ]);

        if (s2Res.ok && isMounted) {
          const s2Data = await s2Res.json();
          setStage2(s2Data);
        }

        if (cfgRes.ok && isMounted) {
          const cfgData: MerchantConfig = await cfgRes.json();
          setConfig(cfgData);
          setMaxRetries(cfgData.max_retries);
          setCooldownSeconds(cfgData.cooldown_seconds);
          setRiskThreshold(cfgData.risk_threshold);
          setEnabledStrategies(cfgData.enabled_strategies || []);
        }
      } catch (e: unknown) {
        if (isMounted) {
          setErrorMessage(e instanceof Error ? e.message : "Unable to load administration telemetry.");
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };
    load();
    return () => {
      isMounted = false;
    };
  }, []);

  const handleSaveConfig = async () => {
    try {
      setErrorMessage(null);
      setSaveSuccess(false);

      const payload = {
        max_retries: maxRetries,
        cooldown_seconds: cooldownSeconds,
        risk_threshold: riskThreshold,
        enabled_strategies: enabledStrategies,
        notification_preferences: config?.notification_preferences || {
          email_alerts: true,
          notify_on_recovery: true,
          notify_on_fraud: true,
        },
      };

      const res = await apiFetch("/api/v1/merchants/config", {
        method: "PUT",
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to persist configuration overrides.");
      }

      const updated = await res.json();
      setConfig(updated);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 4000);
    } catch (e: unknown) {
      setErrorMessage(e instanceof Error ? e.message : "Configuration update failed.");
    }
  };

  const handleEngageKillSwitch = async () => {
    try {
      setErrorMessage(null);
      const res = await apiFetch("/api/v1/admin/kill-switch", {
        method: "POST",
        body: JSON.stringify({
          reason: killReason || "Emergency operator override engaged via administrative control plane.",
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Kill-switch engagement failed.");
      }

      const s2Data = await res.json();
      setStage2(s2Data);
      setKillSuccess(true);
      setShowKillModal(false);
      setTimeout(() => setKillSuccess(false), 5000);
    } catch (e: unknown) {
      setErrorMessage(e instanceof Error ? e.message : "Kill-switch activation rejected.");
    }
  };

  const toggleStrategy = (strat: string) => {
    setEnabledStrategies((prev) =>
      prev.includes(strat) ? prev.filter((s) => s !== strat) : [...prev, strat]
    );
  };

  return (
    <div className="space-y-5">
      {/* View Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-4 border-b border-slate-800/80 gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-slate-100 tracking-tight">
              Administrative Governance & Controls
            </h2>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800/80 text-slate-400 border border-slate-700/60">
              Platform Admin
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Manage stage execution boundaries, distributed emergency kill-switches, and merchant risk parameters.
          </p>
        </div>

        <button
          onClick={fetchAdminData}
          disabled={loading}
          className="inline-flex items-center justify-center gap-1.5 h-8 px-3 text-xs font-medium rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors disabled:opacity-50 self-start sm:self-auto"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-emerald-400" : "text-slate-400"}`} />
          <span>Refresh Status</span>
        </button>
      </div>

      {/* Notifications */}
      {saveSuccess && (
        <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-md text-xs text-emerald-300 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>Policy overrides saved and recorded into the continuous audit hash chain.</span>
        </div>
      )}

      {killSuccess && (
        <div className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-md text-xs text-rose-300 flex items-center gap-2">
          <AlertOctagon className="w-4 h-4 text-rose-400 shrink-0" />
          <span>EMERGENCY KILL SWITCH ENGAGED: Autonomous money movement locked cluster-wide.</span>
        </div>
      )}

      {errorMessage && (
        <div className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-md text-xs text-rose-300 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Stage Guard & Kill Switch Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Stage 1 vs 2 Safety Guard */}
        <div className="p-4 rounded-lg border border-slate-800/80 bg-slate-900/40 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Lock className="w-4 h-4 text-slate-400" />
              <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">
                Financial Execution Guard
              </h3>
            </div>
            <span
              className={`px-2 py-0.5 text-[11px] font-mono font-medium rounded border ${
                stage2?.is_live_authorized
                  ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                  : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
              }`}
            >
              {stage2?.is_live_authorized ? "STAGE 2: LIVE AUTHORIZED" : "STAGE 1: SAFETY LOCKED"}
            </span>
          </div>

          <div className="p-3 rounded bg-slate-950/70 border border-slate-800/80 space-y-2 text-xs">
            <div className="flex items-start gap-2.5">
              {stage2?.is_live_authorized ? (
                <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
              ) : (
                <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
              )}
              <div className="space-y-1">
                <p className="font-medium text-slate-200">
                  {stage2?.is_live_authorized
                    ? "Direct Autonomous Money Movement Authorized"
                    : "Zero Financial Execution Risk (Failsafe Active)"}
                </p>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  {stage2?.is_live_authorized
                    ? "Approved actions trigger external transactions against Razorpay gateway. Dual-key authorization is active."
                    : "All actions halt at the Stage 1 boundary with BLOCKED_STAGE1_SAFETY status. Simulated gateways generate virtual telemetry."}
                </p>
              </div>
            </div>

            <div className="pt-2 border-t border-slate-800/80 text-[11px] text-slate-400 flex items-center justify-between font-mono">
              <span>Dual-Key Administrative Status:</span>
              <span className="text-slate-300">
                {stage2?.is_live_authorized ? "AUTHENTICATED" : "NOT ENGAGED"}
              </span>
            </div>
          </div>
        </div>

        {/* Distributed Emergency Kill Switch */}
        <div className="p-4 rounded-lg border border-rose-500/20 bg-rose-500/5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <AlertOctagon className="w-4 h-4 text-rose-400" />
              <h3 className="text-xs font-semibold text-rose-300 uppercase tracking-wider">
                Distributed Emergency Kill Switch
              </h3>
            </div>
            <span className="px-2 py-0.5 text-[11px] font-mono rounded bg-rose-500/10 text-rose-400 border border-rose-500/20">
              FAIL-CLOSED
            </span>
          </div>

          <p className="text-xs text-slate-300 leading-relaxed">
            Instantly forces financial execution back to Stage 1 safety lock. Authoritative state is stored in PostgreSQL and propagates cluster-wide to all API nodes and background workers.
          </p>

          <div className="pt-1">
            {!showKillModal ? (
              <button
                onClick={() => setShowKillModal(true)}
                className="w-full inline-flex items-center justify-center gap-2 h-8 px-3 rounded-md bg-rose-600/90 hover:bg-rose-600 text-white text-xs font-medium border border-rose-500 transition-colors shadow-sm"
              >
                <Power className="w-3.5 h-3.5" />
                <span>Engage Emergency Kill Switch</span>
              </button>
            ) : (
              <div className="p-3 bg-slate-950 rounded-md border border-rose-500/30 space-y-2.5">
                <p className="text-xs font-medium text-rose-300">
                  Confirm Emergency Kill Switch Engagement:
                </p>
                <input
                  type="text"
                  placeholder="Mandatory reason for tamper-evident audit trail..."
                  value={killReason}
                  onChange={(e) => setKillReason(e.target.value)}
                  className="w-full h-8 px-2.5 bg-slate-900 border border-slate-700 rounded text-xs text-white placeholder-slate-500 focus:outline-none focus:border-rose-500 transition-colors"
                />
                <div className="flex items-center gap-2 justify-end">
                  <button
                    onClick={() => setShowKillModal(false)}
                    className="h-7 px-2.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleEngageKillSwitch}
                    className="h-7 px-3 bg-rose-600 hover:bg-rose-500 text-white text-xs font-medium rounded transition-colors"
                  >
                    Confirm & Lock Now
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Merchant Policy Configuration */}
      <div className="p-4 rounded-lg border border-slate-800/80 bg-slate-900/40 space-y-5">
        <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
          <div className="flex items-center gap-2">
            <Sliders className="w-4 h-4 text-slate-400" />
            <h3 className="text-xs font-semibold text-slate-200 uppercase tracking-wider">
              Merchant Policy & Velocity Governance
            </h3>
          </div>
          <span className="text-[11px] font-mono text-slate-400 bg-slate-950 px-2 py-0.5 rounded border border-slate-800">
            Source: {config?.source || "GLOBAL_DEFAULT"}
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Max Retries */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-300">
              Maximum Retries Per Payment
            </label>
            <input
              type="number"
              min={1}
              max={10}
              value={maxRetries}
              onChange={(e) => setMaxRetries(parseInt(e.target.value) || 1)}
              className="w-full h-8 px-2.5 bg-slate-950 border border-slate-800 rounded-md text-xs text-slate-200 focus:outline-none focus:border-slate-600 font-mono"
            />
            <p className="text-[11px] text-slate-500">
              Hard velocity ceiling enforced by Policy Engine (Rule 3).
            </p>
          </div>

          {/* Cooldown Seconds */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-300">
              Retry Cooldown Period (Seconds)
            </label>
            <input
              type="number"
              min={0}
              max={86400}
              value={cooldownSeconds}
              onChange={(e) => setCooldownSeconds(parseInt(e.target.value) || 0)}
              className="w-full h-8 px-2.5 bg-slate-950 border border-slate-800 rounded-md text-xs text-slate-200 focus:outline-none focus:border-slate-600 font-mono"
            />
            <p className="text-[11px] text-slate-500">
              Mandatory minimum delay between automated attempts.
            </p>
          </div>

          {/* Risk Threshold */}
          <div className="space-y-1.5">
            <div className="flex justify-between items-center">
              <label className="text-xs font-medium text-slate-300">
                Risk Score Threshold
              </label>
              <span className="text-xs font-mono text-slate-200 font-medium">
                {riskThreshold.toFixed(2)}
              </span>
            </div>
            <input
              type="range"
              min={0.0}
              max={1.0}
              step={0.05}
              value={riskThreshold}
              onChange={(e) => setRiskThreshold(parseFloat(e.target.value))}
              className="w-full h-8 accent-emerald-500 cursor-pointer"
            />
            <p className="text-[11px] text-slate-500">
              Transactions exceeding threshold require manual operational clearance.
            </p>
          </div>
        </div>

        {/* Enabled Recovery Strategies */}
        <div className="space-y-2 pt-2 border-t border-slate-800/80">
          <label className="text-xs font-medium text-slate-300">
            Authorized Recovery Strategies
          </label>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5">
            {[
              { id: "OPTIMAL_RETRY_WINDOW", label: "Smart Window Retry" },
              { id: "SMART_ROUTING", label: "Acquiring Route Cascade" },
              { id: "CUSTOMER_OUTREACH", label: "Payment Link Outreach" },
              { id: "PARTIAL_CAPTURE", label: "Step-Up Verification" },
            ].map((strat) => (
              <label
                key={strat.id}
                className={`flex items-center gap-2 p-2.5 rounded-md border text-xs cursor-pointer transition-colors ${
                  enabledStrategies.includes(strat.id)
                    ? "bg-slate-800/70 border-slate-600 text-slate-100"
                    : "bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700"
                }`}
              >
                <input
                  type="checkbox"
                  checked={enabledStrategies.includes(strat.id)}
                  onChange={() => toggleStrategy(strat.id)}
                  className="rounded border-slate-700 text-emerald-500 focus:ring-emerald-500 bg-slate-900"
                />
                <span className="font-medium text-xs">{strat.label}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Save Button */}
        <div className="pt-3 border-t border-slate-800/80 flex justify-end">
          <button
            onClick={handleSaveConfig}
            className="inline-flex items-center justify-center gap-1.5 h-8 px-4 text-xs font-medium rounded-md bg-emerald-600 hover:bg-emerald-500 text-white transition-colors shadow-sm"
          >
            <Save className="w-3.5 h-3.5" />
            <span>Save Configuration (Audit Chained)</span>
          </button>
        </div>
      </div>
    </div>
  );
}
