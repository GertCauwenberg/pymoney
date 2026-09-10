import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, isPending } from "../client";
import type {
  AccountIn,
  AccountPatch,
  AccountsQuery,
  BalanceSheetQuery,
  BookIn,
  ColumnMapping,
  CommoditiesQuery,
  CommodityIn,
  CommodityPatch,
  GrantIn,
  HoldingsQuery,
  ImportDecision,
  IncomeStatementQuery,
  LoginIn,
  NetWorthQuery,
  PayeeIn,
  PayeePatch,
  PayeesQuery,
  PeriodTableQuery,
  PostOccurrenceIn,
  ScheduleIn,
  SchedulePatch,
  PriceIn,
  PricesQuery,
  QuoteFetchIn,
  ReconcileIn,
  RegisterIn,
  RegisterQuery,
  SetSplitStateIn,
  TagIn,
  TagPatch,
  TagsQuery,
  TransactionIn,
  TransactionSearchQuery,
  TransactionUpdateIn,
  User,
} from "../types";
import { useApi } from "./provider";

/** Query keys in one place, so invalidation cannot drift from fetching. */
export const keys = {
  session: ["session"] as const,
  books: ["books"] as const,
  book: (bookId: string) => ["books", bookId] as const,
  grants: (bookId: string) => ["books", bookId, "grants"] as const,
  accounts: (bookId: string, q: AccountsQuery) => ["books", bookId, "accounts", q] as const,
  commodities: (bookId: string, q: CommoditiesQuery) =>
    ["books", bookId, "commodities", q] as const,
  balanceSheet: (bookId: string, q: BalanceSheetQuery) =>
    ["books", bookId, "reports", "balance-sheet", q] as const,
  incomeStatement: (bookId: string, q: IncomeStatementQuery) =>
    ["books", bookId, "reports", "income-statement", q] as const,
  holdings: (bookId: string, q: HoldingsQuery) =>
    ["books", bookId, "reports", "holdings", q] as const,
  netWorth: (bookId: string, q: NetWorthQuery) =>
    ["books", bookId, "reports", "net-worth", q] as const,
  periodTable: (bookId: string, q: PeriodTableQuery) =>
    ["books", bookId, "reports", "period-table", q] as const,
  account: (bookId: string, accountId: string) => ["books", bookId, "accounts", accountId] as const,
  payees: (bookId: string, q: PayeesQuery) => ["books", bookId, "payees", q] as const,
  tags: (bookId: string, q: TagsQuery) => ["books", bookId, "tags", q] as const,
  ledger: (bookId: string, accountId: string, q: Omit<RegisterQuery, "cursor">) =>
    ["books", bookId, "accounts", accountId, "register", q] as const,
  transaction: (bookId: string, transactionId: string) =>
    ["books", bookId, "transactions", transactionId] as const,
  prices: (bookId: string, q: Omit<PricesQuery, "cursor">) => ["books", bookId, "prices", q] as const,
  lastQuoteRun: (bookId: string) => ["books", bookId, "quotes", "last"] as const,
  quoteProviders: ["quote-providers"] as const,
  importMapping: (bookId: string, accountId: string) =>
    ["books", bookId, "accounts", accountId, "import-mapping"] as const,
  schedules: (bookId: string) => ["books", bookId, "schedules"] as const,
  dueSchedules: (bookId: string, on?: string) =>
    ["books", bookId, "schedules", "due", on ?? "today"] as const,
};

/**
 * The current user, or `null` when not logged in. A 401 is the expected
 * answer here, not an error, so it is folded into the data.
 */
export function useSession() {
  const api = useApi();
  return useQuery<User | null, ApiError>({
    queryKey: keys.session,
    queryFn: async () => {
      try {
        return await api.me();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/**
 * A fresh session must not be overwritten by a stale answer: a `GET /auth/me`
 * started *before* the login/activation (without the cookie) can resolve
 * *after* it and write `null` over the user. Cancel anything in flight first.
 */
async function rememberSession(qc: ReturnType<typeof useQueryClient>, user: User): Promise<void> {
  await qc.cancelQueries({ queryKey: keys.session });
  qc.setQueryData(keys.session, user);
}

export function useLogin() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (credentials: LoginIn) => api.login(credentials),
    onSuccess: (session) => rememberSession(qc, session.user),
  });
}

export function useRegister() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (details: RegisterIn) => api.register(details),
    onSuccess: (result) => (isPending(result) ? undefined : rememberSession(qc, result.user)),
  });
}

