/** The mark: a coin with a rising line, drawn inline so it follows no asset
 * pipeline and prints crisply. Decorative -- the wordmark next to it is the
 * accessible name. */
export function Logo({ className = "brand__mark" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="pm-coin" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2f8bdd" />
          <stop offset="1" stopColor="#0b62b8" />
        </linearGradient>
      </defs>
      <circle cx="16" cy="16" r="15" fill="url(#pm-coin)" />
      <circle cx="16" cy="16" r="15" fill="none" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
      <circle cx="16" cy="16" r="11.5" fill="none" stroke="rgba(255,255,255,0.25)" strokeWidth="1" strokeDasharray="2.5 2.5" />
      <path d="M8 20.5 13 15l3.5 3L24 10.5" fill="none" stroke="#ffffff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M20.5 10.5H24V14" fill="none" stroke="#ffffff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="10.5" cy="23" r="1.6" fill="#e69f00" />
    </svg>
  );
}
