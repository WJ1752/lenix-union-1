"""写权限探针：故意用无效 itemId 调 TransferItem。

判据：如果报的是「物品/角色不存在」这类校验错 → 说明 MoveEquipDestinyItems 权限已到位；
      如果报「权限不足 / AuthorizationRequired」→ 说明需要重新授权。
全程不碰任何真实物品（itemId 用的是 0）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import bungie_auth


async def main():
    tok = await bungie_auth.access_token()
    headers = {"X-API-Key": bungie_auth._env("BUNGIE_API_KEY"),
               "Authorization": f"Bearer {tok}"}
    body = {"itemReferenceHash": 0, "stackSize": 1,
            "transferToVault": True, "itemId": "0", "characterId": "0"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(bungie_auth.BASE + "/Platform/Destiny2/Actions/Items/TransferItem/",
                         json=body, headers=headers)
        d = r.json()
    print("HTTP", r.status_code, "ErrorCode", d.get("ErrorCode"))
    print("ErrorStatus:", d.get("ErrorStatus"))
    print("Message:", (d.get("Message") or "")[:200])


asyncio.run(main())
