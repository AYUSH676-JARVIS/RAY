"use client";

import React, { useEffect, useState } from "react";
import {
  Activity,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Database,
  Cpu,
  Radio,
  Inbox,
  ShieldAlert,
  RefreshCw,
} from "lucide-react";
import { apiFetch } from "../lib/api";

interface OperationsData {
  system_status: "HEALTHY" | "DEGRADED" | "CRITICAL";
  database: {
    status: string;
    latency_ms: number;
  };
  worker: {
    status: string;
    active_tasks: number;
    heartbeat_ago_seconds: number;
  };
  webhooks: {
    total_received: number;
    processed_count: number;
    rejected_count: number;
    duplicate_count: number;
    success_rate_percentage: number;
  };
  outbox: {
    pending_backlog_count: number;
    processed_count: number;
    oldest_pending_age_seconds: number | null;
  };
  circuit_breakers: Record<string, string>;
  active_alerts: Array<{
    severity: string;
    component: string;
    message: string;
    timestamp: string;
  }>;
  timestamp: string;
}

export function OperationsView() {
  const [data, setData] = useState<OperationsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/api/v1/operations/status");
      if (res.ok) {
        const json = await res.json();
        setData(json);
      } else {
        setError("Unable to retrieve operations telemetry.");
      }
    } catch (err) {
      setError(`Telemetry connection error: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let isMounted = true;
    const load = async () => {
      try {
        const res = await apiFetch("/api/v1/operations/status");
        if (res.ok && isMounted) {
          const json = await res.json();
          setData(json);
        } else if (isMounted) {
          setError("Unable to retrieve operations telemetry.");
        }
      } catch (err) {
        if (isMounted) {
          setError(`Telemetry connection error: ${err}`);
        }
      }
    };
    load();
    const interval = setInterval(load, 15000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  if (loading && !data) {
    return (
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-12 text-center space-y-3">
        <Activity className="w-6 h-6 text-emerald-400 animate-spin mx-auto" />
        <h4 className="text-xs font-medium text-slate-300">Connecting to platform telemetry...</h4>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-12 text-center space-y-3">
        <AlertTriangle className="w-6 h-6 text-amber-400 mx-auto" />
        <h4 className="text-xs font-medium text-slate-300">{error || "No operational telemetry available."}</h4>
        <button
          onClick={fetchStatus}
          className="h-8 px-3 bg-slate-800 text-slate-200 hover:text-white rounded-md text-xs font-medium border border-slate-700 transition-colors"
        >
          Retry Connection
        </button>
      </div>
    );
  }

  const isHealthy = data.system_status === "HEALTHY";
  const isDegraded = data.system_status === "DEGRADED";

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-4 border-b border-slate-800/80 gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-slate-100 tracking-tight">
              Platform Observability & Operations
            </h2>
            <span
              className={`text-[11px] font-mono px-2 py-0.5 rounded border ${
                isHealthy
                  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                  : isDegraded
                  ? "bg-amber-500/10 text-amber-400 border-amber-500/20"
                  : "bg-rose-500/10 text-rose-400 border-rose-500/20"
              }`}
            >
              {data.system_status}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Real-time telemetry for background workers, transactional outbox queue, webhook ingestion, and circuit breakers.
          </p>
        </div>

        <button
          onClick={fetchStatus}
          disabled={loading}
          className="inline-flex items-center justify-center gap-1.5 h-8 px-3 text-xs font-medium rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors disabled:opacity-50 self-start sm:self-auto"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-emerald-400" : "text-slate-400"}`} />
          <span>Refresh Telemetry</span>
        </button>
      </div>

      {/* System Status Banner */}
      <div
        className={`p-3.5 rounded-md border flex items-center justify-between gap-4 ${
          isHealthy
            ? "bg-emerald-500/5 border-emerald-500/20 text-emerald-200"
            : isDegraded
            ? "bg-amber-500/5 border-amber-500/20 text-amber-200"
            : "bg-rose-500/5 border-rose-500/20 text-rose-200"
        }`}
      >
        <div className="flex items-center gap-3">
          {isHealthy ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          ) : isDegraded ? (
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          ) : (
            <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
          )}
          <div>
            <span className="font-semibold text-xs text-slate-100 tracking-wide">SYSTEM STATUS: {data.system_status}</span>
            <p className="text-[11px] text-slate-400 mt-0.5">
              {isHealthy
                ? "PostgreSQL database, background workers, webhook processors, and outbox dispatcher operating within nominal SLAs."
                : "Degraded subsystem detected. Review active operational alerts below."}
            </p>
          </div>
        </div>
        <span className="text-[11px] font-mono text-slate-400 shrink-0">
          Last polled: {new Date(data.timestamp).toLocaleTimeString()}
        </span>
      </div>

      {/* Core Infrastructure Pillars Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {/* 1. Database */}
        <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="font-medium text-[11px] uppercase tracking-wider">PostgreSQL Database</span>
            <Database className="w-3.5 h-3.5 text-slate-500" />
          </div>
          <div className="text-xl font-bold text-slate-100 font-mono">{data.database.status}</div>
          <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs font-mono">
            <span className="text-slate-500 text-[11px]">Latency:</span>
            <span className="text-emerald-400 font-medium tabular-nums">{data.database.latency_ms} ms</span>
          </div>
        </div>

        {/* 2. Worker Daemon */}
        <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="font-medium text-[11px] uppercase tracking-wider">Worker Daemon</span>
            <Cpu className="w-3.5 h-3.5 text-slate-500" />
          </div>
          <div className="text-xl font-bold text-slate-100 font-mono">{data.worker.status}</div>
          <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs font-mono">
            <span className="text-slate-500 text-[11px]">Active Tasks:</span>
            <span className="text-slate-200 tabular-nums">{data.worker.active_tasks}</span>
          </div>
        </div>

        {/* 3. Inbound Webhooks */}
        <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="font-medium text-[11px] uppercase tracking-wider">Webhook Ingress</span>
            <Radio className="w-3.5 h-3.5 text-slate-500" />
          </div>
          <div className="text-xl font-bold text-slate-100 font-mono tabular-nums">
            {data.webhooks.success_rate_percentage}%
          </div>
          <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs font-mono">
            <span className="text-slate-500 text-[11px]">Processed / Dups:</span>
            <span className="text-slate-200 tabular-nums">{data.webhooks.processed_count} / {data.webhooks.duplicate_count}</span>
          </div>
        </div>

        {/* 4. Transactional Outbox */}
        <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span className="font-medium text-[11px] uppercase tracking-wider">Outbox Backlog</span>
            <Inbox className="w-3.5 h-3.5 text-slate-500" />
          </div>
          <div className="text-xl font-bold text-slate-100 font-mono tabular-nums">
            {data.outbox.pending_backlog_count}
          </div>
          <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs font-mono">
            <span className="text-slate-500 text-[11px]">Total Dispatched:</span>
            <span className="text-slate-200 tabular-nums">{data.outbox.processed_count}</span>
          </div>
        </div>
      </div>

      {/* Circuit Breakers */}
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-4 space-y-3">
        <h4 className="text-xs font-semibold text-slate-200 flex items-center gap-2 uppercase tracking-wider">
          <ShieldAlert className="w-3.5 h-3.5 text-emerald-400" />
          Circuit Breakers & Financial Safety Boundary Status
        </h4>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-xs font-mono">
          {Object.entries(data.circuit_breakers).map(([name, status]) => (
            <div key={name} className="p-2.5 bg-slate-950/70 border border-slate-800/80 rounded-md flex items-center justify-between">
              <span className="text-slate-400 text-[11px]">{name}</span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-medium uppercase ${
                  status === "CLOSED" || status === "ENGAGED"
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                    : "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                }`}
              >
                {status}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Active Alerts */}
      {data.active_alerts.length > 0 && (
        <div className="rounded-lg border border-rose-500/20 bg-rose-500/5 p-4 space-y-2.5">
          <h4 className="text-xs font-semibold text-rose-300 flex items-center gap-2 uppercase tracking-wider">
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
            Active Platform Alerts ({data.active_alerts.length})
          </h4>
          <div className="space-y-1.5">
            {data.active_alerts.map((alert, idx) => (
              <div key={idx} className="p-2.5 bg-slate-950/70 border border-rose-500/20 rounded-md text-xs flex items-center justify-between">
                <div>
                  <span className="font-medium text-rose-400 font-mono text-[11px]">[{alert.component}] </span>
                  <span className="text-slate-200 text-xs">{alert.message}</span>
                </div>
                <span className="text-[11px] font-mono text-slate-500 shrink-0">
                  {new Date(alert.timestamp).toLocaleTimeString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
