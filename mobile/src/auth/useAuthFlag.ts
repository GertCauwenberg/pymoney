/**
 * The auth gate input (E1.1): `null` while the session probe is in flight (the
 * splash holds), then whether a user is signed in. Fed to `Stack.Protected`
 * guards — routing follows the session, never a `useSegments` redirect.
 */
import { useSession } from "@pymoney/core/hooks";

export function useAuthFlag(): boolean | null {
  const session = useSession();
  if (session.isPending) return null;
  return session.data != null;
}
