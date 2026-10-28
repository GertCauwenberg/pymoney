/**
 * In-memory stand-in for the `expo-sqlite` native module (E1.2): jest must
 * never load the real thing. Implements exactly the surface `SqliteKvStore`
 * uses — the `kv` table in `pymoney.db`. Tests seed relaunch state through
 * `mockKv` and reset it in `beforeEach`:
 *
 *   jest.mock("expo-sqlite", () => require("./expoSqliteMock"));
 */
export const mockKv: Record<string, string> = {};

export async function openDatabaseAsync(_name: string) {
  return {
    async execAsync(_source: string): Promise<void> {},

    async runAsync(source: string, ...params: unknown[]): Promise<void> {
      if (source.includes("INSERT INTO kv")) {
        mockKv[String(params[0])] = String(params[1]);
      } else if (source.includes("DELETE FROM kv")) {
        delete mockKv[String(params[0])];
      }
    },

    async getFirstAsync<T>(source: string, ...params: unknown[]): Promise<T | null> {
      if (source.includes("SELECT value FROM kv")) {
        const key = String(params[0]);
        return (key in mockKv ? { value: mockKv[key] } : null) as T | null;
      }
      return null;
    },
  };
}
