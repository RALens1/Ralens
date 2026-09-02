import os, json, hashlib, re, asyncio
from datetime import datetime, timezone
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg
from openai import AsyncOpenAI

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://ralens:ralens@db:5432/ralens")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me")
UA = "R.A.Lens/1.0 regulatory intelligence prototype"

SOURCES = [
    ("FDA / USFDA", "FDA Guidance", "https://www.fda.gov/regulatory-information/search-fda-guidance-documents", "html"),
    ("CDSCO", "Latest Circulars", "https://www.cdsco.gov.in/opencms/opencms/en/Latest-Circulars/", "html"),
    ("CDSCO", "Gazette Notifications", "https://www.cdsco.gov.in/opencms/opencms/en/Notifications/Gazette-Notifications", "html"),
    ("EMA", "Regulatory & procedural guidelines", "https://www.ema.europa.eu/en/news-events/rss-feeds", "ema_page"),
    ("MHRA", "Guidance and regulation", "https://www.gov.uk/search/guidance-and-regulation?organisations%5B%5D=medicines-and-healthcare-products-regulatory-agency", "govuk"),
]

app = FastAPI(title="R.A.Lens API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class AnalyzeRequest(BaseModel):
    update_id: int
    product_context: str

class Feedback(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    category: str
    message: str
    rating: Optional[int] = None

def conn():
    return psycopg.connect(DATABASE_URL)

def init_db():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS regulatory_updates(
            id BIGSERIAL PRIMARY KEY, regulator TEXT NOT NULL, source_name TEXT NOT NULL,
            title TEXT NOT NULL, url TEXT NOT NULL, published_at TEXT, summary TEXT,
            content TEXT, fingerprint TEXT UNIQUE, collected_at TIMESTAMPTZ DEFAULT now())""")
        c.execute("""CREATE TABLE IF NOT EXISTS analyses(
            id BIGSERIAL PRIMARY KEY, update_id BIGINT REFERENCES regulatory_updates(id),
            product_context TEXT NOT NULL, result JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now())""")
        c.execute("""CREATE TABLE IF NOT EXISTS feedback(
            id BIGSERIAL PRIMARY KEY, name TEXT, email TEXT, category TEXT NOT NULL,
            message TEXT NOT NULL, rating INT, created_at TIMESTAMPTZ DEFAULT now())""")

@app.on_event("startup")
async def startup():
    # Retry because the database container may need a few seconds to become ready.
    for _ in range(20):
        try:
            init_db()
            return
        except Exception:
            await asyncio.sleep(2)
    raise RuntimeError("Database unavailable")

async def get(url):
    async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers={"User-Agent": UA}) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.text

def fp(reg, title, url, published, summary):
    return hashlib.sha256(f"{reg}|{title}|{url}|{published}|{summary}".encode()).hexdigest()

def parse_html(regulator, source_name, url, html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.select("table tr"):
        cells = tr.find_all(["td","th"])
        a = tr.find("a", href=True)
        if not a or not cells:
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 8:
            continue
        href = a["href"]
        if href.startswith("/"):
            if "gov.uk" in url: href = "https://www.gov.uk" + href
            elif "fda.gov" in url: href = "https://www.fda.gov" + href
            else: href = "https://www.cdsco.gov.in" + href
        text = " ".join(tr.get_text(" ", strip=True).split())
        published = cells[0].get_text(" ", strip=True)[:100]
        out.append((regulator, source_name, title, href, published, text, text))
    return out[:100]

def parse_govuk(html):
    soup = BeautifulSoup(html, "html.parser")
    out=[]
    for a in soup.select("a.govuk-link"):
        title=" ".join(a.get_text(" ", strip=True).split())
        href=a.get("href","")
        if not title or len(title)<8 or not href.startswith("/"):
            continue
        parent=a.find_parent(["li","div"])
        text=" ".join((parent or a).get_text(" ",strip=True).split())
        date=""
        m=re.search(r"(?:Updated|Published):\s*([0-9]{1,2}\s+\w+\s+20\d{2})", text)
        if m: date=m.group(1)
        out.append(("MHRA","Guidance and regulation",title,"https://www.gov.uk"+href,date,text,text))
    return out[:100]

async def collect_source(reg, name, url, typ):
    html = await get(url)
    if typ == "govuk":
        return parse_govuk(html)
    if typ == "ema_page":
        # EMA publishes official RSS feeds and links them from this page.
        soup = BeautifulSoup(html, "html.parser")
        links=[]
        for a in soup.select("a[href]"):
            href=a.get("href","")
            txt=" ".join(a.get_text(" ",strip=True).split()).lower()
            if href.endswith(".xml") and ("regulatory" in txt or "what" in txt or "scientific" in txt):
                links.append(href)
        allrows=[]
        for link in links[:4]:
            if link.startswith("/"): link="https://www.ema.europa.eu"+link
            try:
                import feedparser
                feed=feedparser.parse((await get(link)))
                for e in feed.entries[:50]:
                    title=" ".join(getattr(e,"title","").split())
                    if title:
                        summary=BeautifulSoup(getattr(e,"summary",""),"html.parser").get_text(" ",strip=True)
                        allrows.append(("EMA",name,title,getattr(e,"link",link),getattr(e,"published",""),summary,summary))
            except Exception:
                pass
        return allrows
    return parse_html(reg,name,url,html)

@app.get("/api/health")
def health():
    with conn() as c:
        c.execute("SELECT count(*) FROM regulatory_updates")
        n=c.fetchone()[0]
    return {"status":"ok","updates":n,"time":datetime.now(timezone.utc).isoformat()}

@app.get("/api/updates")
def updates(regulator: Optional[str]=None, limit: int=50):
    limit=max(1,min(limit,100))
    with conn() as c:
        if regulator and regulator!="All":
            rows=c.execute("""SELECT id,regulator,source_name,title,url,published_at,summary,collected_at
                FROM regulatory_updates WHERE regulator=%s ORDER BY collected_at DESC LIMIT %s""",(regulator,limit)).fetchall()
        else:
            rows=c.execute("""SELECT id,regulator,source_name,title,url,published_at,summary,collected_at
                FROM regulatory_updates ORDER BY collected_at DESC LIMIT %s""",(limit,)).fetchall()
    return [{"id":r[0],"regulator":r[1],"source":r[2],"title":r[3],"url":r[4],"published":r[5],"summary":r[6],"collected_at":str(r[7])} for r in rows]

@app.post("/api/admin/collect")
async def collect(x_admin_key: str):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(401,"Invalid admin key")
    total=0; new=0; errors=[]
    for s in SOURCES:
        try:
            rows=await collect_source(*s)
            total += len(rows)
            with conn() as c:
                for reg,name,title,url,pub,summary,content in rows:
                    f=fp(reg,title,url,pub,summary)
                    r=c.execute("""INSERT INTO regulatory_updates
                        (regulator,source_name,title,url,published_at,summary,content,fingerprint)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(fingerprint) DO NOTHING
                        RETURNING id""",(reg,name,title,url,pub,summary,content,f)).fetchone()
                    if r: new+=1
        except Exception as e:
            errors.append({"source":s[0]+"/"+s[1],"error":str(e)})
    return {"scanned":total,"new":new,"errors":errors}

@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    if not OPENAI_API_KEY:
        raise HTTPException(503,"LLM is not configured. Set OPENAI_API_KEY on the server.")
    with conn() as c:
        row=c.execute("""SELECT id,regulator,source_name,title,url,published_at,content
                         FROM regulatory_updates WHERE id=%s""",(req.update_id,)).fetchone()
    if not row: raise HTTPException(404,"Update not found")
    prompt=f"""You are R.A.Lens, a pharmaceutical regulatory intelligence decision-support assistant.
Use only the supplied official-source evidence. Do not invent requirements.
Do not decide compliance; produce a review aid for a qualified RA professional.

SOURCE: {row[1]} / {row[2]}
TITLE: {row[3]}
URL: {row[4]}
DATE: {row[5]}
SOURCE CONTENT: {row[6][:14000]}

PRODUCT/SUBMISSION CONTEXT:
{req.product_context}

Return JSON keys:
what_changed, why_it_matters, potential_product_impact, affected_ra_artifacts,
priority, relevance, evidence, recommended_ra_review_plan, uncertainties.
Evidence must retain the official URL and distinguish facts from inference.
Priority: Low/Medium/High/Critical. Relevance: Low/Medium/High."""
    client=AsyncOpenAI(api_key=OPENAI_API_KEY)
    res=await client.responses.create(model=os.getenv("OPENAI_MODEL","gpt-5.6"), input=prompt)
    text=res.output_text
    try: result=json.loads(text)
    except Exception:
        m=re.search(r"\{.*\}",text,re.S)
        result=json.loads(m.group(0)) if m else {"raw_output":text}
    with conn() as c:
        c.execute("INSERT INTO analyses(update_id,product_context,result) VALUES(%s,%s,%s)",(row[0],req.product_context,json.dumps(result)))
    return result

@app.post("/api/feedback")
def feedback(f: Feedback):
    with conn() as c:
        c.execute("INSERT INTO feedback(name,email,category,message,rating) VALUES(%s,%s,%s,%s,%s)",
                  (f.name,f.email,f.category,f.message,f.rating))
    return {"ok":True}
