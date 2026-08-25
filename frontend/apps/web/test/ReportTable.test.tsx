import type { Account, Amount, ReportRow } from "@pymoney/core";
import { sectionRows } from "@pymoney/core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ReportTable } from "../src/components/ReportTable";

const eur = (minor: number): Amount => ({ commodity_id: "eur", commodity: "EUR", minor, scale: 100, text: "" });
const row = (id: string, path: string, depth: number, value: number, display = value, own = value): ReportRow => ({
  account_id: id,
  path,
  depth,
  value: eur(value),
  display_value: eur(display),
  own_value: eur(own),
});
const acct = (id: string, account_class: Account["account_class"]) => ({ id, account_class }) as unknown as Account;

const rows = [
  row("a", "Assets", 0, 1500, 1500, 0),
  row("a1", "Assets:Bank", 1, 1500),
  row("l", "Liabilities", 0, -20000, 20000, -5000),
  row("l1", "Liabilities:Card", 1, -15000, 15000),
];
const accounts = [acct("a", "ASSET"), acct("a1", "ASSET"), acct("l", "LIABILITY"), acct("l1", "LIABILITY")];

describe("ReportTable", () => {
  it("renders leaf names indented by depth and natural-signed amounts", () => {
    render(<ReportTable sections={sectionRows(rows, accounts, ["ASSET", "LIABILITY"])} />);
    expect(screen.getByText("Bank")).toBeInTheDocument();
    expect(screen.getByText("Card")).toBeInTheDocument();
    // Liabilities read positive, as the server's display_value says.
    const card = screen.getByText("Card").closest("tr")!;
    expect(within(card).getByText("150.00")).not.toHaveClass("money--negative");
  });

  it("shows the parent's own value with the same sign as its subtree", () => {
    render(<ReportTable sections={sectionRows(rows, accounts, ["ASSET", "LIABILITY"])} />);
    const liabilities = screen.getByText("Liabilities").closest("tr")!;
    // own_value is stored as -5000 and display_value flipped the subtree: show 50.00.
    expect(within(liabilities).getByText("50.00")).toBeInTheDocument();
  });

  it("skips the section header when the class has a single root row", () => {
    render(<ReportTable sections={sectionRows(rows, accounts, ["ASSET"])} />);
    expect(screen.getAllByText("Assets")).toHaveLength(1);
    expect(screen.queryByRole("rowgroup", { name: "Assets" })).toBeNull();
  });

  it("collapses and expands a subtree", async () => {
    const user = userEvent.setup();
    render(<ReportTable sections={sectionRows(rows, accounts, ["ASSET", "LIABILITY"])} />);
    expect(screen.getByText("Bank")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Collapse Assets" }));
    expect(screen.queryByText("Bank")).toBeNull();
    expect(screen.getByText("Card")).toBeInTheDocument(); // siblings untouched
    await user.click(screen.getByRole("button", { name: "Expand Assets" }));
    expect(screen.getByText("Bank")).toBeInTheDocument();
  });

  it("renders totals in the footer with the commodity", () => {
    render(
      <ReportTable
        sections={sectionRows(rows, accounts, ["ASSET"])}
        totals={[{ label: "Net worth", amount: eur(130000), emphasis: true }]}
      />,
    );
    const foot = screen.getByText("Net worth").closest("tr")!;
    expect(within(foot).getByText("1,300.00 EUR")).toBeInTheDocument();
  });

  it("says so when there is nothing", () => {
    render(<ReportTable sections={[]} />);
    expect(screen.getByText(/nothing to show/i)).toBeInTheDocument();
  });
});
