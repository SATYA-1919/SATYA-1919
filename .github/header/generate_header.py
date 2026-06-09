#!/usr/bin/env python3
"""
Self-contained generator for SATYA-1919's animated GitHub profile header.

Fetches live data on every run and rewrites <repo>/header.svg:
  - contribution calendar (rolling last 12 months)  -> scraped, token-free
  - language breakdown (byte-weighted)              -> REST API (token) or scrape
  - lines of code added/removed (your commits only) -> shallow git history

Designed to run in GitHub Actions on a schedule + on push. No secrets required;
GITHUB_TOKEN (auto-provided) is used for the language API when available.

NOTE: GitHub only exposes PRIVATE contributions to the public endpoint when you
enable Settings -> Public profile -> "Include private contributions on my profile".
With that on, the count here matches the total on your profile.
"""
import os, re, json, math, subprocess, tempfile, urllib.request, urllib.error
from pathlib import Path
from datetime import date

# ----------------------------------------------------------------------------- config
LOGIN  = os.environ.get("GH_LOGIN", "SATYA-1919")
TOKEN  = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
HERE   = Path(__file__).resolve().parent
ROOT   = HERE.parents[1] if (HERE.name == "header" and HERE.parent.name == ".github") else HERE
OUT    = Path(os.environ.get("HEADER_OUT", ROOT / "header.svg"))
FONTS  = Path(os.environ.get("HEADER_FONTS", HERE / "fonts.json"))

# commits authored by you (name/email substrings matched by `git log --author`)
AUTHOR_MATCHES = ["satya.19.2004", "186605103+SATYA-1919"]

# contact line (display text inside the SVG; clickable links live in README.md)
LINKEDIN = "linkedin.com/in/satyaki-tirumal-541b98283"
PHONE    = "+91 99497 59581"
EMAIL    = "satya.19.2004@gmail.com"

UA = {"User-Agent": "satya-profile-header"}

