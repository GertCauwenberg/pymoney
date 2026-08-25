/** Export and print. The CSV is built by the core, so every client agrees. */
export function ReportActions({ csv, filename }: { csv: (() => string) | null; filename: string }) {
  const download = () => {
    if (!csv) return;
    const blob = new Blob([csv()], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="actions">
      <button type="button" className="btn" onClick={download} disabled={!csv}>
        Export CSV
      </button>
      <button type="button" className="btn" onClick={() => window.print()}>
        Print
      </button>
    </div>
  );
}
