/**
 * The HTTP client. Platform-agnostic: it needs a `fetch` and somewhere to keep
 * credentials, and nothing else.
 *
 * Two ways to authenticate, mirroring the server:
 *
 * - **Cookie session** (web). The browser holds the cookie; we hold the CSRF
 *   token the server returned at login and send it on unsafe methods.
 * - **Bearer token** (desktop, mobile). Returned once at login; the app stores
 *   it in the platform keychain and hands it to us through `AuthStore`.
 *
 * Reports are all GETs, but logout is a POST, so CSRF matters even here.
 */
import type {
  Account,
  AccountIn,
  AccountPatch,
  AccountsQuery,
  BalanceSheet,
  BalanceSheetQuery,
  Book,
  BookIn,
  Commodity,
  CommodityIn,
  CommodityPatch,
  Grant,
  GrantIn,
  CommoditiesQuery,
  Health,
  Holdings,
  HoldingsQuery,
  IncomeStatement,
  IncomeStatementQuery,
  LoginIn,
  Payee,
  PayeeIn,
  PayeesQuery,
  Problem,
  RegisterIn,
  RegisterPage,
  RegisterQuery,
  Session,
  Transaction,
  TransactionIn,
  TransactionUpdateIn,
  User,
} from "./types";

/** Mirrors the fetch option of the same name, without needing DOM types. */
export type CredentialsMode = "omit" | "same-origin" | "include";

export interface Credentials {
  /** Bearer token, for clients without cookies. */
  bearer?: string | undefined;
  /** CSRF token, for cookie-authenticated unsafe requests. */
  csrf?: string | undefined;
}

/** Where credentials live. Memory on the web, the keychain on a phone. */
export interface AuthStore {
  get(): Credentials;
  set(credentials: Credentials): void;
  clear(): void;
}

export class MemoryAuthStore implements AuthStore {
  private credentials: Credentials = {};
  get(): Credentials {
    return this.credentials;
  }
  set(credentials: Credentials): void {
    this.credentials = credentials;
  }
  clear(): void {
    this.credentials = {};
  }
}

export interface ClientOptions {
  /** e.g. "/api" behind Vite's proxy, or "https://money.example.org". */
  baseUrl: string;
  auth?: AuthStore;
  fetch?: typeof fetch;
  /**
   * Send cookies. "include" for the web app; "omit" for a bearer client, so a
   * stray cookie can never be the credential that was actually used.
   */
  credentials?: CredentialsMode;
  /** Called on every 401, so the app can drop to the login screen. */
  onUnauthorized?: () => void;
}

/** A non-2xx response, with the server's explanation if it gave one. */
export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  /** The book revision the server reported, if any. */
  readonly revision: number | null;

  constructor(status: number, type: string, message: string, revision: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.type = type;
    this.revision = revision;
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let type = "HttpError";
    let message = `${response.status} ${response.statusText}`.trim();
    try {
      const body: unknown = await response.json();
      if (isProblem(body)) {
        type = body.error.type;
        message = body.error.message;
      } else if (isDetail(body)) {
        // FastAPI's own validation errors.
        type = "RequestValidationError";
        message =
          typeof body.detail === "string"
            ? body.detail
            : body.detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ");
      }
    } catch {
      // Not JSON; keep the status line.
    }
    return new ApiError(response.status, type, message, revisionOf(response));
  }
}

function isProblem(body: unknown): body is Problem {
  if (typeof body !== "object" || body === null || !("error" in body)) return false;
  const error = (body as { error: unknown }).error;
  return typeof error === "object" && error !== null && "message" in error && "type" in error;
}

interface FastApiDetail {
  detail: string | { loc: (string | number)[]; msg: string }[];
}

function isDetail(body: unknown): body is FastApiDetail {
  return typeof body === "object" && body !== null && "detail" in body;
}

function revisionOf(response: Response): number | null {
  const header = response.headers.get("X-PyMoney-Revision");
  if (header === null) return null;
  const n = Number(header);
  return Number.isFinite(n) ? n : null;
}

type QueryValue = string | number | boolean | null | undefined | readonly string[];

