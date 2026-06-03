#!/usr/bin/env node
import { pathToFileURL } from "node:url";

/**
 * Backfill missing `club` values on the Glide payment methods Big Table from `Name`.
 *
 * Glide API v2 — team-scoped; GLIDE_APP_ID is optional and not sent to the API.
 *
 * Dry run (default):
 *   GLIDE_API_TOKEN="your_glide_api_token" \
 *   GLIDE_APP_ID="your_glide_app_id_if_needed" \
 *   GLIDE_PAYMENT_METHODS_TABLE_ID="your_payment_methods_table_id" \
 *   APPLY_BACKFILL=false \
 *   node scripts/backfill-glide-clubs.js
 *
 * Apply updates:
 *   GLIDE_API_TOKEN="your_glide_api_token" \
 *   GLIDE_APP_ID="your_glide_app_id_if_needed" \
 *   GLIDE_PAYMENT_METHODS_TABLE_ID="your_payment_methods_table_id" \
 *   APPLY_BACKFILL=true \
 *   node scripts/backfill-glide-clubs.js
 *
 * Optional:
 *   GLIDE_NAME_COLUMN=Name          — Glide column id for Name (default: Name)
 *   GLIDE_CLUB_COLUMN=club          — Glide column id for club (default: club)
 *   GLIDE_PAGE_LIMIT=500
 *   GLIDE_UPDATE_DELAY_MS=250       — pause between PATCH calls
 */

import { GlideApiError, GlideClient, getGlideRowId } from "./lib/glide-api.js";

/** Normalized club code (first Name segment) → canonical club value */
const CLUB_CODE_TO_NAME = new Map([
  ["AT", "Round Table"],
  ["RT", "Round Table"],
  ["RT AT", "Round Table"],
  ["AT RT", "Round Table"],
  ["GTO", "ClubGTO"],
  ["CC", "Creator Club"],
]);

function envBool(name, defaultValue = false) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return defaultValue;
  const v = String(raw).trim().toLowerCase();
  return v === "true" || v === "1" || v === "yes";
}