export function useActivate() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => api.activate(token),
    onSuccess: (session) => rememberSession(qc, session.user),
  });
}

export function useResendActivation() {
  const api = useApi();
  return useMutation({ mutationFn: (email: string) => api.resendActivation(email) });
}

export function useForgotPassword() {
  const api = useApi();
  return useMutation({ mutationFn: (email: string) => api.forgotPassword(email) });
}

export function useResetPassword() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ token, password }: { token: string; password: string }) => api.resetPassword(token, password),
    onSuccess: (session) => rememberSession(qc, session.user),
  });
}

export function useDeleteAccount() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (password: string) => api.deleteMyAccount(password),
    onSuccess: () => {
      qc.setQueryData(keys.session, null);
      qc.removeQueries({ queryKey: ["books"] });
    },
  });
}

export function useDeleteBook() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bookId, name }: { bookId: string; name: string }) => api.deleteBook(bookId, name),
    onSuccess: (_, { bookId }) => {
      qc.removeQueries({ queryKey: keys.book(bookId) });
      void qc.invalidateQueries({ queryKey: keys.books });
    },
  });
}

export function useLogout() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.logout(),
    onSettled: () => {
      // Session first: that is what routes the UI to the login screen and
      // unmounts everything that was looking at book data. Then drop that
      // data; it belongs to the user who just left. (Not `clear()`: it would
      // detach mounted observers, which would then never see the null.)
      qc.setQueryData(keys.session, null);
      qc.removeQueries({ queryKey: ["books"] });
    },
  });
}

export function useBooks() {
  const api = useApi();
  return useQuery({ queryKey: keys.books, queryFn: () => api.books() });
}

export function useBook(bookId: string) {
  const api = useApi();
  return useQuery({ queryKey: keys.book(bookId), queryFn: () => api.book(bookId) });
}

export function useAccounts(bookId: string, query: AccountsQuery = {}) {
  const api = useApi();
  return useQuery({
    queryKey: keys.accounts(bookId, query),
    queryFn: () => api.accounts(bookId, query),
    staleTime: 5 * 60_000,
  });
}

export function useCommodities(bookId: string, query: CommoditiesQuery = {}) {
  const api = useApi();
  return useQuery({
    queryKey: keys.commodities(bookId, query),
    queryFn: () => api.commodities(bookId, query),
    staleTime: 5 * 60_000,
  });
}

export function useBalanceSheet(bookId: string, query: BalanceSheetQuery, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: keys.balanceSheet(bookId, query),
    queryFn: () => api.balanceSheet(bookId, query),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useIncomeStatement(
  bookId: string,
  query: IncomeStatementQuery,
  enabled = true,
) {
  const api = useApi();
  return useQuery({
    queryKey: keys.incomeStatement(bookId, query),
    queryFn: () => api.incomeStatement(bookId, query),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useNetWorth(bookId: string, query: NetWorthQuery, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: keys.netWorth(bookId, query),
    queryFn: () => api.netWorth(bookId, query),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function usePeriodTable(bookId: string, query: PeriodTableQuery, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: keys.periodTable(bookId, query),
    queryFn: () => api.periodTable(bookId, query),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useHoldings(bookId: string, query: HoldingsQuery, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: keys.holdings(bookId, query),
    queryFn: () => api.holdings(bookId, query),
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useAccount(bookId: string, accountId: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.account(bookId, accountId),
    queryFn: () => api.account(bookId, accountId),
    staleTime: 5 * 60_000,
  });
}

export function usePayees(bookId: string, query: PayeesQuery = {}) {
  const api = useApi();
  return useQuery({
    queryKey: keys.payees(bookId, query),
    queryFn: () => api.payees(bookId, query),
    staleTime: 5 * 60_000,
  });
}

export function useTags(bookId: string, query: TagsQuery = {}) {
  const api = useApi();
  return useQuery({
    queryKey: keys.tags(bookId, query),
    queryFn: () => api.tags(bookId, query),
    staleTime: 5 * 60_000,
  });
}

/**
 * An account's register, page by page. The server's cursor is opaque and
 * keyset-based, so a page never shifts under a concurrent insert; we just
 * hand `next_cursor` back until `has_more` is false.
 */
export function useLedger(
  bookId: string,
  accountId: string,
  query: Omit<RegisterQuery, "cursor"> = {},
  enabled = true,
) {
  const api = useApi();
  return useInfiniteQuery({
    queryKey: keys.ledger(bookId, accountId, query),
    queryFn: ({ pageParam }) =>
      api.ledger(bookId, accountId, pageParam ? { ...query, cursor: pageParam } : query),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? (last.next_cursor ?? null) : null),
    enabled,
  });
}

export function useTransactionSearch(bookId: string, query: TransactionSearchQuery | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["books", bookId, "transactions", "search", query] as const,
    queryFn: () => api.searchTransactions(bookId, query!),
    // No criteria, no request: the server would refuse an empty search anyway.
    enabled: query !== null,
    placeholderData: (previous) => previous,
  });
}

export function useHistory(bookId: string, query: { entity_id?: string; limit?: number } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: ["books", bookId, "history", query] as const,
    queryFn: () => api.history(bookId, query),
  });
}

