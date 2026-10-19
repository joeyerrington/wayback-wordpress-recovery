#!/usr/bin/env python3
"""Recover archived WordPress posts to a WXR file. Review drafts before publishing."""
import argparse, csv, html, json, os, re, time, urllib.parse, uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from lxml import etree
from dotenv import load_dotenv
load_dotenv()
SITE_URL=os.getenv("SITE_URL", "").strip().rstrip("/")
if not SITE_URL: raise SystemExit("Set SITE_URL in .env (see .env.example)")
SITE=urllib.parse.urlsplit(SITE_URL if "://" in SITE_URL else "https://"+SITE_URL)
DOMAIN=SITE.hostname.lower().removeprefix("www.")
SITE_URL=f"{SITE.scheme}://{SITE.netloc}"
FROM_YEAR=int(os.getenv("ARCHIVE_FROM_YEAR", "2010"))
TO_YEAR=int(os.getenv("ARCHIVE_TO_YEAR", "2020"))
ARCHIVE_BASE=os.getenv("ARCHIVE_BASE_URL", "https://web.archive.org").rstrip("/")
MAX_CAPTURES=int(os.getenv("MAX_CAPTURES_PER_POST", "3"))
REQUEST_TIMEOUT=int(os.getenv("REQUEST_TIMEOUT", "20"))
REQUEST_DELAY=float(os.getenv("REQUEST_DELAY", "0.5"))
EXCLUDE={x.strip(" /").lower() for x in os.getenv("EXCLUDE_PATHS", "contact,about,get-involved,sample-page").split(",") if x.strip()}

BASE=ARCHIVE_BASE
NS={'excerpt':'http://wordpress.org/export/1.2/excerpt/','content':'http://purl.org/rss/1.0/modules/content/','wp':'http://wordpress.org/export/1.2/','dc':'http://purl.org/dc/elements/1.1/'}
BAD={'wp-admin','wp-content','wp-includes','feed','comments','category','tag','author','page','search','attachment','xmlrpc.php','robots.txt','sitemap.xml'}
SESSION=requests.Session(); SESSION.headers.update({'User-Agent':'WaybackWordPressRecovery/1.0 (site restoration)' })
def get(url, params=None, binary=False):
    for n in range(2):
        try:
            r=SESSION.get(url,params=params,timeout=REQUEST_TIMEOUT);r.raise_for_status();time.sleep(REQUEST_DELAY)
            return r.content if binary else r.text
        except requests.RequestException as e:
            if n==1: raise
            time.sleep(3*(n+1))
def original(url):
    url=re.sub(r'^https?://[^/]+/web/\d+(?:[a-z_]+)?/','',url)
    if url.startswith('//'):url='http:'+url
    return url

def cleanurl(url):
    url=original(url)
    p=urllib.parse.urlsplit(url)
    if not p.hostname or p.hostname.lower().removeprefix('www.')!=DOMAIN:return None
    path=re.sub('/+','/',urllib.parse.unquote(p.path)).rstrip('/')+'/'
    return SITE_URL+path

def candidate(url):
    u=cleanurl(url)
    if not u:return False
    parts=[x for x in urllib.parse.urlsplit(u).path.split('/') if x]
    if not parts or any(x.lower() in BAD for x in parts) or parts[-1].lower() in EXCLUDE:return False
    if re.search(r'\.(jpg|png|gif|webp|css|js|xml|pdf|ico|txt|zip)$',parts[-1],re.I):return False
    return True

def cdx():
    found=[]
    for host in [DOMAIN, 'www.'+DOMAIN]:
        params={'url':host+'/*','output':'json','from':str(FROM_YEAR),'to':str(TO_YEAR),'filter':['statuscode:200','mimetype:text/html'],'fl':'timestamp,original,digest','collapse':'digest'}
        try:
            data=json.loads(get(BASE+'/cdx/search/cdx',params))
            found += [dict(zip(data[0],row)) for row in data[1:] if len(row)==len(data[0])]
        except Exception as e:print('CDX error',host,e)
    return found

def captures_for_url(url):
    """Look up individual URL variants rather than relying on the broad CDX listing."""
    p=urllib.parse.urlsplit(url)
    path=p.path
    found=[]
    for host in (DOMAIN,'www.'+DOMAIN):
        for scheme in ('http','https'):
            target=f'{scheme}://{host}{path}'
            params={'url':target,'output':'json','from':str(FROM_YEAR),'to':str(TO_YEAR),
                    'fl':'timestamp,original,statuscode,mimetype',
                    'filter':['statuscode:200','mimetype:text/html']}
            try:
                data=json.loads(get(BASE+'/cdx/search/cdx',params))
                if isinstance(data,list) and len(data)>1:
                    for row in data[1:]:
                        rec=dict(zip(data[0],row))
                        if cleanurl(rec.get('original',''))==url:
                            found.append((rec['timestamp'],rec['original']))
            except Exception as e:
                print('Individual CDX lookup failed:',target,str(e)[:100])
    return sorted(set(found),reverse=True)

