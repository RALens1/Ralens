import os, time, hashlib, re, asyncio, httpx, feedparser, psycopg
from bs4 import BeautifulSoup
DATABASE_URL=os.getenv("DATABASE_URL","postgresql://ralens:ralens@db:5432/ralens")
UA="R.A.Lens/1.0 regulatory intelligence worker"
SOURCES=[
("FDA / USFDA","FDA Guidance","https://www.fda.gov/regulatory-information/search-fda-guidance-documents","html"),
("CDSCO","Latest Circulars","https://www.cdsco.gov.in/opencms/opencms/en/Latest-Circulars/","html"),
("CDSCO","Gazette Notifications","https://www.cdsco.gov.in/opencms/opencms/en/Notifications/Gazette-Notifications","html"),
("EMA","RSS feeds","https://www.ema.europa.eu/en/news-events/rss-feeds","ema"),
("MHRA","Guidance and regulation","https://www.gov.uk/search/guidance-and-regulation?organisations%5B%5D=medicines-and-healthcare-products-regulatory-agency","govuk"),
]
def db():
    return psycopg.connect(DATABASE_URL)
def fp(*x): return hashlib.sha256("|".join(x).encode()).hexdigest()
async def get(u):
    async with httpx.AsyncClient(timeout=45,follow_redirects=True,headers={"User-Agent":UA}) as c:
        r=await c.get(u); r.raise_for_status(); return r.text
def html_rows(reg,name,url,html):
    soup=BeautifulSoup(html,"html.parser"); out=[]
    for tr in soup.select("table tr"):
        a=tr.find("a",href=True); cells=tr.find_all(["td","th"])
        if not a or not cells: continue
        title=" ".join(a.get_text(" ",strip=True).split())
        href=a["href"]
        if href.startswith("/"):
            base="https://www.fda.gov" if "fda.gov" in url else "https://www.cdsco.gov.in"
            href=base+href
        text=" ".join(tr.get_text(" ",strip=True).split())
        out.append((reg,name,title,href,cells[0].get_text(" ",strip=True)[:100],text,text))
    return out[:100]
def gov_rows(html):
    soup=BeautifulSoup(html,"html.parser"); out=[]
    for a in soup.select("a.govuk-link"):
        title=" ".join(a.get_text(" ",strip=True).split()); href=a.get("href","")
        if not href.startswith("/") or len(title)<8: continue
        p=a.find_parent(["li","div"]); text=" ".join((p or a).get_text(" ",strip=True).split())
        m=re.search(r"(?:Updated|Published):\s*([0-9]{1,2}\s+\w+\s+20\d{2})",text)
        out.append(("MHRA","Guidance and regulation",title,"https://www.gov.uk"+href,m.group(1) if m else "",text,text))
    return out[:100]
async def collect(s):
    reg,name,url,typ=s; html=await get(url)
    if typ=="govuk": return gov_rows(html)
    if typ=="html": return html_rows(reg,name,url,html)
    soup=BeautifulSoup(html,"html.parser"); feeds=[]
    for a in soup.select("a[href]"):
        h=a.get("href",""); t=a.get_text(" ",strip=True).lower()
        if h.endswith(".xml") and ("regulatory" in t or "what" in t or "scientific" in t):
            feeds.append(h if h.startswith("http") else "https://www.ema.europa.eu"+h)
    rows=[]
    for f in feeds[:4]:
        d=feedparser.parse(await get(f))
        for e in d.entries[:50]:
            title=" ".join(getattr(e,"title","").split())
            if title:
                summ=BeautifulSoup(getattr(e,"summary",""),"html.parser").get_text(" ",strip=True)
                rows.append(("EMA",name,title,getattr(e,"link",f),getattr(e,"published",""),summ,summ))
    return rows
async def once():
    total=new=0
    for s in SOURCES:
        try:
            rows=await collect(s); total+=len(rows)
            with db() as c:
                for reg,name,title,url,pub,summary,content in rows:
                    h=fp(reg,title,url,pub,summary)
                    r=c.execute("""INSERT INTO regulatory_updates(regulator,source_name,title,url,published_at,summary,content,fingerprint)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(fingerprint) DO NOTHING RETURNING id""",
                    (reg,name,title,url,pub,summary,content,h)).fetchone()
                    if r:new+=1
        except Exception as e:
            print("SOURCE ERROR",s,e,flush=True)
    print("COLLECTION",total,"scanned",new,"new",flush=True)
async def main():
    while True:
        await once()
        await asyncio.sleep(86400)
asyncio.run(main())
