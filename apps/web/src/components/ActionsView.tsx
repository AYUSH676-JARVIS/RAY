"use client";

import React, { useEffect, useState } from "react";
import {
  Zap,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Lock,
  Search,
  RotateCcw,
} from "lucide-react";
import { apiFetch } from "../lib/api";

interface ActionItem {
  id: string;
  opportunity_id: string;
  strategy_name: string;
  idempotency_key: string;
  status: string;
  amount_usd?: string;
  gateway_status?: string;
  executed_at?: string;
  idempotent_replay?: boolean;
}

export function ActionsView() {
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const fetchActions = async () => {
    try {
      setLoading(true);
      setErrorMessage(null);

      const res = await apiFetch("/api/v1/dashboard");
      if (res.ok) {
        const data = await res.json();
        const items: ActionItem[] = [];
        if (data.recent_events) {
          for (const ev of data.recent_events) {
            if (ev.event_type?.includes("ACTION_") || ev.payload_after_json?.action_id) {
              const p = ev.payload_after_json || {};
              items.push({
                id: p.action_id || ev.id,
                opportunity_id: p.opportunity_id || "opp_auto_derived",
                strategy_name: p.strategy_name || "SMART_ROUTING",
                idempotency_key: p.idempotency_key || `idem_${ev.id.slice(0, 12)}`,
                status: p.status || "BLOCKED_STAGE1_SAFETY",
                amount_usd: p.amount || "199.99",
                gateway_status: p.gateway_status || "BLOCKED_STAGE1_SAFETY",
                executed_at: ev.timestamp,
                idempotent_replay: Boolean(p.idempotent_replay),
              });
            }
          }
        }
        setActions(items);
      }
    } catch (e: unknown) {
      setErrorMessage(e instanceof Error ? e.message : "Unable to retrieve action records.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let isMounted = true;
    const load = async () => {
      try {
        const res = await apiFetch("/api/v1/dashboard");
        if (res.ok && isMounted) {
          const data = await res.json();
          const items: ActionItem[] = [];
          if (data.recent_events) {
            for (const ev of data.recent_events) {
              if (ev.event_type?.includes("ACTION_") || ev.payload_after_json?.action_id) {
                const p = ev.payload_after_json || {};
                items.push({
                  id: p.action_id || ev.id,
                  opportunity_id: p.opportunity_id || "opp_auto_derived",
                  strategy_name: p.strategy_name || "SMART_ROUTING",
                  idempotency_key: p.idempotency_key || `idem_${ev.id.slice(0, 12)}`,
                  status: p.status || "BLOCKED_STAGE1_SAFETY",
                  amount_usd: p.amount || "199.99",
                  gateway_status: p.gateway_status || "BLOCKED_STAGE1_SAFETY",
                  executed_at: ev.timestamp,
                  idempotent_replay: Boolean(p.idempotent_replay),
                });
              }
            }
          }
          setActions(items);
        }
      } catch (e: unknown) {
        if (isMounted) {
          setErrorMessage(e instanceof Error ? e.message : "Unable to retrieve action records.");
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

  const handleExecuteAction = async (actionId: string, currentKey: string) => {
    try {
      setExecutingId(actionId);
      setActionFeedback(null);
      setErrorMessage(null);

      const res = await apiFetch("/api/v1/actions/execute", {
        method: "POST",
        body: JSON.stringify({
          action_id: actionId,
          idempotency_key: currentKey || `idem_manual_${actionId.slice(0, 8)}`,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Action execution failed.");
      }

      setActionFeedback(
        `Action evaluated: Status [${data.status}] ${data.idempotent_replay ? "• Idempotent Cached Replay" : ""}`
      );
      fetchActions();
    } catch (e: unknown) {
      setErrorMessage(e instanceof Error ? e.message : "Execution halted by safety gate.");
    } finally {
      setExecutingId(null);
    }
  };

  const filtered = actions.filter((a) => {
    const matchSearch =
      a.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      a.idempotency_key.toLowerCase().includes(searchTerm.toLowerCase()) ||
      a.strategy_name.toLowerCase().includes(searchTerm.toLowerCase());
    const matchStatus = statusFilter === "ALL" || a.status === statusFilter;
    return matchSearch && matchStatus;
  });

  return (
    <div className="space-y-5">
      {/* View Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-4 border-b border-slate-800/80 gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-slate-100 tracking-tight">
              Action Layer & Idempotency Ledger
            </h2>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800/80 text-slate-400 border border-slate-700/60">
              RFC 8470 Protected
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Durable financial execution tracking with transactional advisory locking, policy clearance verification, and idempotency guarantees.
          </p>
        </div>

        <button
          onClick={fetchActions}
          disabled={loading}
          className="inline-flex items-center justify-center gap-1.5 h-8 px-3 text-xs font-medium rounded-md bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition-colors disabled:opacity-50 self-start sm:self-auto"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin text-emerald-400" : "text-slate-400"}`} />
          <span>Refresh Ledger</span>
        </button>
      </div>

      {/* Notifications */}
      {actionFeedback && (
        <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-md text-xs text-emerald-300 flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span>{actionFeedback}</span>
        </div>
      )}

      {errorMessage && (
        <div className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-md text-xs text-rose-300 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Filter Toolbar */}
      <div className="flex flex-col sm:flex-row items-center gap-3">
        <div className="relative flex-1 w-full">
          <Search className="w-3.5 h-3.5 absolute left-3 top-2.5 text-slate-500" />
          <input
            type="text"
            placeholder="Filter by action identifier, idempotency key, or strategy..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full h-8 pl-8 pr-3 bg-slate-900/90 border border-slate-800 rounded-md text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-600 transition-colors"
          />
        </div>

        <div className="flex items-center gap-1.5 w-full sm:w-auto overflow-x-auto">
          {[
            { id: "ALL", label: "All Records" },
            { id: "BLOCKED_STAGE1_SAFETY", label: "Stage 1 Locked" },
            { id: "BLOCKED_POLICY", label: "Policy Blocked" },
            { id: "EXECUTED_LIVE", label: "Executed Live" },
          ].map((st) => (
            <button
              key={st.id}
              onClick={() => setStatusFilter(st.id)}
              className={`h-8 px-2.5 rounded-md text-xs font-medium whitespace-nowrap transition-colors ${
                statusFilter === st.id
                  ? "bg-slate-800 text-slate-100 border border-slate-700 shadow-sm"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/60"
              }`}
            >
              {st.label}
            </button>
          ))}
        </div>
      </div>

      {/* Data Table */}
      <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 overflow-hidden">
        {filtered.length === 0 ? (
          <div className="py-16 text-center space-y-2">
            <Zap className="w-6 h-6 text-slate-600 mx-auto" />
            <p className="text-xs font-medium text-slate-300">No action records found</p>
            <p className="text-[11px] text-slate-500 max-w-sm mx-auto">
              Recovery actions are dispatched after yield opportunities obtain deterministic policy clearance.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-900/80 text-slate-400 border-b border-slate-800 text-[11px] uppercase tracking-wider font-medium">
                <tr>
                  <th className="py-2.5 px-4">Action ID</th>
                  <th className="py-2.5 px-4">Strategy</th>
                  <th className="py-2.5 px-4">Idempotency Key</th>
                  <th className="py-2.5 px-4 text-right">Amount</th>
                  <th className="py-2.5 px-4">Execution Status</th>
                  <th className="py-2.5 px-4">Timestamp</th>
                  <th className="py-2.5 px-4 text-right">Control</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {filtered.map((act) => {
                  const isLive = act.status.includes("LIVE") || act.status === "EXECUTED";
                  const isStage1 = act.status.includes("STAGE1");
                  const isBlocked = act.status.includes("BLOCKED") || act.status.includes("REJECTED");

                  return (
                    <tr key={act.id} className="hover:bg-slate-800/25 transition-colors">
                      <td className="py-3 px-4 font-mono text-slate-300 text-[11px]">
                        {act.id.length > 18 ? `${act.id.slice(0, 18)}...` : act.id}
                      </td>
                      <td className="py-3 px-4 font-medium text-slate-200">
                        {act.strategy_name.replace(/_/g, " ")}
                      </td>
                      <td className="py-3 px-4 font-mono text-[11px] text-slate-400">
                        <span className="bg-slate-950 px-1.5 py-0.5 rounded border border-slate-800 text-slate-300">
                          {act.idempotency_key}
                        </span>
                      </td>
                      <td className="py-3 px-4 font-mono text-right tabular-nums text-slate-200">
                        ${act.amount_usd || "0.00"}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono border ${
                            isLive
                              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                              : isStage1
                              ? "bg-amber-500/10 text-amber-400 border-amber-500/20"
                              : isBlocked
                              ? "bg-rose-500/10 text-rose-400 border-rose-500/20"
                              : "bg-slate-800 text-slate-400 border-slate-700"
                          }`}
                        >
                          {isStage1 && <Lock className="w-2.5 h-2.5" />}
                          {act.status}
                        </span>
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-500 text-[11px]">
                        {act.executed_at ? new Date(act.executed_at).toLocaleTimeString() : "—"}
                      </td>
                      <td className="py-3 px-4 text-right">
                        <button
                          onClick={() => handleExecuteAction(act.id, act.idempotency_key)}
                          disabled={executingId === act.id}
                          className="inline-flex items-center gap-1 h-6 px-2 text-[11px] font-medium rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700/80 transition-colors disabled:opacity-50 ml-auto"
                          title="Verify idempotent replay handling against active gates"
                        >
                          <RotateCcw className={`w-2.5 h-2.5 ${executingId === act.id ? "animate-spin" : ""}`} />
                          <span>Replay</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
