import { countByCategory, discoverWorldCupMarkets } from "../../world-cup-2026-arbitrage-trading-bot-main/src/api/gamma.js";
import { buildMarketStateRows, buildMatchStateRows } from "../../world-cup-2026-arbitrage-trading-bot-main/src/arbitrage/scanner.js";
import { getMarketScope, loadSettings } from "../../world-cup-2026-arbitrage-trading-bot-main/src/config.js";
import {
  saveMarketsToLocal,
  saveMatchQuotesToLocal,
  saveMatchesToLocal,
  saveQuotesToLocal,
} from "../../world-cup-2026-arbitrage-trading-bot-main/src/local/store.js";

async function main(): Promise<void> {
  const settings = loadSettings();
  const scope = getMarketScope();
  const savedAt = new Date();

  console.log("Fetching World Cup markets from Polymarket...");
  const groups = await discoverWorldCupMarkets(settings.minLiquidityUsd, settings.maxMarkets);
  const counts = countByCategory(groups);
  console.log(`Loaded ${groups.length} events (${counts.matches} matches, ${counts.futures} futures)`);

  if (groups.length === 0) {
    throw new Error("No World Cup market groups loaded from Polymarket.");
  }

  await saveMarketsToLocal(groups, savedAt);
  await saveMatchesToLocal(groups, savedAt);

  console.log("Loading live order book prices...");
  const matchRows = await buildMatchStateRows(groups);
  const futuresRows = await buildMarketStateRows(groups.filter((group) => group.category !== "match"));

  await saveMatchQuotesToLocal(matchRows, savedAt, "match-quotes");
  if (scope === "matches") {
    await saveMatchQuotesToLocal(matchRows, savedAt, "quotes");
  }
  if (futuresRows.length > 0) {
    await saveQuotesToLocal(futuresRows, savedAt);
  }

  console.log(`Market state ready (${matchRows.length} match rows, ${futuresRows.length} futures rows)`);
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`Fatal error: ${message}`);
  process.exit(1);
});