export function useTransaction(bookId: string, transactionId: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.transaction(bookId, transactionId),
    queryFn: () => api.transaction(bookId, transactionId),
  });
}

export function useCreateBook() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BookIn) => api.createBook(body),
    onSuccess: (book) => {
      qc.setQueryData(keys.book(book.id), book);
      void qc.invalidateQueries({ queryKey: keys.books });
    },
  });
}

export function useGrants(bookId: string, enabled = true) {
  const api = useApi();
  return useQuery({ queryKey: keys.grants(bookId), queryFn: () => api.grants(bookId), enabled });
}

export function usePutGrant(bookId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (grant: GrantIn) => api.putGrant(bookId, grant),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.grants(bookId) }),
  });
}

export function useDeleteGrant(bookId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (subject: string) => api.deleteGrant(bookId, subject),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.grants(bookId) });
      // Revoking your own grant changes what /books returns.
      void qc.invalidateQueries({ queryKey: keys.books });
    },
  });
}

// -- writes to the ledger ---------------------------------------------------
//
// Invalidation is deliberately broad. A posted transaction moves every balance,
// every report and the register of each account it touches; an account change
// alters every path. Refetching a book's queries is cheap next to getting one
// of them wrong, and the server's revision header would show the staleness.

function useInvalidateBook(bookId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["books", bookId] });
    void qc.invalidateQueries({ queryKey: keys.books });
  };
}

/** Every structural change to an account, behind one hook. */
export type AccountChange =
  | { kind: "create"; body: AccountIn }
  | { kind: "patch"; accountId: string; body: AccountPatch }
  | { kind: "rename"; accountId: string; name: string }
  | { kind: "move"; accountId: string; parentId: string | null }
  | { kind: "closed"; accountId: string; closed: boolean }
  | { kind: "merge"; sourceId: string; targetId: string }
  | { kind: "delete"; accountId: string };

export function useChangeAccount(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: async (change: AccountChange) => {
      switch (change.kind) {
        case "create":
          return api.createAccount(bookId, change.body);
        case "patch":
          return api.patchAccount(bookId, change.accountId, change.body);
        case "rename":
          return api.renameAccount(bookId, change.accountId, change.name);
        case "move":
          return api.moveAccount(bookId, change.accountId, change.parentId);
        case "closed":
          return api.setAccountClosed(bookId, change.accountId, change.closed);
        case "merge":
          return api.mergeAccount(bookId, change.targetId, change.sourceId);
        case "delete":
          await api.deleteAccount(bookId, change.accountId);
          return null;
      }
    },
    onSuccess: invalidate,
  });
}

/** Every change to a payee, behind one hook. */
export type PayeeChange =
  | { kind: "create"; body: PayeeIn }
  | { kind: "patch"; payeeId: string; body: PayeePatch }
  | { kind: "merge"; targetId: string; sourceIds: string[] }
  | { kind: "delete"; payeeId: string };

export function useChangePayee(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: async (change: PayeeChange) => {
      switch (change.kind) {
        case "create":
          return api.createPayee(bookId, change.body);
        case "patch":
          return api.patchPayee(bookId, change.payeeId, change.body);
        case "merge":
          return api.mergePayees(bookId, change.targetId, change.sourceIds);
        case "delete":
          await api.deletePayee(bookId, change.payeeId);
          return null;
      }
    },
    onSuccess: invalidate,
  });
}

export function useCreatePayee(bookId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PayeeIn) => api.createPayee(bookId, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["books", bookId, "payees"] }),
  });
}