function envInt(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function isClubEmpty(value) {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  return false;
}

function getField(row, columnKey) {
  if (!row || typeof row !== "object") return undefined;
  if (Object.prototype.hasOwnProperty.call(row, columnKey)) {
    return row[columnKey];
  }
  const lower = columnKey.toLowerCase();
  for (const [key, val] of Object.entries(row)) {
    if (key.startsWith("$")) continue;
    if (key.toLowerCase() === lower) return val;
  }
  return undefined;
}

/**
 * Extract and normalize the club code from the first segment of Name.
 * @param {string | undefined | null} name
 * @returns {{ ok: true, code: string } | { ok: false, reason: string }}
 */
export function extractClubCodeFromName(name) {
  if (name === null || name === undefined) {
    return { ok: false, reason: "missing Name" };
  }
  const text = String(name).trim();
  if (!text) {
    return { ok: false, reason: "empty Name" };
  }
  if (!text.includes("/")) {
    return { ok: false, reason: "Name has no '/'" };
  }
  const firstSegment = text.split("/")[0].trim();
  if (!firstSegment) {
    return { ok: false, reason: "empty club segment before '/'" };
  }
  const code = firstSegment.replace(/\s+/g, " ").toUpperCase();
  return { ok: true, code };
}

/**
 * @param {string} code - normalized uppercase club code
 * @returns {string | null}
 */
export function mapClubCodeToName(code) {
  return CLUB_CODE_TO_NAME.get(code) ?? null;
}

function logSection(title) {
  console.log(`\n=== ${title} ===`);
}

async function main() {
  const token = process.env.GLIDE_API_TOKEN?.trim();
  const tableId = process.env.GLIDE_PAYMENT_METHODS_TABLE_ID?.trim();
  const nameColumn = process.env.GLIDE_NAME_COLUMN?.trim() || "Name";
  const clubColumn = process.env.GLIDE_CLUB_COLUMN?.trim() || "club";
  const apply = envBool("APPLY_BACKFILL", false);

  if (!token) {
    console.error("Missing GLIDE_API_TOKEN");
    process.exit(1);
  }
  if (!tableId) {
    console.error("Missing GLIDE_PAYMENT_METHODS_TABLE_ID");
    process.exit(1);
  }

  const glideAppId = process.env.GLIDE_APP_ID?.trim();
  if (glideAppId) {
    console.log(
      `Note: GLIDE_APP_ID is set (${glideAppId}) but Glide API v2 is team-scoped; app id is not sent.`,
    );
  }

  const client = new GlideClient({
    token,
    tableId,
    pageLimit: envInt("GLIDE_PAGE_LIMIT", 500),
    updateDelayMs: envInt("GLIDE_UPDATE_DELAY_MS", 250),
  });

  logSection(apply ? "APPLY MODE" : "DRY RUN");
  console.log(`Table: ${tableId}`);
  console.log(`Columns: Name=${nameColumn}, club=${clubColumn}`);
  console.log(`APPLY_BACKFILL=${apply}`);

  logSection("Fetching rows from Glide");
  let rows;
  try {
    rows = await client.getAllRows();
  } catch (err) {
    console.error("Failed to fetch rows:", err.message);
    if (err instanceof GlideApiError) {
      console.error(JSON.stringify(err.body, null, 2));
    }
    process.exit(1);
  }

  const stats = {
    scanned: rows.length,
    skippedHasClub: 0,
    skippedInvalidName: 0,
    skippedUnmapped: 0,
    skippedNoRowId: 0,
    wouldUpdate: 0,
    updated: 0,
    failed: 0,
  };

  /** @type {{ rowId: string, name: string, code: string, club: string }[]} */
  const planned = [];
  /** @type {{ rowId: string, name: string, error: string }[]} */
  const failures = [];

  for (const row of rows) {
    const existingClub = getField(row, clubColumn);
    if (!isClubEmpty(existingClub)) {
      stats.skippedHasClub += 1;
      continue;
    }

    const name = getField(row, nameColumn);
    const extracted = extractClubCodeFromName(name);
    if (!extracted.ok) {
      stats.skippedInvalidName += 1;
      const rowId = getGlideRowId(row) ?? "(no row id)";
      console.log(
        `[skip invalid Name] rowId=${rowId} reason=${extracted.reason} name=${JSON.stringify(name ?? "")}`,
      );
      continue;
    }

    const clubName = mapClubCodeToName(extracted.code);
    if (!clubName) {
      stats.skippedUnmapped += 1;
      const rowId = getGlideRowId(row) ?? "(no row id)";
      console.log(
        `[skip unmapped] rowId=${rowId} code=${extracted.code} name=${JSON.stringify(String(name ?? ""))}`,
      );
      continue;
    }

    const rowId = getGlideRowId(row);
    if (!rowId) {
      stats.skippedNoRowId += 1;
      console.log(
        `[skip no row id] code=${extracted.code} name=${JSON.stringify(String(name ?? ""))}`,
      );
      continue;
    }

    stats.wouldUpdate += 1;
    planned.push({
      rowId,
      name: String(name ?? ""),
      code: extracted.code,
      club: clubName,
    });
  }

  logSection("Summary (scan)");
  console.log(`Total Glide rows scanned: ${stats.scanned}`);
  console.log(`Skipped (club already set): ${stats.skippedHasClub}`);
  console.log(`Skipped (invalid Name): ${stats.skippedInvalidName}`);
  console.log(`Skipped (unmapped club code): ${stats.skippedUnmapped}`);
  if (stats.skippedNoRowId) {
    console.log(`Skipped (missing Glide row id): ${stats.skippedNoRowId}`);
  }
  console.log(
    apply
      ? `Rows to update in Glide: ${stats.wouldUpdate}`
      : `Rows that would be updated: ${stats.wouldUpdate}`,
  );

  if (planned.length === 0) {
    console.log("\nNo rows need updates.");
    return;
  }

  logSection(apply ? "Updates" : "Preview (dry run)");
  for (const item of planned) {
    console.log(
      `rowId=${item.rowId} | code=${item.code} | club=${item.club} | Name=${item.name}`,
    );
  }

  if (!apply) {
    console.log(
      "\nDry run complete. Set APPLY_BACKFILL=true to write club values to Glide.",
    );
    return;
  }

  logSection("Writing to Glide");
  for (const item of planned) {
    try {
      await client.updateRow(item.rowId, { [clubColumn]: item.club });
      stats.updated += 1;
      console.log(`[updated] rowId=${item.rowId} club=${item.club}`);
    } catch (err) {
      stats.failed += 1;
      const detail =
        err instanceof GlideApiError
          ? `${err.message} body=${JSON.stringify(err.body)}`
          : err.message;
      failures.push({ rowId: item.rowId, name: item.name, error: detail });
      console.error(`[failed] rowId=${item.rowId} error=${detail}`);
    }
  }

  logSection("Summary (apply)");
  console.log(`Total Glide rows scanned: ${stats.scanned}`);
  console.log(`Rows updated in Glide: ${stats.updated}`);
  console.log(
    `Rows skipped: ${
      stats.skippedHasClub +
      stats.skippedInvalidName +
      stats.skippedUnmapped +
      stats.skippedNoRowId
    }`,
  );
  console.log(`Failed updates: ${stats.failed}`);

  if (failures.length > 0) {
    logSection("Failed update details");
    for (const f of failures) {
      console.log(`rowId=${f.rowId} name=${f.name}`);
      console.log(`  error: ${f.error}`);
    }
    process.exit(1);
  }
}

const isMain =
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href;

if (isMain) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
