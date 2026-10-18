#!/usr/bin/env python3
"""Daily agent for aouralive.github.io.

Every run writes ONE new guide page for one country/language (round robin),
in the same design as the existing guides, with the same sign-up link.
Then it adds the page to sitemap.xml and (optionally) posts it to Telegram.

Secrets (GitHub Actions):
  GEMINI_API_KEY      required
  TELEGRAM_BOT_TOKEN  optional (bot must be admin of the channel)
  TELEGRAM_CHANNEL    optional, e.g. @mychannel
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://aouralive.github.io/"
CTA = "https://tango.onelink.me/RCIH/hebmiioo"
STATE = ROOT / "agent" / "pages.json"
TODAY = dt.date.today().isoformat()

# country, language name, html lang, dir, home page
TARGETS = [
    ("India", "Hindi", "hi", "ltr", "hi.html"),
    ("Philippines", "English", "en", "ltr", "en.html"),
    ("Indonesia", "Indonesian", "id", "ltr", "id.html"),
    ("Brazil", "Brazilian Portuguese", "pt", "ltr", "pt.html"),
    ("Colombia", "Spanish", "es", "ltr", "es.html"),
    ("Egypt", "Egyptian Arabic (simple, warm)", "ar", "rtl", "index.html"),
    ("Turkey", "Turkish", "tr", "ltr", "tr.html"),
    ("Mexico", "Spanish", "es", "ltr", "es.html"),
    ("Nigeria", "English", "en", "ltr", "en.html"),
    ("France", "French", "fr", "ltr", "fr.html"),
    ("Vietnam", "English", "en", "ltr", "en.html"),
    ("Peru", "Spanish", "es", "ltr", "es.html"),
    ("Kenya", "English", "en", "ltr", "en.html"),
    ("Saudi Arabia", "Gulf Arabic (simple)", "ar", "rtl", "index.html"),
    ("Pakistan", "English", "en", "ltr", "en.html"),
    ("Argentina", "Spanish", "es", "ltr", "es.html"),
]
TOPICS = [
    "work from home for women with only a phone",
    "how to start earning from live streaming as a beginner",
    "part-time online job for students and stay-at-home moms",
    "how to grow viewers and receive more gifts on live streams",
    "safety and privacy tips for women who go live online",
    "how to set up your phone, light and sound for live streaming",
]
# guides that already exist per language (for internal links)
EVERGREEN = {
    "ar": [("ar-become-host.html", None), ("ar-earnings.html", None), ("ar-work-from-home.html", None)],
    "en": [("en-become-host.html", None), ("en-earnings.html", None), ("en-work-from-home.html", None)],
    "es": [("es-become-host.html", None), ("es-earnings.html", None), ("es-work-from-home.html", None)],
    "fr": [("fr-become-host.html", None), ("fr-earnings.html", None), ("fr-work-from-home.html", None)],
}


def gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("::error::GEMINI_API_KEY secret is missing")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}}).encode()
    last = None
    for model in ("gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-flash-latest"):
        for attempt in range(3):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read())
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:  # noqa
                last = e
                code = getattr(e, "code", None)
                if code == 404:
                    break
                time.sleep(8 * (attempt + 1))
    sys.exit(f"::error::Gemini failed: {last}")


def prompt_for(country, lang, topic, avoid):
    return f"""You write one helpful, honest web guide for women in {country}, written fully in {lang}.
Topic: "{topic}" — adapted to life in {country} (local context: phones, mobile data, daily schedule, family life).
The reader can start live streaming from her phone and receive virtual gifts that become earnings.

Strict rules:
- Honest: never promise or invent income amounts; say earnings vary with effort and consistency.
- Do NOT name any app, company or agency.
- Adults only: mention sign-up is for 18+.
- Practical, warm, simple sentences. No exaggeration, no fake testimonials, no statistics.
- Do not repeat these existing titles: {avoid}

