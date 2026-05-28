import asyncio
import httpx
from datetime import datetime, timezone


async def findItemRanking(keyword: str, item_ids: list, max_pages: int = 5) -> list:
    url = "https://api.mercadolibre.com/sites/MLB/search"
    results = {iid: {"found": False, "position": None, "page": None,
                     "index_on_page": None, "title": None, "price": None,
                     "permalink": None} for iid in item_ids}
    found_set = set()

    async with httpx.AsyncClient(timeout=20, verify=False) as client:
        for page in range(max_pages):
            offset = page * 50
            resp = await client.get(url, params={"q": keyword, "offset": offset, "limit": 50})
            data = resp.json()
            items = data.get("results", [])
            if not items:
                break

            for idx, item in enumerate(items):
                iid = item.get("id")
                if iid in results and iid not in found_set:
                    found_set.add(iid)
                    results[iid] = {
                        "found": True,
                        "position": offset + idx + 1,
                        "page": page + 1,
                        "index_on_page": idx + 1,
                        "title": item.get("title"),
                        "price": item.get("price"),
                        "permalink": item.get("permalink"),
                    }

            if len(found_set) == len(item_ids):
                break

            if page < max_pages - 1:
                await asyncio.sleep(0.3)

    return [{"item_id": iid, **results[iid]} for iid in item_ids]


async def batch_ranking(keywords: list, item_ids: list, max_pages: int = 5) -> dict:
    keyword_results = []
    for kw in keywords:
        items = await findItemRanking(kw, item_ids, max_pages=max_pages)
        keyword_results.append({"keyword": kw, "items": items})

    return {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "keywords": keyword_results,
        "items_monitored": item_ids,
    }
