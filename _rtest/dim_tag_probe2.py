import asyncio, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bungie_auth, httpx
async def main():
    tok = json.load(open("bungie_token.json", encoding="utf-8"))
    mt = tok.get("membership_type"); mid = str(tok.get("membership_id"))
    at = await bungie_auth.access_token()
    hdr = {"X-API-Key": bungie_auth._env("BUNGIE_API_KEY"), "Authorization": f"Bearer {at}"}
    async with httpx.AsyncClient(timeout=20) as c:
        for path in ("/Platform/Destiny2/Actions/Items/SetTag/",
                     "/Platform/Destiny2/Actions/Items/SetItemTag/",
                     "/Platform/Destiny2/Actions/Items/SetLockState/"):
            r = await c.post(bungie_auth.BASE + path, json={"itemId":"0","tag":2,"membershipType":mt}, headers=hdr)
            print(path, r.status_code, r.text[:200].replace("\n"," "))
asyncio.run(main())