def archive_fallback(url):
    """Wayback's nearest-capture redirect can work even if CDX omitted a URL."""
    for ts in (f'{TO_YEAR}1231235959', f'{(FROM_YEAR+TO_YEAR)//2}0601000000', f'{FROM_YEAR}0101000000'):
        for scheme in ('http','https'):
            for host in (DOMAIN,'www.'+DOMAIN):
                target=f'{scheme}://{host}{urllib.parse.urlsplit(url).path}'
                yield f'{BASE}/web/{ts}/{target}',ts

def matches_post(markup,url):
    """Reject homepage, error pages and redirects to other posts."""
    soup=BeautifulSoup(markup,'html.parser')
    canonical=soup.select_one('link[rel="canonical"]')
    if canonical and cleanurl(canonical.get('href','')) not in (None,url):
        return False
    og=soup.select_one('meta[property="og:url"]')
    if og and cleanurl(og.get('content','')) not in (None,url):
        return False
    return True

def discover_from_home():
    urls={}
    for year in sorted(set([FROM_YEAR,(FROM_YEAR+TO_YEAR)//2,TO_YEAR])):
        for host in [DOMAIN, 'www.'+DOMAIN]:
            try:
                markup=get(f'{BASE}/web/{year}1231235959/http://{host}/')
                soup=BeautifulSoup(markup,'html.parser')
                for a in soup.select('article a[href], .post a[href], h1 a[href], h2 a[href], h3 a[href]'):
                    u=cleanurl(a.get('href',''))
                    if u and candidate(u):urls[u]=year+'1231235959'
            except Exception:pass
    return urls

def select_content(soup):
    for bad in soup.select('script,style,nav,footer,aside,.sharedaddy,.share-buttons,.post-navigation,.comments,.comment-respond,.related-posts,.wp-caption-text .share'):
        bad.decompose()
    for selector in ['article .entry-content','article .post-content','.entry-content','.post-content','.article-content','article']:
        blocks=soup.select(selector)
        if blocks:
            block=max(blocks,key=lambda x:len(x.get_text(' ',strip=True)))
            if len(block.get_text(' ',strip=True))>=100 or len(block.select('img'))>=1:return block
    return None

def extract(markup,url,ts):
    soup=BeautifulSoup(markup,'html.parser')
    title_node=soup.select_one('article h1.entry-title, h1.entry-title, article h1, h1.post-title, h2.entry-title')
    if not title_node:title_node=soup.select_one('h1')
    title=title_node.get_text(' ',strip=True) if title_node else ''
    if not title or title.lower() in (DOMAIN, 'archives'):return None
    body=select_content(soup)
    if not body:return None
    for tag in body.select('a[href],img[src],img[data-src]'):
        for attr in ['href','src','data-src']:
            if tag.has_attr(attr):
                raw=tag[attr]
                if raw.startswith('/web/'):raw=BASE+raw
                tag[attr]=original(raw)
    for tag in body.select('script,iframe,form'):tag.decompose()
    date=''
    node=soup.select_one('time[datetime],meta[property="article:published_time"],meta[name="date"],.entry-date,.post-date,time')
    if node:date=node.get('datetime') or node.get('content') or node.get_text(' ',strip=True)
    try:
        from dateutil import parser as dateparser
        parsed=dateparser.parse(date,fuzzy=True) if date else None
        date=parsed.strftime('%Y-%m-%d %H:%M:%S') if parsed else ''
    except Exception:date=''
    author_node=soup.select_one('a[rel="author"],.author .fn,.byline .author,.author-name')
    author=author_node.get_text(' ',strip=True) if author_node else 'Unknown'
    cats=[a.get_text(' ',strip=True) for a in soup.select('a[rel="category tag"],.cat-links a')]
    tags=[a.get_text(' ',strip=True) for a in soup.select('a[rel="tag"],.tags-links a')]
    content=str(body)
    return dict(url=url,title=title,date=date,author=author,categories=cats,tags=tags,content=content,characters=len(body.get_text(' ',strip=True)),capture=ts)

def el(parent,name,value=None,ns=None):
    tag='{'+NS[ns]+'}'+name if ns else name
    node=etree.SubElement(parent,tag)
    if value is not None:node.text=str(value)
    return node

def wxr(posts,path):
    root=etree.Element('rss',nsmap={k:v for k,v in NS.items()},version='2.0')
    channel=el(root,'channel');el(channel,'title',DOMAIN+' (Recovered)');el(channel,'link',SITE_URL);el(channel,'description',f'Recovered {FROM_YEAR}-{TO_YEAR} posts');el(channel,'wxr_version','1.2','wp');el(channel,'base_site_url',SITE_URL,'wp');el(channel,'base_blog_url',SITE_URL,'wp')
    authors=sorted(set(p['author'] for p in posts))
    for idx,name in enumerate(authors,1):
        a=el(channel,'author',ns='wp');el(a,'author_id',idx,'wp');el(a,'author_login',re.sub('[^a-z0-9]+','-',name.lower()).strip('-') or 'unknown','wp');el(a,'author_display_name',name,'wp')
    for idx,p in enumerate(posts,1):
        item=el(channel,'item');el(item,'title',p['title']);el(item,'link',p['url']);el(item,'pubDate','');el(item,'creator',re.sub('[^a-z0-9]+','-',p['author'].lower()).strip('-') or 'unknown','dc');el(item,'guid',p['url']).set('isPermaLink','false')
        el(item,'post_id',idx,'wp');el(item,'post_date',p['date'] or f'{FROM_YEAR}-01-01 00:00:00','wp');el(item,'post_date_gmt',p['date'] or f'{FROM_YEAR}-01-01 00:00:00','wp');el(item,'post_name',urllib.parse.urlsplit(p['url']).path.strip('/').split('/')[-1],'wp');el(item,'status','draft','wp');el(item,'post_type','post','wp');el(item,'comment_status','closed','wp');el(item,'ping_status','closed','wp');el(item,'encoded',p['content'],'content');el(item,'encoded','','excerpt')
        for domain,names in [('category',p['categories']),('post_tag',p['tags'])]:
            for name in names:
                c=el(item,'category',name);c.set('domain',domain);c.set('nicename',re.sub('[^a-z0-9]+','-',name.lower()).strip('-'))
    Path(path).write_bytes(etree.tostring(root,encoding='UTF-8',xml_declaration=True,pretty_print=True))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',default='recovered');ap.add_argument('--limit',type=int,default=0,help='Test with first N URLs');ap.add_argument('--refresh',action='store_true',help='Recheck previously processed URLs');args=ap.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    rows=cdx(); print('CDX captures:',len(rows))
    urls=defaultdict(list)
    for row in rows:
        u=cleanurl(row['original'])
        if u and candidate(u):urls[u].append((row['timestamp'],row['original']))
    # Homepage discovery only adds candidates, never fabricated capture timestamps.
    for u in discover_from_home():
        urls.setdefault(u,[])
    print('Candidate post URLs:',len(urls))
    posts=[];report=[]
    cache_file=out/'checkpoint.json'
    cache={} if args.refresh or not cache_file.exists() else json.loads(cache_file.read_text())
    for i,(url,captures) in enumerate(sorted(urls.items()),1):
        if args.limit and i>args.limit:break
        if url in cache:
            entry=cache[url];report.append(entry['report'])
            if entry.get('post'):posts.append(entry['post'])
            print(f'{i}/{len(urls)} cached: {url}')
            continue
        archive='';post=None;errors=[];title='';chars=0
        # Resolve captures per URL. Broad CDX results are incomplete for this site.
        captures=sorted(set(captures+captures_for_url(url)),reverse=True)
        attempts=[(f'{BASE}/web/{ts}/{original_url}',ts) for ts,original_url in captures[:MAX_CAPTURES]]
        # Nearest-capture URLs are a fallback, not proof that an exact capture exists.
        if not attempts: attempts.extend(archive_fallback(url))
        for archive,ts in attempts:
            try:
                markup=get(archive)
                if not matches_post(markup,url):
                    continue
                result=extract(markup,url,ts)
                if result and (post is None or result['characters']>post['characters']):
                    post=result
            except Exception as e:
                errors.append(str(e)[:120])
        if post:
            posts.append(post);status='recovered' if post['characters']>=300 or '<img' in post['content'] else 'review_needed'
            title=post['title'];chars=post['characters']
        else:
            status='review_needed' if captures else 'no_usable_capture'
            if errors:status='failed: '+errors[-1][:100]
        record=dict(url=url,archive=archive,status=status,title=title,characters=chars)
        report.append(record)
        cache[url]={'report':record,'post':post}
        cache_file.write_text(json.dumps(cache,ensure_ascii=False,indent=2))
        print(f'{i}/{len(urls)} {status}: {url}')
    # Drop duplicates by URL and title, preserving longest version.
    dedup={}
    for p in posts:
        key=re.sub(r'\W+','',p['title'].lower())
        if key not in dedup or p['characters']>dedup[key]['characters']:dedup[key]=p
    posts=list(dedup.values())
    wxr(posts,out/'wordpress-import.xml')
    (out/'posts.json').write_text(json.dumps(posts,indent=2,ensure_ascii=False))
    with (out/'recovery-report.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['url','archive','status','title','characters']);w.writeheader();w.writerows(report)
    print(f'Exported {len(posts)} draft posts to {out}/wordpress-import.xml')
    print('Note: Images are referenced, not downloaded. Review all recovered drafts before publishing.')
if __name__=='__main__':main()
