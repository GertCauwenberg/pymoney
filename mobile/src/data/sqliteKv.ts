/**
 * The real device store (E1.2): one small `kv` table in `pymoney.db`, the same
 * database the offline mirror (E5.1) will grow. Kept behind `KvStore` so jest
 * never loads the native module.
 */
import * as SQLite from "expo-sqlite";

import type { KvStore } from "./kv";

export class SqliteKvStore implements KvStore {
  private constructor(private readonly db: SQLite.SQLiteDatabase) {}

  static async open(): Promise<SqliteKvStore> {
    const db = await SQLite.openDatabaseAsync("pymoney.db");
    await db.execAsync(
      "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)",
    );
    return new SqliteKvStore(db);
  }

  async get(key: string): Promise<string | null> {
    const row = await this.db.getFirstAsync<{ value: string }>(
      "SELECT value FROM kv WHERE key = ?",
      key,
    );
    return row?.value ?? null;
  }

  async set(key: string, value: string): Promise<void> {
    await this.db.runAsync(
      "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      key,
      value,
    );
  }

  async delete(key: string): Promise<void> {
    await this.db.runAsync("DELETE FROM kv WHERE key = ?", key);
  }
}
