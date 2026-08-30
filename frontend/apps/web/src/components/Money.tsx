import { formatAmount, isNegative, isZero } from "@pymoney/core";
import type { Amount } from "@pymoney/core";
import { useLocale } from "@pymoney/core/hooks";

/**
 * An amount in a table cell: right-aligned tabular digits, negatives marked,
 * zero optionally quiet. The exact `minor` is kept on the element so copying
 * or testing has the authoritative value, not the localised string.
 */
export function Money({
  amount,
  withCommodity = false,
  blankZero = false,
  strong = false,
}: {
  amount: Amount | null | undefined;
  withCommodity?: boolean;
  blankZero?: boolean;
  strong?: boolean;
}) {
  const locale = useLocale();
  if (!amount) return <span className="money money--na">—</span>;
  const classes = ["money"];
  if (isNegative(amount)) classes.push("money--negative");
  if (isZero(amount)) classes.push("money--zero");
  if (strong) classes.push("money--strong");
  return (
    <span className={classes.join(" ")} data-minor={amount.minor} title={`${amount.text} ${amount.commodity}`}>
      {formatAmount(amount, { withCommodity, blankZero, locale })}
    </span>
  );
}
