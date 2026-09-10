/**
 * Friendly names for the wire types.
 *
 * Everything here is derived from `generated/openapi.d.ts`, which is in turn
 * generated from `docs/openapi.json`, the contract the server commits to. Do
 * not hand-write a field: if the server does not send it, the type will not
 * have it, and that is the point.
 */
import type { components, operations } from "./generated/openapi";

type Schemas = components["schemas"];

export type Amount = Schemas["AmountOut"];
export type Rate = Schemas["RateOut"];
export type Conversion = Schemas["ConversionOut"];
export type ReportRow = Schemas["ReportRowOut"];
export type BalanceSheet = Schemas["BalanceSheetOut"];
export type IncomeStatement = Schemas["IncomeStatementOut"];
export type Holdings = Schemas["HoldingsOut"];
export type Holding = Schemas["HoldingOut"];
export type NetWorthSeries = Schemas["NetWorthSeriesOut"];
export type NetWorthPoint = Schemas["NetWorthPointOut"];
export type PeriodTable = Schemas["PeriodTableOut"];
export type PeriodRow = Schemas["PeriodRowOut"];
export type PeriodColumn = Schemas["PeriodOut"];
export type ReportInterval = NetWorthSeries["interval"];
export type Book = Schemas["BookOut"];
export type BookIn = Schemas["BookIn"];
export type ImportResult = Schemas["ImportOut"];
export type Grant = Schemas["GrantOut"];
export type GrantIn = Schemas["GrantIn"];
export type Role = GrantIn["role"];
export type Account = Schemas["AccountOut"];
export type AccountIn = Schemas["AccountIn"];
export type AccountPatch = Schemas["AccountPatch"];
export type AccountClass = Schemas["AccountClass"];
export type AccountType = Schemas["AccountType"];
export type Commodity = Schemas["CommodityOut"];
export type CommodityIn = Schemas["CommodityIn"];
export type CommodityPatch = Schemas["CommodityPatch"];
export type CommodityKind = Schemas["CommodityKind"];
export type RegisterPage = Schemas["RegisterPage"];
export type RegisterRow = Schemas["RegisterRowOut"];
export type RegisterTotals = Schemas["RegisterTotals"];
export type Transaction = Schemas["TransactionOut"];
export type TransactionIn = Schemas["TransactionIn"];
export type TransactionUpdateIn = Schemas["TransactionUpdateIn"];
export type TransactionSearchResult = Schemas["TransactionSearchOut"];
export type Change = Schemas["ChangeOut"];
export type LegIn = Schemas["LegIn"];
export type Split = Schemas["SplitOut"];
export type Payee = Schemas["PayeeOut"];
export type Attachment = Schemas["AttachmentOut"];
export type Price = Schemas["PriceOut"];
export type PriceIn = Schemas["PriceIn"];
export type PricePage = Schemas["PricePage"];
export type QuoteProvider = Schemas["QuoteProviderOut"];
export type QuoteFetchIn = Schemas["QuoteFetchIn"];
export type QuoteRun = Schemas["QuoteRunOut"];
export type PayeeIn = Schemas["PayeeIn"];
export type PayeePatch = Schemas["PayeePatch"];
export type Tag = Schemas["TagOut"];
export type TagIn = Schemas["TagIn"];
export type TagPatch = Schemas["TagPatch"];
export type ReconcileState = Schemas["ReconcileStateName"];
export type ReconcileIn = Schemas["ReconcileIn"];
export type SetSplitStateIn = Schemas["SetSplitStateIn"];
export type User = Schemas["UserOut"];
export type Session = Schemas["SessionOut"];
export type LoginIn = Schemas["LoginIn"];
export type RegisterIn = Schemas["RegisterIn"];
export type Pending = Schemas["PendingOut"];
export type Health = Schemas["HealthOut"];

// -- scheduled / recurring transactions -------------------------------------
export type Schedule = Schemas["ScheduleOut"];
export type ScheduleIn = Schemas["ScheduleIn"];
export type SchedulePatch = Schemas["SchedulePatch"];
export type Recurrence = Schemas["RecurrenceModel"];
export type Frequency = Recurrence["frequency"];
export type ScheduleTemplate = Schemas["ScheduleTemplateOut"];
export type ScheduleTemplateIn = Schemas["ScheduleTemplateIn"];
export type TemplateLeg = Schemas["TemplateLegOut"];
export type TemplateLegIn = Schemas["TemplateLegIn"];
export type DuePanel = Schemas["DuePanelOut"];
export type DueSchedule = Schemas["DueScheduleOut"];
export type PostOccurrenceIn = Schemas["PostOccurrenceIn"];

// -- CSV statement import ----------------------------------------------------
export type ColumnMapping = Schemas["ColumnMappingModel"];
export type AmountKind = ColumnMapping["amount_kind"];
export type CsvInspect = Schemas["CsvInspectOut"];
export type CsvPreview = Schemas["CsvPreviewOut"];
export type ImportProposal = Schemas["ImportProposalOut"];
export type ProposalStatus = ImportProposal["status"];
export type RowAction = ImportProposal["default_action"];
export type StatementImport = Schemas["StatementImportOut"];
export type ImportProblem = Schemas["ImportProblemOut"];

/**
 * One statement row's committed verdict, keyed by its `row_key`. Hand-written,
 * not generated: it is a field inside a multipart form (a JSON string), not a
 * request body, so FastAPI never puts it in the contract. Kept in step with
 * `pymoney.api.schemas.ImportDecisionIn` -- the matching test on the server
 * side is what catches drift.
 */
export interface ImportDecision {
  row_key: string;
  action?: RowAction;
  payee_id?: string | null;
  new_payee?: string;
  category_id?: string | null;
  memo?: string;
}

type Query<Op extends keyof operations> = NonNullable<
  operations[Op]["parameters"]["query"]
>;

/** Query parameters of the three report endpoints, minus the path. */
export type BalanceSheetQuery = Query<"get_balance_sheet_books__book_id__reports_balance_sheet_get">;
export type IncomeStatementQuery = Query<"get_income_statement_books__book_id__reports_income_statement_get">;
export type HoldingsQuery = Query<"get_holdings_books__book_id__reports_holdings_get">;
export type NetWorthQuery = Query<"get_net_worth_books__book_id__reports_net_worth_get">;
export type PeriodTableQuery = Query<"get_period_table_books__book_id__reports_period_table_get">;
export type AccountsQuery = Query<"list_accounts_books__book_id__accounts_get">;
export type CommoditiesQuery = Query<"list_commodities_books__book_id__commodities_get">;
export type RegisterQuery = Query<"register_books__book_id__accounts__account_id__register_get">;
export type TransactionSearchQuery = Query<"search_transactions_route_books__book_id__transactions_search_get">;
export type PayeesQuery = Query<"list_payees_books__book_id__payees_get">;
export type TagsQuery = Query<"list_tags_books__book_id__tags_get">;
export type PricesQuery = Query<"list_prices_books__book_id__prices_get">;

/** The body of every non-2xx response the server produces itself. */
export interface Problem {
  error: {
    type: string;
    message: string;
    status: number;
  };
}
