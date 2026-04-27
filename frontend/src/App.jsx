import { useState, useEffect, useCallback } from "react";
import { getMerchants, getBalance, getLedger, getBankAccounts, getPayouts, createPayout } from "./api";
import { v4 as uuidv4 } from "uuid";

const TERMINAL = ["COMPLETED", "FAILED"];

const fmt = (paise) => `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;

const StatusBadge = ({ status }) => {
  const colors = {
    PENDING: "bg-yellow-500/20 text-yellow-300 border border-yellow-500/30",
    PROCESSING: "bg-blue-500/20 text-blue-300 border border-blue-500/30",
    COMPLETED: "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30",
    FAILED: "bg-red-500/20 text-red-300 border border-red-500/30",
    CREDIT: "bg-emerald-500/20 text-emerald-300",
    DEBIT: "bg-red-500/20 text-red-300",
    HELD: "bg-amber-500/20 text-amber-300",
    FINAL: "bg-slate-600/40 text-slate-300",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${colors[status] || "bg-slate-700 text-slate-300"}`}>
      {status}
    </span>
  );
};

export default function App() {
  const [merchants, setMerchants] = useState([]);
  const [selectedMerchant, setSelectedMerchant] = useState(null);
  const [balance, setBalance] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [payouts, setPayouts] = useState([]);
  const [bankAccounts, setBankAccounts] = useState([]);
  const [form, setForm] = useState({ bank_account_id: "", amount: "" });
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState(null);

  const showToast = (msg, type = "success") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Load merchants once
  useEffect(() => {
    getMerchants().then((r) => {
      setMerchants(r.data);
      if (r.data.length > 0) setSelectedMerchant(r.data[0]);
    }).catch(() => showToast("Could not load merchants", "error"));
  }, []);

  // reload: balance + ledger + bank accounts ONLY (no payouts — separate interval)
  const reload = useCallback(() => {
    if (!selectedMerchant) return;
    const id = selectedMerchant.id;
    getBalance(id).then((r) => setBalance(r.data)).catch(() => { });
    getLedger(id).then((r) => setLedger(r.data.results || [])).catch(() => { });
    getBankAccounts(id).then((r) => {
      setBankAccounts(r.data);
      if (r.data.length > 0 && !form.bank_account_id)
        setForm((f) => ({ ...f, bank_account_id: r.data[0].id }));
    }).catch(() => { });
  }, [selectedMerchant]); // NOTE: payouts NOT fetched here — avoids payout-poll cascade

  // Fetch payouts once on merchant change (separate from polling)
  const fetchPayouts = useCallback(() => {
    if (!selectedMerchant) return;
    getPayouts(selectedMerchant.id).then((r) => setPayouts(r.data.results || [])).catch(() => { });
  }, [selectedMerchant]);

  // Initial load when merchant changes
  useEffect(() => { reload(); fetchPayouts(); }, [reload, fetchPayouts]);

  // Poll balance + ledger every 5s (independent of payout state)
  useEffect(() => {
    const t = setInterval(() => { if (selectedMerchant) reload(); }, 5000);
    return () => clearInterval(t);
  }, [reload]);

  // Poll payouts every 3s ONLY while non-terminal payouts exist
  // Uses a ref for allTerminal to avoid re-creating the interval on every render
  useEffect(() => {
    const allTerminal = payouts.length > 0 && payouts.every((p) => TERMINAL.includes(p.status));
    if (allTerminal) return; // stop — no cleanup needed, interval was never created
    const t = setInterval(() => {
      if (selectedMerchant)
        getPayouts(selectedMerchant.id).then((r) => setPayouts(r.data.results || [])).catch(() => { });
    }, 3000);
    return () => clearInterval(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedMerchant]); // intentionally NOT in deps: payouts — avoids interval reset on every poll


  const handlePayout = async (e) => {
    e.preventDefault();
    if (!form.amount || !form.bank_account_id) return;
    const amountPaise = Math.round(parseFloat(form.amount) * 100);
    if (isNaN(amountPaise) || amountPaise <= 0) {
      showToast("Enter a valid amount", "error"); return;
    }
    setSubmitting(true);
    try {
      await createPayout(selectedMerchant.id, form.bank_account_id, amountPaise, uuidv4());
      showToast("Payout requested!");
      setForm((f) => ({ ...f, amount: "" }));
      reload();
    } catch (err) {
      const detail = err?.response?.data?.detail || "Payout failed";
      showToast(detail, "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-5 py-3 rounded-xl shadow-2xl text-sm font-medium transition-all
          ${toast.type === "error" ? "bg-red-900 border border-red-700 text-red-200" : "bg-emerald-900 border border-emerald-700 text-emerald-200"}`}>
          {toast.msg}
        </div>
      )}

      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center text-white font-bold text-sm">P</div>
          <span className="font-semibold text-lg tracking-tight">Playto Pay</span>
        </div>
        <select
          className="bg-slate-800 border border-slate-700 text-slate-100 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
          value={selectedMerchant?.id || ""}
          onChange={(e) => {
            const m = merchants.find((m) => m.id === e.target.value);
            setSelectedMerchant(m);
            setBalance(null); setLedger([]); setPayouts([]); setBankAccounts([]);
            setForm({ bank_account_id: "", amount: "" });
          }}
        >
          {merchants.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
      </header>

      <main className="max-w-6xl mx-auto px-4 py-8 space-y-8">
        {/* Balance Cards — Stripe-style: Available (Primary) + 4 secondary metrics */}
        <div className="space-y-3">
          {/* Primary card — Available Balance */}
          <div className="bg-gradient-to-r from-violet-600/20 to-indigo-600/20 border border-violet-500/30 rounded-2xl p-6 flex items-center justify-between">
            <div>
              <p className="text-xs text-violet-300 uppercase tracking-widest mb-1">Available Balance</p>
              <p className="text-4xl font-bold text-white">{balance?.available_paise !== undefined ? fmt(balance.available_paise) : "—"}</p>
            </div>
            <div className="w-14 h-14 rounded-2xl bg-violet-500/20 flex items-center justify-center text-2xl">💳</div>
          </div>
          {/* Secondary metrics — 4 cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: "Held Funds", value: balance?.held_paise, color: "from-amber-500 to-orange-500", tip: "Locked in active payouts" },
              { label: "Total Credited", value: balance?.total_credited_paise, color: "from-emerald-500 to-teal-500", tip: "All inflows incl. reversals" },
              { label: "Gross Payouts", value: balance?.total_debited_paise, color: "from-rose-500 to-red-500", tip: "All payout attempts" },
              { label: "Net Settled", value: balance?.net_settled_paise, color: "from-blue-500 to-indigo-500", tip: "Completed payouts only" },
            ].map(({ label, value, color, tip }) => (
              <div key={label} className="bg-slate-900 border border-slate-800 rounded-xl p-4 group relative">
                <p className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</p>
                <p className={`text-xl font-bold bg-gradient-to-r ${color} bg-clip-text text-transparent`}>
                  {value !== undefined && value !== null ? fmt(value) : "—"}
                </p>
                <p className="text-xs text-slate-600 mt-1">{tip}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          {/* Payout Form */}
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
            <h2 className="text-base font-semibold mb-4 text-slate-100">Request Payout</h2>
            <form onSubmit={handlePayout} className="space-y-4">
              <div>
                <label className="text-xs text-slate-400 block mb-1">Bank Account</label>
                <select
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
                  value={form.bank_account_id}
                  onChange={(e) => setForm((f) => ({ ...f, bank_account_id: e.target.value }))}
                  required
                >
                  {bankAccounts.map((b) => (
                    <option key={b.id} value={b.id}>{b.account_holder_name} — {b.account_number}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-400 block mb-1">Amount (₹)</label>
                <input
                  type="text"
                  step="0.01"
                  min="0.01"
                  placeholder="e.g. 500.00"
                  className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500"
                  value={form.amount}
                  onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value }))}
                  required
                />
              </div>
              <button
                type="submit"
                disabled={submitting}
                className="w-full bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 disabled:opacity-50 text-white font-semibold py-2.5 rounded-xl transition-all text-sm"
              >
                {submitting ? "Processing…" : "Request Payout"}
              </button>
            </form>
          </div>

          {/* Recent Ledger */}
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 overflow-hidden">
            <h2 className="text-base font-semibold mb-4 text-slate-100">Recent Transactions</h2>
            <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
              {ledger.length === 0 && <p className="text-sm text-slate-500">No transactions yet.</p>}
              {ledger.map((e) => (
                <div key={e.id} className="flex items-center justify-between text-sm py-1.5 border-b border-slate-800">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={e.entry_type} />
                    <StatusBadge status={e.status} />
                    <span className="text-slate-400 text-xs truncate max-w-[120px]">{e.description || "—"}</span>
                  </div>
                  <span className={`font-semibold ${e.entry_type === "CREDIT" ? "text-emerald-400" : "text-red-400"}`}>
                    {e.entry_type === "CREDIT" ? "+" : "-"}{fmt(e.amount_paise)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Payout History */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-base font-semibold text-slate-100">Payout History</h2>
            <span className="text-xs text-slate-500">Auto-refreshes until all settled</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-slate-500 text-xs uppercase border-b border-slate-800">
                  <th className="pb-2 pr-4">ID</th>
                  <th className="pb-2 pr-4">Amount</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">Attempts</th>
                  <th className="pb-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {payouts.length === 0 && (
                  <tr><td colSpan={5} className="py-4 text-slate-500">No payouts yet.</td></tr>
                )}
                {payouts.map((p) => (
                  <tr key={p.id} className="border-b border-slate-800/40 hover:bg-slate-800/20 transition-colors">
                    <td className="py-2.5 pr-4 font-mono text-xs text-slate-400">{p.id.slice(0, 8)}…</td>
                    <td className="py-2.5 pr-4 font-semibold">{fmt(p.amount_paise)}</td>
                    <td className="py-2.5 pr-4"><StatusBadge status={p.status} /></td>
                    <td className="py-2.5 pr-4 text-slate-400">{p.attempt_count}</td>
                    <td className="py-2.5 text-slate-400 text-xs">{new Date(p.created_at).toLocaleString("en-IN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </main>
    </div>
  );
}
