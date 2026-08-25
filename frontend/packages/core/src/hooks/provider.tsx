import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useMemo, type ReactNode } from "react";

import { ApiClient, ApiError } from "../client";

const ApiContext = createContext<ApiClient | null>(null);

export function useApi(): ApiClient {
  const client = useContext(ApiContext);
  if (!client) throw new Error("useApi must be used inside <ApiProvider>");
  return client;
}

/** Defaults sensible for a ledger: fresh for a while, never retry a 4xx. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status < 500) return false;
          return failureCount < 2;
        },
      },
    },
  });
}

export function ApiProvider({
  client,
  queryClient,
  children,
}: {
  client: ApiClient;
  queryClient?: QueryClient;
  children: ReactNode;
}) {
  const qc = useMemo(() => queryClient ?? createQueryClient(), [queryClient]);
  return (
    <ApiContext.Provider value={client}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </ApiContext.Provider>
  );
}