Return JSON only, with exactly these keys:
{{"slug": "short-ascii-words-with-hyphens (3-6 words, english letters only, include the country)",
"title": "SEO title, max 65 characters, includes the country",
"description": "meta description, max 155 characters",
"h1": "page heading",
"intro": "2 sentences",
"hype": "1 short encouraging sentence with one emoji",
"box_title": "short call to action with emoji, e.g. start your first live today",
"box_text": "1 sentence: free sign-up, takes one minute, download the app and create an account",
"cta": "button text with rocket emoji, e.g. download the app and sign up",
"sections": [{{"h": "heading", "p": "paragraph of 60-90 words"}}, ... exactly 5 items],
"faq": [{{"q": "question", "a": "answer"}}, ... exactly 3 items],
"home": "word for Home",
"read_more": "heading for 'Read more'",
"adults": "sentence: sign-up for adults 18+ only"}}"""


def esc(s):
    return html.escape(str(s), quote=True)


def check(d):
    need = ["slug", "title", "description", "h1", "intro", "hype", "box_title", "box_text", "cta",
            "sections", "faq", "home", "read_more", "adults"]
    for k in need:
        if not d.get(k):
            raise ValueError(f"missing {k}")
    if len(d["sections"]) < 4 or len(d["faq"]) < 2:
        raise ValueError("too short")
    blob = json.dumps(d, ensure_ascii=False).lower()
    if "tango" in blob or "agency" in blob:
        raise ValueError("names the app/agency")


def template_parts():
    t = (ROOT / "en-work-from-home.html").read_text(encoding="utf-8")
    pre = t[:t.index("<title>")]
    style = t[t.index("<style>"):t.index("</style>") + 8]
    tail = t[t.index("</head>"):]
    return pre, style, tail


def render(d, lang, direction, home, links):
    pre, style, _ = template_parts()
    pre = re.sub(r'<html lang="[a-z]+" dir="[a-z]+">', f'<html lang="{lang}" dir="{direction}">', pre)
    url = BASE + d["file"]
    box = (f'<div class="box"><b>{esc(d["box_title"])}</b><span>{esc(d["box_text"])}</span>'
           f'<a class="cta" href="{CTA}" rel="noopener noreferrer" target="_blank">{esc(d["cta"])}</a></div>')
    secs = ""
    for i, s in enumerate(d["sections"][:6]):
        secs += f'<section><h2>{esc(s["h"])}</h2><p>{esc(s["p"])}</p></section>'
        if i == 1:
            secs += box
    faq = "".join(f'<details><summary>{esc(f["q"])}</summary><p>{esc(f["a"])}</p></details>' for f in d["faq"][:4])
    guides = "".join(f'<a href="{esc(h)}">{esc(t)}</a>' for h, t in links)
    ld = [
        {"@context": "https://schema.org", "@type": "Article", "headline": d["h1"], "description": d["description"],
         "inLanguage": lang, "datePublished": TODAY, "dateModified": TODAY, "image": BASE + "live2.jpg",
         "mainEntityOfPage": url, "author": {"@type": "Organization", "name": "Aoura Live"},
         "publisher": {"@type": "Organization", "name": "Aoura Live"}},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in d["faq"][:4]]},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": d["home"], "item": BASE + home},
            {"@type": "ListItem", "position": 2, "name": d["h1"], "item": url}]},
    ]
    head = (pre + f'<title>{esc(d["title"])}</title>\n<meta name="description" content="{esc(d["description"])}">\n'
            f'<link rel="canonical" href="{url}">\n<meta property="og:type" content="article">\n'
            f'<meta property="og:title" content="{esc(d["h1"])}">\n<meta property="og:description" content="{esc(d["description"])}">\n'
            f'<meta property="og:url" content="{url}">\n<meta property="og:image" content="{BASE}live2.jpg">\n'
            '<meta name="theme-color" content="#0B0520">\n<link rel="icon" href="favicon.svg" type="image/svg+xml">\n'
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Readex+Pro:wght@500;600;700&family=IBM+Plex+Sans+Arabic:wght@400;500;600&display=swap">\n')
    body = (f'\n<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>\n</head>\n<body>\n'
            f'<div class="page"><main class="wrap art">\n'
            f'<header class="top"><a class="brand" href="{home}" style="text-decoration:none"><span class="dot"></span>Aoura Live</a></header>\n'
            f'<nav class="crumb"><a href="{home}">{esc(d["home"])}</a> › {esc(d["h1"])}</nav>\n<h1>{esc(d["h1"])}</h1>\n'
            f'<p class="intro">{esc(d["intro"])}</p>\n<p class="hype">{esc(d["hype"])}</p>\n{box}\n{secs}\n'
            f'<section class="faq"><h2>FAQ</h2>{faq}</section>\n{box}\n'
            + (f'<section class="guides"><h2>{esc(d["read_more"])}</h2>{guides}</section>\n' if guides else "")
            + f'<p class="foot">{esc(d["adults"])}</p>\n</main>\n'
            f'<a class="stick" href="{CTA}" rel="noopener noreferrer" target="_blank">{esc(d["cta"])}</a></div>\n</body>\n</html>\n')
    return head + style + body


def guide_title(path):
    m = re.search(r"<h1>(.*?)</h1>", (ROOT / path).read_text(encoding="utf-8"), re.S)
    return html.unescape(m.group(1).strip()) if m else path


def add_to_sitemap(file):
    sm = ROOT / "sitemap.xml"
    s = sm.read_text(encoding="utf-8")
    u = BASE + file
    if u not in s:
        s = s.replace("</urlset>", f"<url><loc>{u}</loc><lastmod>{TODAY}</lastmod></url></urlset>")
        sm.write_text(s, encoding="utf-8")


def telegram(d, file):
    raw = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chan = os.environ.get("TELEGRAM_CHANNEL", "").strip()
    m = re.search(r"\d{6,12}:[A-Za-z0-9_-]{30,40}", raw)
    if not m or not chan:
        print("telegram: not configured, skipped")
        return
    if not chan.startswith("@") and not chan.lstrip("-").isdigit():
        chan = "@" + chan
    text = f"✨ <b>{esc(d['h1'])}</b>\n\n{esc(d['intro'])}\n\n👉 {BASE}{file}"
    body = urllib.parse.urlencode({"chat_id": chan, "text": text, "parse_mode": "HTML"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{m.group(0)}/sendMessage", data=body, timeout=30) as r:
            print("telegram:", r.status)
    except Exception as e:  # noqa
        print("::warning::telegram failed:", getattr(e, "code", e))


def main():
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"n": 0, "pages": []}
    n = state["n"]
    country, langname, lang, direction, home = TARGETS[n % len(TARGETS)]
    topic = TOPICS[(n // len(TARGETS)) % len(TOPICS)]
    avoid = [p["title"] for p in state["pages"] if p["lang"] == lang][-15:]
    d = None
    for _ in range(3):
        try:
            d = json.loads(gemini(prompt_for(country, langname, topic, avoid)))
            check(d)
            break
        except Exception as e:  # noqa
            print("::warning::retry:", e)
            d = None
    if not d:
        sys.exit("::error::could not write a valid page")
    slug = re.sub(r"[^a-z0-9-]+", "-", d["slug"].lower()).strip("-")[:60] or f"guide-{n}"
    file = f"{lang}-{slug}.html"
    i = 2
    while (ROOT / file).exists():
        file = f"{lang}-{slug}-{i}.html"
        i += 1
    d["file"] = file
    same = [p for p in state["pages"] if p["lang"] == lang][-4:]
    links = [(p["file"], p["title"]) for p in reversed(same)]
    links += [(h, guide_title(h)) for h, _ in EVERGREEN.get(lang, []) if (ROOT / h).exists()]
    (ROOT / file).write_text(render(d, lang, direction, home, links[:6]), encoding="utf-8")
    add_to_sitemap(file)
    state["n"] = n + 1
    state["pages"].append({"file": file, "title": d["h1"], "lang": lang, "country": country, "date": TODAY})
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", file, "|", country, "|", topic)
    (ROOT / "agent" / "last.json").write_text(json.dumps({"file": file, "h1": d["h1"], "intro": d["intro"]},
                                                         ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:] == ["--telegram"]:
        last = json.loads((ROOT / "agent" / "last.json").read_text(encoding="utf-8"))
        telegram(last, last["file"])
    else:
        main()
