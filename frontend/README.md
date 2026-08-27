# PyMoney frontend

React clients for the PyMoney API. Two npm workspaces:

```
frontend/
  packages/core/   @pymoney/core  platform-agnostic: types, API client, money
                                  formatting, periods, React Query hooks. No DOM.
  apps/web/        @pymoney/web   the web app: Vite + React + react-router
```

The split is the point. `@pymoney/core` has no dependency on the browser — it
needs a `fetch`, somewhere to keep credentials, and React. A React Native app
(Expo) or a desktop shell (Tauri, Electron) imports the same client, the same
hooks, the same formatting and the same report helpers, and only writes UI.
Everything that could be wrong about money or about the server lives in one
place and is tested once.

## Running it

```bash
make serve                 # the API on :8000 (PYMONEY_COOKIE_SECURE=0 for plain http)
make seed-demo             # optional: a small, fully priced demo book
make web-install           # npm ci
make web-dev               # http://localhost:5173
```

In development Vite proxies `/api/*` to the API, so the browser sees a single
origin: no CORS, no cookie-domain settings. See `apps/web/vite.config.ts` and
`apps/web/.env.example`.

```bash
make web-check             # what CI runs: generated types current, typecheck, tests, build
make web-test              # just the tests
make web-build             # production build -> apps/web/dist
```

## The contract

`packages/core/src/generated/openapi.d.ts` is generated from `docs/openapi.json`
with `openapi-typescript` and committed. `npm run check` regenerates it and
fails if it differs, exactly as the Python side fails when `docs/openapi.json`
drifts from the code. So: the server cannot change a field without the spec
changing, the spec cannot change without the client types changing, and the
client types cannot change without the frontend build noticing. No hand-written
wire types anywhere: `packages/core/src/types.ts` only gives the generated ones
friendlier names.

## What the core honours (see the main README, "What the web and desktop clients have to honour")

- **Amounts are never floats.** `Amount.minor` and `Amount.scale` are integers
  and every formatting operation in `money.ts` is BigInt string manipulation.
  Nothing is divided, ever. `formatMinor(9007199254740993n, 100)` gives the
  right last digit; a float would not.
- **Rates are exact.** `formatRatePercent` computes from `num`/`den` with
  integer rounding. `approx` is used only where the server itself says it is
  for display.
- **Two auth modes, mirroring the server.** The web app uses the session
  cookie and sends `X-CSRF-Token` on unsafe methods. A client created with
  `credentials: "omit"` (mobile, desktop) keeps the bearer token the server
  returns once at login and sends `Authorization: Bearer` instead; it never
  sends cookies, so a stray cookie can never be the credential actually used.
  Where the credential is stored is an `AuthStore` the app provides:
  sessionStorage on the web, the keychain on a phone.
- **Errors are the server's.** `ApiError` carries the `type`, `message` and
  `status` from the API's problem body (or FastAPI's validation `detail`) and
  the `X-PyMoney-Revision` header. The UI shows the server's explanation rather
  than inventing its own.

## Books and sharing

A new user lands on a "create your first book" form (name and base currency;
the common currencies are a dropdown, anything else can be typed). The books
page also creates further books; with a single book it redirects straight into
it unless reached on purpose through the PyMoney brand link.

