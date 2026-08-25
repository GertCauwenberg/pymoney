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
export type Book = Schemas["BookOut"];
export type Account = Schemas["AccountOut"];
export type AccountClass = Schemas["AccountClass"];
export type AccountType = Schemas["AccountType"];
export type Commodity = Schemas["CommodityOut"];
export type CommodityKind = Schemas["CommodityKind"];
export type User = Schemas["UserOut"];
export type Session = Schemas["SessionOut"];
export type LoginIn = Schemas["LoginIn"];
export type RegisterIn = Schemas["RegisterIn"];
export type Health = Schemas["HealthOut"];

type Query<Op extends keyof operations> = NonNullable<
  operations[Op]["parameters"]["query"]
>;

/** Query parameters of the three report endpoints, minus the path. */
export type BalanceSheetQuery = Query<"get_balance_sheet_books__book_id__reports_balance_sheet_get">;
export type IncomeStatementQuery = Query<"get_income_statement_books__book_id__reports_income_statement_get">;
export type HoldingsQuery = Query<"get_holdings_books__book_id__reports_holdings_get">;
export type AccountsQuery = Query<"list_accounts_books__book_id__accounts_get">;
export type CommoditiesQuery = Query<"list_commodities_books__book_id__commodities_get">;

/** The body of every non-2xx response the server produces itself. */
export interface Problem {
  error: {
    type: string;
    message: string;
    status: number;
  };
}
