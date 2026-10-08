"""拉官方 DestinyHistoricalStatsDefinition（zh-chs 与 zh-cht）看智谋字段的官方译名"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2

WANT = ("motesDeposited", "motesPickedUp", "motesDenied", "motesLost", "invasions",
        "invasionKills", "invasionDeaths", "invaderKills", "invaderDeaths",
        "primevalKills", "primevalDamage", "primevalHealing", "highValueKills",
        "smallBlockersSent", "mediumBlockersSent", "largeBlockersSent", "bankOverage",
        "blockerKills", "roundsWon", "roundsPlayed", "fastestCompletionMs", "mobKills")


async def main():
    r = await d2.client().get("/Platform/Destiny2/Manifest/")
    man = r.json()["Response"]
    paths = man["jsonWorldComponentContentPaths"]
    for lang in ("zh-chs", "zh-cht", "en"):
        url = paths.get(lang, {}).get("DestinyHistoricalStatsDefinition")
        if not url:
            print(lang, "no DestinyHistoricalStatsDefinition")
            continue
        rr = await d2.client().get(url)
        data = json.loads(rr.content.decode("utf-8-sig"))
        print(f"\n=== {lang} 共 {len(data)} 条 ===")
        for k, v in data.items():
            if v.get("statId") in WANT:
                print(f"  {v.get('statId'):22s} name={v.get('statName')!r:26s} "
                      f"desc={str(v.get('statDescription'))[:60]!r} group={v.get('group')}")


asyncio.run(main())
