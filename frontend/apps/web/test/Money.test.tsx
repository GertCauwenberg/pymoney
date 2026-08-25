import { render, screen } from "@testing-library/react";

import { Money } from "../src/components/Money";

const amt = (minor: number) => ({ commodity_id: "c", commodity: "EUR", minor, scale: 100, text: String(minor / 100) });

describe("Money", () => {
  it("keeps the exact minor units on the element", () => {
    render(<Money amount={amt(-123456)} />);
    const el = screen.getByText("-1,234.56");
    expect(el).toHaveAttribute("data-minor", "-123456");
    expect(el).toHaveClass("money--negative");
  });
  it("renders a dash for a missing amount", () => {
    render(<Money amount={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
  it("can blank zeros", () => {
    const { container } = render(<Money amount={amt(0)} blankZero />);
    expect(container.querySelector(".money")?.textContent).toBe("");
  });
});