Owners get a **Sharing** tab: who has access, add someone by the email they
log in with, change roles (viewer / editor / owner), revoke. The server's rule
that a book must keep at least one owner is reflected in the UI (the last
owner's controls are disabled) and its 409 is shown if it fires anyway. Grants
are keyed on the email string and the server does not check that an account
exists, so the page says so: a typo grants access to nobody.

## The reports

Three pages, one per report endpoint. All settings live in the URL, so a
report can be bookmarked or sent to the other account holder.

- **Balance sheet** — as-of date, report currency, closed accounts, strict
  mode. Sections by account class (rows carry a path but not a class; the
  account list supplies it), collapsible subtrees, totals from the payload.
- **Income statement** — period presets (this/last month, quarter, year to
  date, last year, last 12 months) or any range; savings rate from the exact
  ratio.
- **Holdings** — quantity, price, cost, market value, unrealised gain.

Every account name in a report is a link to that account's **register** for
the same window (the income statement's period, or everything up to the
balance sheet's as-of date), and every register row links to its
**transaction** with all its splits, each of which links back to its account's
register.

## Writing

What a user may change follows the server's roles, and the UI shows only what
the server would accept: a **viewer** sees no write controls; an **editor**
posts, edits and deletes transactions and can add payees; an **owner** also
manages the chart of accounts (the `Accounts` tab) and sharing.

- **Posting** (`New transaction` on a register) -- the register's account is
  the first leg; pick the category for the second, type one amount and leave
  the other blank: the server fills it with whatever balances the entry, and
  the form shows that figure as you type. More legs can be added. Amounts are
  typed as decimals and sent as the strings they are (`packages/core/src/ledger.ts`
  parses them into BigInt minor units for the running sum; nothing is ever a
  float). Money into an account is positive, out of it negative, as stored --
  this is the one place the natural sign is *not* applied, because the form
  edits the ledger's own figures. A leg whose account is held in another
  commodity than the transaction currency gets a quantity field. A payee can
  be created from the payee picker ("New payee…") and is created just before
  the post. Each form instance sends one `Idempotency-Key`, so a retry after a
  dropped connection cannot post twice.
- **Editing** (`Edit` on a transaction) -- the stored splits come back as exact
  decimals; the whole set of legs is sent again (the server never patches half
  a transaction), carrying each split's reconciliation mark, action and number
  so that fixing a memo does not un-clear a split. Reconciled transactions
  cannot be edited or deleted and the buttons say why.
- **Accounts** -- create (type, parent of the same class, commodity,
  placeholder), rename, move, edit the descriptive fields, close or reopen,
  delete. Each kind of change goes to its own endpoint, in order, stopping at
  the first the server refuses. Type and commodity are fixed once an account
  exists, as on the server. The register's `Edit account` link opens the
  editor for that account (`/accounts?edit=<id>`).

- **Commodities** (the `Commodities` tab; editors and owners) -- add a
  currency or a security (code, name, kind, smallest unit, symbol, ISIN, quote
  source), edit the display fields, delete. The smallest unit is fixed once a
  commodity exists, as on the server, and the list says up front why a delete
  would be refused (the base currency; accounts still using it).

The form-side checks (`validateDraft`) only catch what would be refused
anyway -- a missing account, an amount with too many decimals, two blank
legs, a placeholder or closed account -- so the user hears about it before a
round trip. The server keeps the final say and its message is shown verbatim.

- **Register** (`/books/:id/accounts/:accountId`) — one row per leg, oldest
  first, with reconciliation mark, running balance, opening and closing
  balances from the server's `totals`, cursor-based "load more", optional
  date window and sub-accounts. Amounts carry the reports' natural sign
  (income positive, a credit card's charges positive), so the closing balance
  is the figure that was clicked on. A Value column appears only when a leg
  was booked in another commodity than the account's.
- **Transaction** (`/books/:id/transactions/:transactionId`) — the facts and
  every split, values shown *as stored* and summed, because a detail view is
  the place where double entry should be visible.

Every report shows **how the numbers came to be**: the exchange rates the
server used (with their sources and, for chained rates, the path) and, in
lenient mode, what it could not value and left out. The "fail if anything
cannot be valued" toggle is the server's `strict` flag; the pages default to
lenient with the omissions listed loudly, because an error page tells you less
than a report with a warning on it.

`display_value` is shown (the server signs it to read naturally: debt and
income positive). `own_value` comes "as stored", so `displayOwnValue` gives it
the same sign convention before it sits next to the subtree total.

CSV export and print styles are there. The CSV is built in the core, so the
mobile app's export will be byte-identical.

## Adding a mobile or desktop app

Create `apps/mobile` (or `apps/desktop`) in this workspace and depend on
`@pymoney/core`. You need to provide:

1. An `ApiClient` with `credentials: "omit"` and an `AuthStore` backed by
   secure storage.
2. An `<ApiProvider client={...}>` at the root.
3. Screens. `useBalanceSheet`, `useIncomeStatement`, `useHoldings`,
   `useAccounts`, `useCommodities`, `useBooks`, `useSession`, `useLogin`,
   `useLogout`, and the mutations `useCreateTransaction`,
   `useUpdateTransaction`, `useDeleteTransaction`, `useCreatePayee`,
   `useChangeAccount`, `useChangeCommodity` are all in `@pymoney/core/hooks`; `sectionRows`,
   `visibleRows`, `periodFor`, `formatAmount`, `rowsToCsv`, and the
   transaction-draft helpers (`newDraft`, `validateDraft`, `draftBalance`,
   `draftToTransactionIn`, `draftFromTransaction`) in `@pymoney/core`.

Nothing in `packages/core` imports from `react-dom`, `react-router` or the DOM
types; `tsconfig.json` there has `lib: ["ES2022"]` only, so a DOM API creeping
in is a type error.

## Deploying the web app

`apps/web/dist` is static. Either serve it from the same origin as the API
with a reverse proxy mapping `/api/` to uvicorn (and strip the prefix, or set
`PYMONEY_ROOT_PATH`), or host it anywhere, set `VITE_API_BASE` at build time
to the API's URL and list the web origin in `PYMONEY_CORS_ORIGINS`. Cookies
cross origins only with `PYMONEY_COOKIE_SAMESITE=none` and HTTPS; the same
origin is simpler and is what the dev proxy imitates.