/** Every change to a tag, behind one hook -- the twin of `useChangePayee`. */
export type TagChange =
  | { kind: "create"; body: TagIn }
  | { kind: "patch"; tagId: string; body: TagPatch }
  | { kind: "delete"; tagId: string };

export function useChangeTag(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: async (change: TagChange) => {
      switch (change.kind) {
        case "create":
          return api.createTag(bookId, change.body);
        case "patch":
          return api.patchTag(bookId, change.tagId, change.body);
        case "delete":
          await api.deleteTag(bookId, change.tagId);
          return null;
      }
    },
    onSuccess: invalidate,
  });
}

export function useCreateTag(bookId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TagIn) => api.createTag(bookId, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["books", bookId, "tags"] }),
  });
}

export function useAttachments(bookId: string, transactionId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["books", bookId, "transactions", transactionId, "attachments"] as const,
    queryFn: () => api.attachments(bookId, transactionId),
  });
}

export function useUploadAttachment(bookId: string, transactionId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: Blob) => api.uploadAttachment(bookId, transactionId, file),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["books", bookId, "transactions", transactionId, "attachments"] }),
  });
}

export function useDeleteAttachment(bookId: string, transactionId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (attachmentId: string) => api.deleteAttachment(bookId, attachmentId),
    onSuccess: () =>
      void qc.invalidateQueries({ queryKey: ["books", bookId, "transactions", transactionId, "attachments"] }),
  });
}

export function useCreateTransaction(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: TransactionIn; idempotencyKey?: string }) =>
      api.createTransaction(bookId, body, idempotencyKey),
    onSuccess: invalidate,
  });
}

export function useUpdateTransaction(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: ({ transactionId, body }: { transactionId: string; body: TransactionUpdateIn }) =>
      api.updateTransaction(bookId, transactionId, body),
    onSuccess: invalidate,
  });
}

export function useDeleteTransaction(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: (transactionId: string) => api.deleteTransaction(bookId, transactionId),
    onSuccess: invalidate,
  });
}

export function useSetSplitState(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: ({
      transactionId,
      splitId,
      body,
    }: {
      transactionId: string;
      splitId: string;
      body: SetSplitStateIn;
    }) => api.setSplitState(bookId, transactionId, splitId, body),
    onSuccess: invalidate,
  });
}

export function useReconcileAccount(bookId: string, accountId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: (body: ReconcileIn) => api.reconcileAccount(bookId, accountId, body),
    onSuccess: invalidate,
  });
}

export type CommodityChange =
  | { kind: "create"; body: CommodityIn }
  | { kind: "patch"; commodityId: string; body: CommodityPatch }
  | { kind: "delete"; commodityId: string };

/**
 * Commodities are embedded in every amount the book hands out, so a changed
 * symbol or precision shows up everywhere: invalidate the whole book.
 */
export function useChangeCommodity(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: async (change: CommodityChange) => {
      switch (change.kind) {
        case "create":
          return api.createCommodity(bookId, change.body);
        case "patch":
          return api.patchCommodity(bookId, change.commodityId, change.body);
        case "delete":
          await api.deleteCommodity(bookId, change.commodityId);
          return null;
      }
    },
    onSuccess: invalidate,
  });
}

// -- prices and quotes --------------------------------------------------------

export function useQuoteProviders() {
  const api = useApi();
  return useQuery({ queryKey: keys.quoteProviders, queryFn: () => api.quoteProviders(), staleTime: Infinity });
}

/** Price history, page by page; filters are part of the key. */
export function usePrices(bookId: string, query: Omit<PricesQuery, "cursor"> = {}) {
  const api = useApi();
  return useInfiniteQuery({
    queryKey: keys.prices(bookId, query),
    queryFn: ({ pageParam }) => api.prices(bookId, pageParam ? { ...query, cursor: pageParam } : query),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.has_more ? (last.next_cursor ?? null) : null),
  });
}

export type PriceChange = { kind: "create"; body: PriceIn } | { kind: "delete"; priceId: string };

/** Prices feed every valuation: a change invalidates the book. */
export function useChangePrice(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: async (change: PriceChange) => {
      if (change.kind === "create") return api.createPrice(bookId, change.body);
      await api.deletePrice(bookId, change.priceId);
      return null;
    },
    onSuccess: invalidate,
  });
}