/** Build a query string, leaving out what is not set. Arrays repeat the key. */
export function queryString(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const v of value) search.append(key, v);
    } else {
      search.set(key, String(value));
    }
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export class ApiClient {
  readonly baseUrl: string;
  readonly auth: AuthStore;
  private readonly fetchImpl: typeof fetch;
  private readonly credentialsMode: CredentialsMode;
  private readonly onUnauthorized: (() => void) | undefined;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.auth = options.auth ?? new MemoryAuthStore();
    // Bind, or a browser's fetch complains about an illegal invocation.
    const f = options.fetch ?? globalThis.fetch;
    this.fetchImpl = (input, init) => f(input, init);
    this.credentialsMode = options.credentials ?? "include";
    this.onUnauthorized = options.onUnauthorized;
  }

  // -- plumbing ----------------------------------------------------------

  async request<T>(method: string, path: string, body?: unknown, idempotencyKey?: string): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    const { bearer, csrf } = this.auth.get();
    if (bearer) headers["Authorization"] = `Bearer ${bearer}`;
    else if (csrf && UNSAFE.has(method)) headers["X-CSRF-Token"] = csrf;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

    const init: RequestInit = { method, headers, credentials: this.credentialsMode };
    if (body !== undefined) init.body = JSON.stringify(body);
    const response = await this.fetchImpl(this.baseUrl + path, init);

    if (!response.ok) {
      const error = await ApiError.fromResponse(response);
      if (error.status === 401) this.onUnauthorized?.();
      throw error;
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("PUT", path, body);
  }

  patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("PATCH", path, body);
  }

  delete<T = void>(path: string): Promise<T> {
    return this.request<T>("DELETE", path);
  }

  // -- auth --------------------------------------------------------------

  /**
   * Log in and remember what the server handed back. The bearer token is only
   * kept when cookies are off: a web client must not hold a long-lived token
   * in JavaScript-reachable memory when the cookie already does the job.
   */
  async login(credentials: LoginIn): Promise<Session> {
    const session = await this.post<Session>("/auth/login", credentials);
    this.remember(session);
    return session;
  }

  async register(details: RegisterIn): Promise<Session> {
    const session = await this.post<Session>("/auth/register", details);
    this.remember(session);
    return session;
  }

  private remember(session: Session): void {
    const useBearer = this.credentialsMode === "omit";
    this.auth.set({
      csrf: session.csrf_token,
      bearer: useBearer ? (session.token ?? undefined) : undefined,
    });
  }

  async logout(): Promise<void> {
    try {
      await this.post<void>("/auth/logout");
    } finally {
      this.auth.clear();
    }
  }

  me(): Promise<User> {
    return this.get<User>("/auth/me");
  }

  health(): Promise<Health> {
    return this.get<Health>("/health");
  }

  // -- books and reference data -----------------------------------------

  books(): Promise<Book[]> {
    return this.get<Book[]>("/books");
  }

  book(bookId: string): Promise<Book> {
    return this.get<Book>(`/books/${bookId}`);
  }

  /** Create a book; the caller becomes its owner. */
  createBook(body: BookIn): Promise<Book> {
    return this.post<Book>("/books", body);
  }

  // -- access control (owners only) ---------------------------------------

  grants(bookId: string): Promise<Grant[]> {
    return this.get<Grant[]>(`/books/${bookId}/grants`);
  }

  /** Grant access, or change an existing grant's role. */
  putGrant(bookId: string, grant: GrantIn): Promise<Grant> {
    return this.put<Grant>(`/books/${bookId}/grants`, grant);
  }

  deleteGrant(bookId: string, subject: string): Promise<void> {
    return this.delete(`/books/${bookId}/grants/${encodeURIComponent(subject)}`);
  }

  accounts(bookId: string, query: AccountsQuery = {}): Promise<Account[]> {
    return this.get<Account[]>(`/books/${bookId}/accounts${queryString(query)}`);
  }

  account(bookId: string, accountId: string): Promise<Account> {
    return this.get<Account>(`/books/${bookId}/accounts/${accountId}`);
  }

  // -- the chart of accounts (owners only) ---------------------------------
  //
  // Each structural change is its own endpoint because each has its own rules:
  // a move can make a cycle, a close needs the children closed first, a delete
  // needs the account empty. PATCH only touches the descriptive fields.

  createAccount(bookId: string, body: AccountIn): Promise<Account> {
    return this.post<Account>(`/books/${bookId}/accounts`, body);
  }

  patchAccount(bookId: string, accountId: string, body: AccountPatch): Promise<Account> {
    return this.patch<Account>(`/books/${bookId}/accounts/${accountId}`, body);
  }

  renameAccount(bookId: string, accountId: string, name: string): Promise<Account> {
    return this.post<Account>(`/books/${bookId}/accounts/${accountId}/rename`, { name });
  }

  moveAccount(bookId: string, accountId: string, parentId: string | null): Promise<Account> {
    return this.post<Account>(`/books/${bookId}/accounts/${accountId}/move`, { parent_id: parentId });
  }

  setAccountClosed(bookId: string, accountId: string, closed: boolean): Promise<Account> {
    return this.post<Account>(`/books/${bookId}/accounts/${accountId}/closed`, { closed });
  }

  deleteAccount(bookId: string, accountId: string): Promise<void> {
    return this.delete(`/books/${bookId}/accounts/${accountId}`);
  }

  commodities(bookId: string, query: CommoditiesQuery = {}): Promise<Commodity[]> {
    return this.get<Commodity[]>(`/books/${bookId}/commodities${queryString(query)}`);
  }

  // -- commodities (editors) ----------------------------------------------

  createCommodity(bookId: string, body: CommodityIn): Promise<Commodity> {
    return this.post<Commodity>(`/books/${bookId}/commodities`, body);
  }

  /** Display fields only; the minor unit factor is fixed for life. */
  patchCommodity(bookId: string, commodityId: string, body: CommodityPatch): Promise<Commodity> {
    return this.patch<Commodity>(`/books/${bookId}/commodities/${commodityId}`, body);
  }

  /** Refused (409) while any account or price uses it, or for the base currency. */
  deleteCommodity(bookId: string, commodityId: string): Promise<void> {
    return this.delete(`/books/${bookId}/commodities/${commodityId}`);
  }

  payees(bookId: string, query: PayeesQuery = {}): Promise<Payee[]> {
    return this.get<Payee[]>(`/books/${bookId}/payees${queryString(query)}`);
  }

  createPayee(bookId: string, body: PayeeIn): Promise<Payee> {
    return this.post<Payee>(`/books/${bookId}/payees`, body);
  }

  // -- ledger ------------------------------------------------------------

  /**
   * One page of an account's register. Pass `next_cursor` back as `cursor`.
   * Named for the ledger, because `register()` above is the sign-up call.
   */
  ledger(bookId: string, accountId: string, query: RegisterQuery = {}): Promise<RegisterPage> {
    return this.get<RegisterPage>(
      `/books/${bookId}/accounts/${accountId}/register${queryString(query)}`,
    );
  }

  transaction(bookId: string, transactionId: string): Promise<Transaction> {
    return this.get<Transaction>(`/books/${bookId}/transactions/${transactionId}`);
  }

  /**
   * Post a transaction. The legs are exact decimals as strings; the server
   * books them and decides where any rounding remainder lands. Pass an
   * `idempotencyKey` when retrying is possible (a flaky connection), and the
   * same key will never post twice.
   */
  createTransaction(bookId: string, body: TransactionIn, idempotencyKey?: string): Promise<Transaction> {
    return this.request<Transaction>("POST", `/books/${bookId}/transactions`, body, idempotencyKey);
  }

  /** Legs, when given, replace the whole set: a transaction is never patched half. */
  updateTransaction(bookId: string, transactionId: string, body: TransactionUpdateIn): Promise<Transaction> {
    return this.patch<Transaction>(`/books/${bookId}/transactions/${transactionId}`, body);
  }

  deleteTransaction(bookId: string, transactionId: string): Promise<void> {
    return this.delete(`/books/${bookId}/transactions/${transactionId}`);
  }

  // -- reports -----------------------------------------------------------

  balanceSheet(bookId: string, query: BalanceSheetQuery = {}): Promise<BalanceSheet> {
    return this.get<BalanceSheet>(`/books/${bookId}/reports/balance-sheet${queryString(query)}`);
  }

  incomeStatement(bookId: string, query: IncomeStatementQuery): Promise<IncomeStatement> {
    return this.get<IncomeStatement>(
      `/books/${bookId}/reports/income-statement${queryString(query)}`,
    );
  }

  holdings(bookId: string, query: HoldingsQuery = {}): Promise<Holdings> {
    return this.get<Holdings>(`/books/${bookId}/reports/holdings${queryString(query)}`);
  }
}
