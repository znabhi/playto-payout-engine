import axios from "axios";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: `${BASE_URL}/api/v1`,
  headers: { "Content-Type": "application/json" },
});

export const getMerchants = () => api.get("/merchants/");

export const getBalance = (merchantId) =>
  api.get(`/merchants/${merchantId}/balance/`);

export const getLedger = (merchantId, page = 1) =>
  api.get(`/merchants/${merchantId}/ledger/?page=${page}`);

export const getBankAccounts = (merchantId) =>
  api.get(`/merchants/${merchantId}/bank-accounts/`);

export const createPayout = (merchantId, bankAccountId, amountPaise, idempotencyKey) =>
  api.post(
    "/payouts/",
    { merchant_id: merchantId, bank_account_id: bankAccountId, amount_paise: amountPaise },
    { headers: { "Idempotency-Key": idempotencyKey } }
  );

export const getPayouts = (merchantId, page = 1) =>
  api.get(`/payouts/list/?merchant_id=${merchantId}&page=${page}`);

export const getPayoutDetail = (payoutId) =>
  api.get(`/payouts/${payoutId}/`);

export default api;
