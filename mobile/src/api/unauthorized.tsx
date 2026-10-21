import { createContext, useContext, type ReactNode } from "react";

/** Set when the server rejects the token mid-session. */
export interface UnauthorizedEvent {
  at: number;
}

export interface UnauthorizedState {
  event: UnauthorizedEvent | null;
  consume: () => void;
}

const UnauthorizedContext = createContext<UnauthorizedState>({
  event: null,
  consume: () => undefined,
});

/** The mid-session 401 signal. `event` is non-null until `consume()`. */
export function useUnauthorized(): UnauthorizedState {
  return useContext(UnauthorizedContext);
}

export function UnauthorizedProvider({
  value,
  children,
}: {
  value: UnauthorizedState;
  children: ReactNode;
}) {
  return (
    <UnauthorizedContext.Provider value={value}>{children}</UnauthorizedContext.Provider>
  );
}