# ----------------------------------------------------------------------------- helpers
def _fetch(url, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def _api(path):
    return json.loads(_fetch("https://api.github.com" + path,
        {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}))

# ----------------------------------------------------------------------------- fetch: contributions
def fetch_contributions():
    """Return (total, grid[weeks][7] of level 0-4, stats dict). Token-free scrape."""
    html = _fetch(f"https://github.com/users/{LOGIN}/contributions")
    total = int(re.search(r"([\d,]+)\s+contributions", html).group(1).replace(",", ""))
    cells = sorted(re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*data-level="(\d)"', html))

    pd = lambda s: date(*map(int, s.split("-")))
    start = pd(cells[0][0]); start_dow = (start.weekday() + 1) % 7
    weeks = {}
    for s, lvl in cells:
        col = (pd(s) - start).days + start_dow
        weeks.setdefault(col // 7, {})[(pd(s).weekday() + 1) % 7] = int(lvl)
    ncols = max(weeks) + 1
    grid = [[weeks.get(c, {}).get(r, 0) for r in range(7)] for c in range(ncols)]

    levels = [int(l) for _, l in cells]
    active = sum(1 for x in levels if x > 0)
    longest = run = 0
    for _, l in cells:
        run = run + 1 if int(l) > 0 else 0
        longest = max(longest, run)
    return total, grid, {"active_days": active, "longest_streak": longest}

# ----------------------------------------------------------------------------- fetch: repos + languages
def fetch_repos():
    if TOKEN:
        try:
            data = _api(f"/users/{LOGIN}/repos?per_page=100&type=owner")
            return [r["name"] for r in data if not r.get("fork")]
        except urllib.error.URLError:
            pass
    html = _fetch(f"https://github.com/{LOGIN}?tab=repositories")
    return re.findall(r'itemprop="name codeRepository"[^>]*>\s*([^<\s]+)', html)

def fetch_languages(repos):
    agg = {}
    if TOKEN:
        try:
            for r in repos:
                for k, v in _api(f"/repos/{LOGIN}/{r}/languages").items():
                    agg[k] = agg.get(k, 0) + v
        except urllib.error.URLError:
            agg = {}
    if not agg:                                                  # scrape fallback
        for r in repos:
            try:
                h = _fetch(f"https://github.com/{LOGIN}/{r}")
            except urllib.error.URLError:
                continue
            i = h.find("Languages</h2>")
            if i < 0:
                continue
            block = h[i:i + 4000]
            seen = {}
            for lang, pct in re.findall(r">([A-Za-z][A-Za-z0-9+#. ]*?)</span>\s*<span[^>]*>(\d+\.\d+)%", block):
                seen.setdefault(lang.strip(), float(pct))
            for k, v in seen.items():
                agg[k] = agg.get(k, 0) + v
    total = sum(agg.values()) or 1
    return {k: round(v / total * 100, 1) for k, v in sorted(agg.items(), key=lambda x: -x[1])}

# ----------------------------------------------------------------------------- fetch: lines of code
def fetch_loc(repos):
    """Sum your insertions/deletions across repos, excluding vendored/generated files."""
    exclude = [":(exclude)**/node_modules/**", ":(exclude)node_modules/**",
               ":(exclude)**/build/**", ":(exclude)build/**", ":(exclude)**/.dart_tool/**",
               ":(exclude)*package-lock.json", ":(exclude)*yarn.lock", ":(exclude)*pubspec.lock",
               ":(exclude)*.lock", ":(exclude)*.g.dart", ":(exclude)*.freezed.dart",
               ":(exclude)*.map", ":(exclude)*.svg", ":(exclude)*.png", ":(exclude)*.jpg",
               ":(exclude)*.ttf", ":(exclude)*.woff2"]
    authors = [a for m in AUTHOR_MATCHES for a in ("--author", m)]
    added = removed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for r in repos:
            dst = os.path.join(tmp, r)
            clone = subprocess.run(
                ["git", "clone", "--quiet", "--filter=blob:none",
                 f"https://github.com/{LOGIN}/{r}.git", dst],
                capture_output=True)
            if clone.returncode != 0:
                continue
            out = subprocess.run(
                ["git", "-C", dst, "log", *authors, "--pretty=tformat:", "--numstat", "--", ".", *exclude],
                capture_output=True, text=True).stdout
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and parts[0] != "-" and parts[1] != "-":
                    added += int(parts[0]); removed += int(parts[1])
    return {"added": added, "removed": removed}

# ----------------------------------------------------------------------------- svg build
BG="#0D1117"; PANEL="#0E141C"; BORDER="#21262D"; HAIR="#1B2128"
ACCENT="#388BFD"; GREEN="#39D353"; GREEN_D="#2EA043"; RED="#F85149"
TXT_HI="#E6EDF3"; TXT_MID="#9DA7B3"; TXT_DIM="#6E7681"; TXT_FAINT="#4B535D"
LANG_COL={"TypeScript":"#3178C6","Kotlin":"#A97BFF","Dart":"#00B4AB","CSS":"#E26FA8",
          "HTML":"#E3683A","JavaScript":"#E3C72E","other":"#6E7681"}
LVL_TOP={1:"#0E4429",2:"#006D32",3:"#26A641",4:"#39D353"}
FF="'JBM','JetBrains Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace"

def _hx(h): h=h.lstrip("#"); return tuple(int(h[i:i+2],16) for i in (0,2,4))
def _shade(h,f): r,g,b=_hx(h); return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

def build_svg(total, grid, stats, langs, loc, repos):
    W,H,M = 1000,600,14
    WX,WY,WW,WH = M,M,W-2*M,H-2*M
    PADL,PADR = 46,954
    S=[]; add=S.append
    def text(x,y,s,size=13,fill=TXT_MID,weight=400,anchor="start",spacing=0,cls=""):
        ls=f' letter-spacing="{spacing}"' if spacing else ""
        c=f' class="{cls}"' if cls else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FF}" font-size="{size}" '
                f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}{c}>{s}</text>')

    defs=f'''<defs>
  <linearGradient id="winbg" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#0E141C"/><stop offset="1" stop-color="#0A0E14"/></linearGradient>
  <radialGradient id="glow" cx="20%" cy="-5%" r="85%">
    <stop offset="0" stop-color="#1F6FEB" stop-opacity="0.12"/>
    <stop offset="55%" stop-color="#1F6FEB" stop-opacity="0"/></radialGradient>
  <radialGradient id="floorglow" cx="50%" cy="50%" r="60%">
    <stop offset="0" stop-color="#0B3A22" stop-opacity="0.5"/>
    <stop offset="100%" stop-color="#0B3A22" stop-opacity="0"/></radialGradient>
  <linearGradient id="footedge" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{ACCENT}" stop-opacity="0"/>
    <stop offset="0.5" stop-color="{ACCENT}" stop-opacity="0.45"/>
    <stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></linearGradient>
  <filter id="soft" x="-40%" y="-40%" width="180%" height="180%">
    <feDropShadow dx="0" dy="1" stdDeviation="4" flood-color="#39D353" flood-opacity="0.35"/></filter>
  <filter id="outer" x="-20%" y="-20%" width="140%" height="160%">
    <feDropShadow dx="0" dy="10" stdDeviation="22" flood-color="#000000" flood-opacity="0.55"/></filter>
  <pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse">
    <circle cx="1" cy="1" r="0.7" fill="#2B3440" fill-opacity="0.32"/></pattern>
  <style>
    @font-face{{font-family:'JBM';font-weight:400;src:url(FONT_REG) format('woff2');}}
    @font-face{{font-family:'JBM';font-weight:500;src:url(FONT_MED) format('woff2');}}
    @font-face{{font-family:'JBM';font-weight:700;src:url(FONT_BLD) format('woff2');}}
    @font-face{{font-family:'JBM';font-weight:800;src:url(FONT_XBD) format('woff2');}}
    .col{{opacity:0;animation:rise .5s ease-out forwards;}}
    @keyframes rise{{from{{opacity:0;transform:translateY(6px);}}to{{opacity:1;transform:translateY(0);}}}}
    .cur{{animation:blink 1.15s steps(1) infinite;}}
    @keyframes blink{{0%,50%{{opacity:1;}}50.01%,100%{{opacity:0;}}}}
    .peak{{animation:pulse 2.6s ease-in-out infinite;}}
    @keyframes pulse{{0%,100%{{opacity:.8;}}50%{{opacity:1;}}}}
    .seg{{opacity:0;animation:fadein .6s ease forwards;}}
    @keyframes fadein{{to{{opacity:1;}}}}
  </style></defs>'''

    # window chrome
    add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    add(f'<g filter="url(#outer)"><rect x="{WX}" y="{WY}" width="{WW}" height="{WH}" rx="14" fill="url(#winbg)" stroke="{BORDER}"/></g>')
    add(f'<rect x="{WX}" y="{WY}" width="{WW}" height="{WH}" rx="14" fill="url(#dots)"/>')
    add(f'<rect x="{WX}" y="{WY}" width="{WW}" height="{WH}" rx="14" fill="url(#glow)"/>')
    for i,c in enumerate(["#FF5F57","#FEBC2E","#28C840"]):
        add(f'<circle cx="{WX+26+i*18}" cy="{WY+20}" r="5" fill="{c}" fill-opacity="0.9"/>')
    add(text(WX+86,WY+24,f'{LOGIN} / <tspan fill="{TXT_MID}">README.md</tspan><tspan fill="{TXT_FAINT}"> / profile</tspan>',size=12,fill=TXT_DIM,weight=500))
    add(f'<line x1="{WX}" y1="{WY+40}" x2="{WX+WW}" y2="{WY+40}" stroke="{HAIR}"/>')

    # header + name + taglines
    hy=86
    add(f'<circle cx="{PADL+5}" cy="{hy-4}" r="4.5" fill="{GREEN_D}" filter="url(#soft)"/>')
    add(text(PADL+20,hy,f'GITHUB / <tspan fill="{ACCENT}" font-weight="700">@{LOGIN}</tspan>',size=14,fill=TXT_MID,weight=500,spacing=0.5))
    add(text(PADR,hy,"HYDERABAD, INDIA",size=12.5,fill=TXT_DIM,weight=500,anchor="end",spacing=1.2))
    add(text(PADL,130,"SATYA",size=40,fill=TXT_HI,weight=800,spacing=2))
    add(f'<rect class="cur" x="{PADL+157}" y="108" width="13" height="26" fill="{ACCENT}" rx="1"/>')
    add(text(PADL,160,f'<tspan fill="#3FB950">#</tspan> Full-stack <tspan fill="{TXT_FAINT}">·</tspan> App Development',size=15,fill=TXT_MID,weight=500))
    add(text(PADL,182,f'<tspan fill="#3FB950">#</tspan> <tspan fill="{TXT_DIM}">Electronics &amp; Computer Engineering</tspan>',size=13,fill=TXT_DIM))

    # section label
    sy=220
    add(text(PADL,sy,f'<tspan fill="{ACCENT}">&gt;</tspan> CONTRIBUTION GRAPH',size=13,fill=TXT_MID,weight=700,spacing=1.5))
    cw=12*0.6+1.0
    add(text(PADR-17*cw,sy,str(total),size=12,fill=GREEN,weight=800,anchor="end"))
    add(text(PADR,sy,"IN THE LAST YEAR",size=12,fill=TXT_DIM,weight=700,anchor="end",spacing=1))

    # isometric grid
    ncols=len(grid)
    wx,wy,dx,dy,hU = 15.6,1.6,8.2,8.2,9.0
    P=lambda gx,gy,h=0.0:(gx*wx+gy*dx,-gx*wy+gy*dy-h)
    def tile0(c,r):
        nt=P(c,r);et=P(c+1,r);st=P(c+1,r+1);wt=P(c,r+1)
        return (f'<polygon points="{nt[0]:.1f},{nt[1]:.1f} {et[0]:.1f},{et[1]:.1f} '
                f'{st[0]:.1f},{st[1]:.1f} {wt[0]:.1f},{wt[1]:.1f}" fill="#181F29" stroke="#0E141B" stroke-width="0.5"/>')
    def cube(c,r,lvl):
        h=lvl*hU
        nt=P(c,r,h);et=P(c+1,r,h);st=P(c+1,r+1,h);wt=P(c,r+1,h)
        wb=P(c,r+1,0);sb=P(c+1,r+1,0)
        top=LVL_TOP[lvl];right=_shade(top,0.72);left=_shade(top,0.5)
        cls=' class="peak"' if lvl==4 else ''
        return (f'<g{cls}>'
            f'<polygon points="{nt[0]:.1f},{nt[1]:.1f} {wt[0]:.1f},{wt[1]:.1f} {wb[0]:.1f},{wb[1]:.1f} {P(c,r,0)[0]:.1f},{P(c,r,0)[1]:.1f}" fill="{left}"/>'
            f'<polygon points="{wt[0]:.1f},{wt[1]:.1f} {st[0]:.1f},{st[1]:.1f} {sb[0]:.1f},{sb[1]:.1f} {wb[0]:.1f},{wb[1]:.1f}" fill="{right}"/>'
            f'<polygon points="{nt[0]:.1f},{nt[1]:.1f} {et[0]:.1f},{et[1]:.1f} {st[0]:.1f},{st[1]:.1f} {wt[0]:.1f},{wt[1]:.1f}" fill="{top}"/></g>')
    xs=[];ys=[]
    for c in range(ncols+1):
        for r in range(8):
            for h in (0,4*hU):
                p=P(c,r,h);xs.append(p[0]);ys.append(p[1])
    minx,maxx,miny,maxy=min(xs),max(xs),min(ys),max(ys)
    bbw,bbh=maxx-minx,maxy-miny
    TX0,TX1,TY0,TY1=PADL-4,PADR+4,224,398
    scale=min((TX1-TX0)/bbw,(TY1-TY0)/bbh)
    tx=TX0-minx*scale+((TX1-TX0)-bbw*scale)/2
    ty=TY0-miny*scale+((TY1-TY0)-bbh*scale)/2
    add(f'<ellipse cx="{((minx+maxx)/2)*scale+tx:.0f}" cy="{maxy*scale+ty-10:.0f}" rx="{bbw*scale*0.5:.0f}" ry="18" fill="url(#floorglow)"/>')
    add(f'<g transform="translate({tx:.2f},{ty:.2f}) scale({scale:.4f})">')
    A=P(0,0);C=P(ncols,7);Dd=P(0,7);SK=5
    A0=P(0,0,-SK);D0=P(0,7,-SK);C0=P(ncols,7,-SK)
    add(f'<polygon points="{A[0]:.1f},{A[1]:.1f} {Dd[0]:.1f},{Dd[1]:.1f} {D0[0]:.1f},{D0[1]:.1f} {A0[0]:.1f},{A0[1]:.1f}" fill="#0B0F15"/>')
    add(f'<polygon points="{Dd[0]:.1f},{Dd[1]:.1f} {C[0]:.1f},{C[1]:.1f} {C0[0]:.1f},{C0[1]:.1f} {D0[0]:.1f},{D0[1]:.1f}" fill="#090C12"/>')
    cells=[(c,r,grid[c][r]) for c in range(ncols) for r in range(7)]
    cells.sort(key=lambda t:((t[1]+1)*dy-t[0]*wy,t[0]))
    for c,r,lvl in cells:
        add(f'<g class="col" style="animation-delay:{c*0.009:.3f}s">')
        add(tile0(c,r) if lvl==0 else cube(c,r,lvl))
        add('</g>')
    add('</g>')

    # divider
    dvy=410
    add(f'<line x1="{PADL}" y1="{dvy}" x2="{PADR}" y2="{dvy}" stroke="{HAIR}"/>')

    # donut (languages)
    order=["TypeScript","Kotlin","Dart","CSS","HTML"]
    shown=[(k,langs[k]) for k in order if k in langs]
    other=round(sum(v for k,v in langs.items() if k not in order),1)
    if other>0: shown.append(("other",other))
    totp=sum(v for _,v in shown) or 1
    DCX,DCY,RO,RI=PADL+58,490,40,26
    add(text(PADL,dvy+28,f'<tspan fill="{ACCENT}">&gt;</tspan> STACK',size=12,fill=TXT_MID,weight=700,spacing=1.5))
    arc=lambda cx,cy,r,a0,a1:(cx+r*math.cos(a0),cy+r*math.sin(a0),cx+r*math.cos(a1),cy+r*math.sin(a1),1 if (a1-a0)>math.pi else 0)
    ang=-math.pi/2
    for i,(name,pct) in enumerate(shown):
        a0,a1=ang,ang+pct/totp*2*math.pi
        ox0,oy0,ox1,oy1,lg=arc(DCX,DCY,RO,a0,a1)
        ix1,iy1,ix0,iy0,_=arc(DCX,DCY,RI,a1,a0)
        add(f'<path class="seg" style="animation-delay:{0.5+i*0.07:.2f}s" d="M{ox0:.1f},{oy0:.1f} A{RO},{RO} 0 {lg} 1 {ox1:.1f},{oy1:.1f} L{ix1:.1f},{iy1:.1f} A{RI},{RI} 0 {lg} 0 {ix0:.1f},{iy0:.1f} Z" fill="{LANG_COL.get(name,TXT_DIM)}"/>')
        ang=a1+0.05
    add(f'<circle cx="{DCX}" cy="{DCY}" r="{RI-3}" fill="{PANEL}"/>')
    add(text(DCX,DCY-1,str(len(shown)),size=17,fill=TXT_HI,weight=800,anchor="middle"))
    add(text(DCX,DCY+12,"LANGS",size=7.5,fill=TXT_DIM,weight=500,anchor="middle",spacing=1))
    lx,ly=DCX+62,455
    for name,pct in shown:
        add(f'<rect x="{lx}" y="{ly-8}" width="9" height="9" rx="2" fill="{LANG_COL.get(name,TXT_DIM)}"/>')
        add(text(lx+15,ly,name,size=11,fill=TXT_MID,weight=500))
        add(text(lx+120,ly,f'{pct:.1f}%',size=11,fill=TXT_DIM,weight=500,anchor="end"))
        ly+=14

    # activity stats
    add(text(648,dvy+28,f'<tspan fill="{ACCENT}">&gt;</tspan> ACTIVITY',size=12,fill=TXT_MID,weight=700,spacing=1.5))
    quad=[(str(total),"CONTRIBUTIONS"),(str(stats["active_days"]),"ACTIVE DAYS"),
          (str(stats["longest_streak"]),"LONGEST STREAK"),(str(len(repos)),"PUBLIC REPOS")]
    colx=[648,810]; rowy=[470,510]
    for idx,(val,lab) in enumerate(quad):
        x=colx[idx%2]; y=rowy[idx//2]
        add(text(x,y,val,size=27,fill=TXT_HI,weight=800))
        add(text(x+2,y+16,lab,size=9.5,fill=TXT_DIM,weight=500,spacing=1.2))

    # lines of code bar
    added,removed=loc["added"],loc["removed"]; tot=(added+removed) or 1
    bly=542
    add(text(PADL,bly,f'<tspan fill="{ACCENT}">&gt;</tspan> LINES OF CODE',size=12,fill=TXT_MID,weight=700,spacing=1.5))
    c2=11*0.6+0.5
    radd=f"++ {added:,}"; rrem=f"-- {removed:,}"
    add(text(PADR,bly,rrem,size=11,fill=RED,weight=700,anchor="end",spacing=0.5))
    add(text(PADR-(len(rrem)+3)*c2,bly,radd,size=11,fill=GREEN,weight=700,anchor="end",spacing=0.5))
    barY,barH,gap=bly+11,7,3; barW=PADR-PADL
    gw=(added/tot)*(barW-gap); rw=(removed/tot)*(barW-gap)
    add(f'<rect x="{PADL}" y="{barY}" width="{barW}" height="{barH}" rx="{barH/2}" fill="#11161E"/>')
    add(f'<rect class="seg" style="animation-delay:.55s" x="{PADL}" y="{barY}" width="{gw:.1f}" height="{barH}" rx="2" fill="{GREEN}"/>')
    add(f'<rect class="seg" style="animation-delay:.7s" x="{PADL+gw+gap:.1f}" y="{barY}" width="{rw:.1f}" height="{barH}" rx="2" fill="{RED}"/>')

    # footer
    fy=580
    add(f'<line x1="{PADL}" y1="{fy-15}" x2="{PADR}" y2="{fy-15}" stroke="url(#footedge)"/>')
    add(text(PADL,fy,f'{LINKEDIN} <tspan fill="{TXT_FAINT}">::</tspan> <tspan fill="{TXT_MID}">{PHONE}</tspan> <tspan fill="{TXT_FAINT}">::</tspan> <tspan fill="{ACCENT}">{EMAIL}</tspan>',size=11.5,fill=TXT_MID,weight=500,spacing=0.3))

    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}" role="img" aria-label="{LOGIN} GitHub profile header">'
            + defs + "".join(S) + "</svg>")

def inject_fonts(svg):
    f = json.load(open(FONTS))
    for token, key in (("FONT_REG","reg"),("FONT_MED","med"),("FONT_BLD","bld"),("FONT_XBD","xbd")):
        svg = svg.replace(token, f"data:font/woff2;base64,{f[key]}")
    return svg

# ----------------------------------------------------------------------------- main
def main():
    print(f"login={LOGIN} token={'yes' if TOKEN else 'no'}")
    total, grid, stats = fetch_contributions()
    repos = fetch_repos()
    langs = fetch_languages(repos)
    loc   = fetch_loc(repos)
    print(f"contributions={total} active={stats['active_days']} streak={stats['longest_streak']} "
          f"repos={len(repos)} langs={len(langs)} loc=+{loc['added']}/-{loc['removed']}")
    svg = inject_fonts(build_svg(total, grid, stats, langs, loc, repos))
    OUT.write_text(svg)
    print(f"wrote {OUT} ({len(svg)//1024} KB)")

if __name__ == "__main__":
    main()
