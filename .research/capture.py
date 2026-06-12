import asyncio, json, sys
from playwright.async_api import async_playwright

TARGETS = {
    "gene-validity": "https://search.clinicalgenome.org/kb/gene-validity?page=1&size=25&search=",
    "gene-dosage":   "https://search.clinicalgenome.org/kb/gene-dosage?page=1&size=25&search=",
    "erepo":         "https://erepo.clinicalgenome.org/evrepo/",
    "actionability": "https://actionability.clinicalgenome.org/ac/",
}

async def grab(pw, name, url):
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context()
    page = await ctx.new_page()
    calls = []
    def on_request(req):
        if req.resource_type in ("xhr","fetch") or any(k in req.url for k in ("/api","graphql",".json",".tsv",".csv","download")):
            calls.append({"method":req.method,"url":req.url,"rtype":req.resource_type})
    page.on("request", on_request)
    resp_meta = {}
    def on_response(resp):
        u = resp.url
        if u in [c["url"] for c in calls]:
            ct = resp.headers.get("content-type","")
            resp_meta[u] = {"status":resp.status,"ct":ct,"lm":resp.headers.get("last-modified",""),"etag":resp.headers.get("etag","")}
    page.on("response", on_response)
    try:
        await page.goto(url, wait_until="networkidle", timeout=45000)
    except Exception as e:
        print(f"[{name}] goto warn: {e}", file=sys.stderr)
    await page.wait_for_timeout(3000)
    for c in calls:
        c.update(resp_meta.get(c["url"], {}))
    await browser.close()
    return calls

async def main():
    out = {}
    async with async_playwright() as pw:
        for name, url in TARGETS.items():
            try:
                out[name] = await grab(pw, name, url)
                print(f"[{name}] captured {len(out[name])} api/xhr calls", file=sys.stderr)
            except Exception as e:
                out[name] = [{"error": str(e)}]
                print(f"[{name}] ERROR {e}", file=sys.stderr)
    print(json.dumps(out, indent=2))

asyncio.run(main())
