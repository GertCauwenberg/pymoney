import { useT } from "@pymoney/core/hooks";
import { ApiError } from "@pymoney/core";

export function describeError(error: unknown, t: (key: string) => string = (k) => k): { title: string; detail: string } {
  if (error instanceof ApiError) {
    const kind =
      error.status === 422
        ? t("The report could not be produced")
        : error.status === 404
          ? t("Not found")
          : error.status === 403
            ? t("Not allowed")
            : error.status >= 500
              ? t("Server error")
              : t("Request failed");
    return { title: kind, detail: `${error.message} (${error.type}, HTTP ${error.status})` };
  }
  if (error instanceof Error) return { title: t("Something went wrong"), detail: error.message };
  return { title: t("Something went wrong"), detail: String(error) };
}

export function ErrorBanner({
  error,
  title,
  onRetry,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}) {
  const t = useT();
  const described = describeError(error, t);
  return (
    <div className="banner banner--error" role="alert">
      <strong>{title ?? described.title}.</strong> <span>{described.detail}</span>
      {onRetry && (
        <button type="button" className="btn btn--small" onClick={onRetry}>
          {t("Retry")}
        </button>
      )}
    </div>
  );
}
