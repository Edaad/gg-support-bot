export function errText(e: unknown, fallback: string): string {
  return e instanceof Error ? e.message : fallback
}