export function useLastQuoteRun(bookId: string) {
  const api = useApi();
  return useQuery({ queryKey: keys.lastQuoteRun(bookId), queryFn: () => api.lastQuoteRun(bookId) });
}

export function useFetchQuotes(bookId: string) {
  const api = useApi();
  const qc = useQueryClient();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: (body: QuoteFetchIn = {}) => api.fetchQuotes(bookId, body),
    onSuccess: (run) => {
      qc.setQueryData(keys.lastQuoteRun(bookId), run);
      invalidate();
    },
  });
}

// -- scheduled / recurring transactions -------------------------------------

export function useSchedules(bookId: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.schedules(bookId),
    queryFn: () => api.schedules(bookId),
    staleTime: 60_000,
  });
}

/** The "due" panel: what needs posting. Polled lightly; a write invalidates it. */
export function useDueSchedules(bookId: string, on?: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.dueSchedules(bookId, on),
    queryFn: () => api.dueSchedules(bookId, on),
    staleTime: 30_000,
  });
}

export type ScheduleChange =
  | { kind: "create"; body: ScheduleIn }
  | { kind: "update"; scheduleId: string; body: SchedulePatch }
  | { kind: "delete"; scheduleId: string };

export function useChangeSchedule(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: async (change: ScheduleChange) => {
      switch (change.kind) {
        case "create":
          return api.createSchedule(bookId, change.body);
        case "update":
          return api.updateSchedule(bookId, change.scheduleId, change.body);
        case "delete":
          await api.deleteSchedule(bookId, change.scheduleId);
          return null;
      }
    },
    onSuccess: invalidate,
  });
}

export function usePostOccurrence(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: ({
      scheduleId,
      body,
      idempotencyKey,
    }: {
      scheduleId: string;
      body?: PostOccurrenceIn;
      idempotencyKey?: string;
    }) => api.postOccurrence(bookId, scheduleId, body ?? {}, idempotencyKey),
    onSuccess: invalidate,
  });
}

export function useSkipOccurrence(bookId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: ({ scheduleId, on }: { scheduleId: string; on?: string }) =>
      api.skipOccurrence(bookId, scheduleId, on),
    onSuccess: invalidate,
  });
}

// -- CSV statement import ----------------------------------------------------
//
// A wizard, not a reactive view: inspect the upload, preview it against the
// book, commit the reviewed rows. Only the commit changes anything, so only
// the commit invalidates the book.

export function useInspectCsv(bookId: string) {
  const api = useApi();
  return useMutation({ mutationFn: (file: Blob) => api.inspectCsv(bookId, file) });
}

export function usePreviewCsv(bookId: string, accountId: string) {
  const api = useApi();
  return useMutation({
    mutationFn: ({ file, mapping }: { file: Blob; mapping: ColumnMapping }) =>
      api.previewCsv(bookId, accountId, file, mapping),
  });
}

export function useCommitCsv(bookId: string, accountId: string) {
  const api = useApi();
  const invalidate = useInvalidateBook(bookId);
  return useMutation({
    mutationFn: ({
      file,
      mapping,
      decisions,
      uncategorisedAccountId,
    }: {
      file: Blob;
      mapping: ColumnMapping;
      decisions: ImportDecision[];
      uncategorisedAccountId?: string | undefined;
    }) => api.commitCsv(bookId, accountId, file, mapping, decisions, uncategorisedAccountId),
    onSuccess: invalidate,
  });
}

export function useImportMapping(bookId: string, accountId: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.importMapping(bookId, accountId),
    queryFn: () => api.importMapping(bookId, accountId),
    staleTime: Infinity,
  });
}

export function useSaveImportMapping(bookId: string, accountId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mapping: ColumnMapping) => api.saveImportMapping(bookId, accountId, mapping),
    onSuccess: (saved) => qc.setQueryData(keys.importMapping(bookId, accountId), saved),
  });
}

export function useImportKmymoney() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, name, roundingAdjustments, skipPrices }: { file: Blob; name: string; roundingAdjustments?: boolean; skipPrices?: boolean }) =>
      api.importKmymoney(file, name, { roundingAdjustments, skipPrices }),
    onSuccess: (result) => {
      qc.setQueryData(keys.book(result.book.id), result.book);
      void qc.invalidateQueries({ queryKey: keys.books });
    },
  });
}

export function useUpdateMe() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { display_name?: string; locale?: string }) => api.patchMe(body),
    onSuccess: (user) => qc.setQueryData(keys.session, user),
  });
}
