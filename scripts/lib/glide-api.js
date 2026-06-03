/**
 * Minimal Glide API v2 client for Big Tables.
 * @see https://apidocs.glideapps.com/api-reference/v2/general/introduction
 */

const DEFAULT_BASE_URL = "https://api.glideapps.com";
const DEFAULT_PAGE_LIMIT = 500;
const DEFAULT_UPDATE_DELAY_MS = 250;
const MAX_RETRIES = 5;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseRetryAfterMs(headerValue) {
  if (!headerValue) return null;
  const seconds = Number(headerValue);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return seconds * 1000;
  }
  const dateMs = Date.parse(headerValue);
  if (Number.isFinite(dateMs)) {
    return Math.max(0, dateMs - Date.now());
  }
  return null;
}

export class GlideApiError extends Error {
  constructor(message, { status, body } = {}) {
    super(message);
    this.name = "GlideApiError";
    this.status = status;
    this.body = body;
  }
}

export class GlideClient {
  /**
   * @param {object} options
   * @param {string} options.token - Bearer token (GLIDE_API_TOKEN)
   * @param {string} options.tableId - Big Table ID
   * @param {string} [options.baseUrl]
   * @param {number} [options.pageLimit]
   * @param {number} [options.updateDelayMs] - Delay between PATCH requests
   */
  constructor({
    token,
    tableId,
    baseUrl = DEFAULT_BASE_URL,
    pageLimit = DEFAULT_PAGE_LIMIT,
    updateDelayMs = DEFAULT_UPDATE_DELAY_MS,
  }) {
    if (!token) throw new Error("GLIDE_API_TOKEN is required");
    if (!tableId) throw new Error("GLIDE_PAYMENT_METHODS_TABLE_ID is required");
    this.token = token;
    this.tableId = tableId;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.pageLimit = pageLimit;
    this.updateDelayMs = updateDelayMs;
  }

  async request(method, path, { query, body } = {}) {
    const url = new URL(`${this.baseUrl}${path}`);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null) {
          url.searchParams.set(key, String(value));
        }
      }
    }

    let attempt = 0;
    while (true) {
      const response = await fetch(url, {
        method,
        headers: {
          Authorization: `Bearer ${this.token}`,
          Accept: "application/json",
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });

      const text = await response.text();
      let parsed;
      if (text) {
        try {
          parsed = JSON.parse(text);
        } catch {
          parsed = text;
        }
      }

      if (response.ok) {
        return parsed ?? {};
      }

      const retryable =
        response.status === 429 ||
        response.status === 502 ||
        response.status === 503 ||
        response.status === 504;

      if (retryable && attempt < MAX_RETRIES) {
        const retryAfter =
          parseRetryAfterMs(response.headers.get("retry-after")) ??
          Math.min(30_000, 500 * 2 ** attempt);
        attempt += 1;
        await sleep(retryAfter);
        continue;
      }

      const errMsg =
        typeof parsed === "object" && parsed?.error?.message
          ? parsed.error.message
          : `Glide API ${method} ${path} failed (${response.status})`;
      throw new GlideApiError(errMsg, { status: response.status, body: parsed });
    }
  }

  /**
   * Fetch all rows with continuation pagination.
   * @returns {Promise<object[]>}
   */
  async getAllRows() {
    const rows = [];
    let continuation;

    do {
      const page = await this.request(
        "GET",
        `/tables/${this.tableId}/rows`,
        {
          query: {
            limit: this.pageLimit,
            ...(continuation ? { continuation } : {}),
          },
        },
      );

      const batch = Array.isArray(page?.data) ? page.data : [];
      rows.push(...batch);
      continuation = page?.continuation;
    } while (continuation);

    return rows;
  }

  /**
   * PATCH a single row (column keys are Glide column IDs).
   * @param {string} rowId
   * @param {Record<string, unknown>} fields
   */
  async updateRow(rowId, fields) {
    if (!rowId) {
      throw new Error("rowId is required for updateRow");
    }
    await this.request(
      "PATCH",
      `/tables/${this.tableId}/rows/${encodeURIComponent(rowId)}`,
      { body: fields },
    );
    if (this.updateDelayMs > 0) {
      await sleep(this.updateDelayMs);
    }
  }
}

/**
 * Glide row identifier from a row object returned by GET /rows.
 * @param {object} row
 * @returns {string | null}
 */
export function getGlideRowId(row) {
  if (!row || typeof row !== "object") return null;
  const id = row.$rowID ?? row.$rowId ?? row.rowID ?? row.rowId;
  return typeof id === "string" && id.trim() ? id.trim() : null;
}
