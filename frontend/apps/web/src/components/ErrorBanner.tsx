import { ApiError } from "@pymoney/core";

export function describeError(error: unknown): { title: string; detail: string } {
  if (error instanceof ApiError) {
    const kind =
      error.status === 422
        ? "The report could not be produced"
        : error.status === 404
          ? "Not found"
          : error.status === 403
            ? "Not allowed"
            : error.status >= 500
              ? "Server error"
              : "Request failed";
    return { title: kind, detail: `${error.message} (${error.type}, HTTP ${error.status})` };
  }
  if (error instanceof Error) return { title: "Something went wrong", detail: error.message };
  return { title: "Something went wrong", detail: String(error) };
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
  const described = describeError(error);
  return (
    <div className="banner banner--error" role="alert">
      <strong>{title ?? described.title}.</strong> <span>{described.detail}</span>
      {onRetry && (
        <button type="button" className="btn btn--small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
