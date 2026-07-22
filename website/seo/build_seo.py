#!/usr/bin/env python3
"""Generate programmatic-SEO "resale value" pages for snapworth.eu.

Targets high-intent searches like "how much is a <item> worth to resell".
Outputs static HTML into website/worth/ (served at /worth/<slug> via cleanUrls),
a hub page at /worth, plus sitemap.xml + robots.txt. On-brand, self-contained,
with FAQ schema for rich results.

Run:  python3 website/seo/build_seo.py
"""
import html, pathlib, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]          # website/
OUT = ROOT / "worth"
OUT.mkdir(parents=True, exist_ok=True)

SITE = "https://www.snapworth.eu"
APP_STORE = "https://apps.apple.com/us/app/snapworth-resale-scanner/id6788521307"
TODAY = datetime.date.today().isoformat()
YEAR = datetime.date.today().year

# ── Item dataset ─────────────────────────────────────────────────────────────
# Ranges are typical US secondhand resale values, framed as guidance. The app is
# the tool for an exact, photo-based valuation (that's the CTA).
ITEMS = [
    dict(slug="patagonia-better-sweater", name="Patagonia Better Sweater", cat="Clothing",
         low=40, high=85,
         intro="Patagonia's Better Sweater fleece is a resale staple — the brand's repair reputation and steady demand keep secondhand prices strong, especially in classic colorways.",
         conditions=[("Like new", "$65–$85"), ("Good", "$40–$60"), ("Worn / pilled", "$25–$38")],
         factors=["Colorway (classic neutrals and retro tones sell fastest)", "Size (M/L move quickest)", "Pilling and pet hair", "Zipper and cuff condition", "Whether it's a current or discontinued style"],
         platforms=["Poshmark", "eBay", "Grailed", "Depop"],
         faqs=[("Are older Patagonia fleeces worth more?", "Some discontinued colorways and vintage Synchilla pieces command a premium, but most Better Sweaters resell on condition and color rather than age."),
               ("Does Patagonia's warranty affect resale?", "Yes — Patagonia's Ironclad Guarantee and repair service give buyers confidence, which supports higher secondhand prices than comparable fleeces.")]),
    dict(slug="north-face-nuptse-700", name="The North Face Nuptse 700", cat="Clothing",
         low=110, high=200,
         intro="The North Face Nuptse 700 puffer is one of the most reliable flips in outerwear — strong year-round demand and iconic status keep resale values high.",
         conditions=[("Like new", "$160–$200"), ("Good", "$120–$150"), ("Worn", "$80–$110")],
         factors=["Colorway (black and bold retro colors lead)", "Real vs. replica (buyers scrutinize this heavily)", "Down loft and any leaks", "Zipper pulls and logo condition", "Season (peaks in fall/winter)"],
         platforms=["Grailed", "eBay", "Depop", "Poshmark"],
         faqs=[("How do I tell a real Nuptse from a fake?", "Check the stitching on the logo, the RDS down tag, zipper quality, and the interior neck label. Replicas are common, so clear photos raise buyer trust and price."),
               ("When do puffers sell for the most?", "Demand and prices peak in autumn and early winter. Listing in September–December typically gets the best offers.")]),
    dict(slug="levis-501-vintage", name="Vintage Levi's 501 Jeans", cat="Clothing",
         low=30, high=90,
         intro="Vintage Levi's 501s are a cornerstone of the resale market. Made-in-USA pairs, big-E tabs, and selvedge denim can push prices well above modern 501s.",
         conditions=[("Vintage / made in USA", "$60–$90"), ("Good modern pair", "$35–$55"), ("Worn / flawed", "$20–$30")],
         factors=["Made in USA vs. imported", "Big-E red tab (pre-1971)", "Selvedge denim", "Fades and repairs (some add value)", "Measured waist/inseam accuracy"],
         platforms=["eBay", "Depop", "Etsy", "Grailed"],
         faqs=[("What makes vintage Levi's valuable?", "Age markers like the big-E tab, single-stitch details, made-in-USA construction, and selvedge denim drive value. Natural fading is often a plus for vintage buyers."),
               ("Should I list jeans by tag size or measured size?", "Always include measured waist and inseam. Vintage sizing runs small and buyers filter by measurements, which reduces returns and raises trust.")]),
    dict(slug="carhartt-detroit-jacket", name="Carhartt Detroit Jacket", cat="Clothing",
         low=60, high=150,
         intro="The Carhartt Detroit Jacket is a workwear icon with crossover fashion demand. Broken-in and vintage pieces are especially sought after.",
         conditions=[("Vintage / union made", "$110–$150"), ("Good", "$70–$100"), ("Heavily worn", "$45–$65")],
         factors=["Union-made / vintage tags", "Blanket lining condition", "Fades and honest wear (often desirable)", "Color (brown duck is classic)", "Size availability"],
         platforms=["Grailed", "eBay", "Depop", "Poshmark"],
         faqs=[("Is worn-in Carhartt worth more?", "Often yes — faded, broken-in Detroit jackets are prized for their look, provided seams, zippers, and lining are intact."),
               ("How do I date a vintage Carhartt?", "Tag style, 'Union Made' labels, and construction details help date pieces. Older union-made jackets typically resell higher.")]),
    dict(slug="nike-air-max-90", name="Nike Air Max 90", cat="Shoes",
         low=40, high=120,
         intro="Nike Air Max 90s have deep, steady resale demand. Original and collaboration colorways can command a premium over general releases.",
         conditions=[("Deadstock / unworn", "$90–$120"), ("Good", "$55–$80"), ("Used", "$40–$50")],
         factors=["Colorway and collaborations", "Deadstock vs. worn", "Original box included", "Sole and midsole condition", "Size (popular sizes sell faster)"],
         platforms=["StockX", "eBay", "GOAT", "Depop"],
         faqs=[("Does the original box matter for sneakers?", "Yes — the original box, especially with the label, adds value and buyer confidence, particularly for deadstock pairs."),
               ("Which Air Max 90 colorways resell best?", "Original 'Infrared', triple black/white, and limited collaborations tend to hold value best.")]),
    dict(slug="lululemon-align-leggings", name="Lululemon Align Leggings", cat="Clothing",
         low=30, high=60,
         intro="Lululemon Align leggings have one of the strongest resale markets in activewear, thanks to high retail prices and loyal demand for specific colors.",
         conditions=[("Like new", "$45–$60"), ("Good", "$32–$44"), ("Worn / pilled", "$20–$28")],
         factors=["Color (rare/discontinued colors spike)", "Inseam length", "Pilling and piling on the waistband", "Size dot / rip tag present", "Overall stretch and opacity"],
         platforms=["Poshmark", "Mercari", "eBay", "Depop"],
         faqs=[("Why do some Align colors sell for more?", "Discontinued or limited seasonal colors become collectible, and demand can exceed the original retail price."),
               ("How do I prove Align leggings are authentic?", "Show the size dip tag inside and the rip tag with the style number. Clear tag photos reduce counterfeit concerns and raise price.")]),
    dict(slug="dr-martens-1460-boots", name="Dr. Martens 1460 Boots", cat="Shoes",
         low=50, high=110,
         intro="Dr. Martens 1460 boots resell well because they're built to last and expensive new. Made-in-England and vintage pairs fetch the most.",
         conditions=[("Made in England / vintage", "$85–$110"), ("Good", "$55–$75"), ("Worn", "$40–$50")],
         factors=["Made in England vs. current production", "Sole wear and resoles", "Leather cracking", "Size", "Original laces and box"],
         platforms=["eBay", "Depop", "Grailed", "Poshmark"],
         faqs=[("Are older Dr. Martens better for resale?", "Vintage and made-in-England pairs are prized for quality and typically resell higher than current mass-produced versions."),
               ("Do scuffed Docs still sell?", "Yes — broken-in leather is part of the appeal, as long as the soles and stitching are solid.")]),
    dict(slug="coach-leather-handbag", name="Coach Leather Handbag", cat="Bags",
         low=40, high=150,
         intro="Coach leather handbags are a reliable resale category. Vintage 'Made in USA' bags and full-leather styles outperform coated-canvas pieces.",
         conditions=[("Vintage leather / excellent", "$110–$150"), ("Good", "$60–$95"), ("Worn", "$40–$55")],
         factors=["Full leather vs. coated canvas", "Vintage 'Made in USA' creed patch", "Hardware tarnish", "Interior condition and smell", "Authenticity (serial/creed)"],
         platforms=["Poshmark", "eBay", "Mercari", "Vestiaire"],
         faqs=[("Are vintage Coach bags valuable?", "Many vintage all-leather Coach bags (e.g. the Willis or Bonnie Cashin era) resell well above modern coated-canvas styles."),
               ("How do I authenticate a Coach bag?", "Check the interior creed patch and serial number, stitching quality, and hardware. Photos of these details raise buyer trust and price.")]),
    dict(slug="le-creuset-dutch-oven", name="Le Creuset Dutch Oven", cat="Home",
         low=80, high=250,
         intro="Le Creuset enameled Dutch ovens hold value exceptionally well. Discontinued colors and larger sizes can approach retail prices secondhand.",
         conditions=[("Excellent / rare color", "$180–$250"), ("Good", "$110–$160"), ("Chipped enamel", "$70–$95")],
         factors=["Size (5.5qt and 7.25qt are popular)", "Discontinued colors (Flame, rare limited tones)", "Interior enamel chips", "Knob type (phenolic vs. metal)", "Lid fit and cracks"],
         platforms=["eBay", "Facebook Marketplace", "Poshmark", "Mercari"],
         faqs=[("Which Le Creuset colors are worth the most?", "Discontinued and limited-edition colors command premiums. Classic Flame and rare regional colors are especially sought after."),
               ("Does a chipped Dutch oven still sell?", "Yes, at a discount. Minor exterior chips matter less than interior enamel damage, which affects cooking.")]),
    dict(slug="vintage-pyrex-bowls", name="Vintage Pyrex Bowls", cat="Home",
         low=20, high=120,
         intro="Vintage Pyrex is a collector favorite. Rare patterns and complete nesting sets can sell for surprising sums, while common pieces move steadily.",
         conditions=[("Rare pattern / complete set", "$70–$120"), ("Good common pattern", "$30–$55"), ("Faded / worn", "$18–$28")],
         factors=["Pattern rarity (promotional patterns spike)", "Complete nesting sets vs. singles", "Print fading and dishwasher wear", "Chips and cracks", "Original lids for casseroles"],
         platforms=["eBay", "Etsy", "Facebook Marketplace", "Mercari"],
         faqs=[("Which Pyrex patterns are most valuable?", "Rare promotional patterns like Lucky in Love, Pink Gooseberry, and Atomic Eyes command the highest prices; common patterns still sell well as sets."),
               ("How does fading affect Pyrex value?", "Bright, un-faded prints sell for significantly more. Dishwasher-dulled pieces drop in value but still have a market.")]),
    dict(slug="stanley-quencher-tumbler", name="Stanley Quencher Tumbler", cat="Home",
         low=25, high=60,
         intro="The Stanley Quencher's viral demand created a lively resale market, with limited and collaboration colors reselling above retail.",
         conditions=[("Limited color / new", "$45–$60"), ("Good used", "$28–$40"), ("Scratched", "$20–$26")],
         factors=["Limited-edition and collab colors", "New vs. used", "Lid and straw included", "Scratches and dishwasher dulling", "Season/hype cycles"],
         platforms=["Mercari", "eBay", "Poshmark", "Facebook Marketplace"],
         faqs=[("Why do some Stanley cups resell above retail?", "Limited drops and collaborations (e.g. holiday and brand partnerships) sell out fast, pushing secondhand prices above the original price."),
               ("Do used Stanley tumblers sell?", "Yes, at a discount — especially popular or discontinued colors, provided the lid and straw are included.")]),
    dict(slug="ralph-lauren-polo-sweater", name="Ralph Lauren Polo Sweater", cat="Clothing",
         low=25, high=70,
         intro="Polo Ralph Lauren knitwear is a thrift-flip favorite. Cable-knits, quarter-zips, and bold vintage logos drive the strongest resale.",
         conditions=[("Vintage / excellent", "$50–$70"), ("Good", "$30–$45"), ("Worn", "$20–$28")],
         factors=["Vintage logo era", "Material (lambswool/cotton cable-knit)", "Pilling and moth holes", "Size", "Bold or rare colorways"],
         platforms=["Depop", "eBay", "Poshmark", "Grailed"],
         faqs=[("Are vintage Polo sweaters worth more?", "Older pieces with distinctive logos and quality knits generally resell higher than current basics."),
               ("Do moth holes ruin resale value?", "Small holes drop value but don't kill it — disclose them clearly with photos and price accordingly.")]),
    dict(slug="birkenstock-arizona-sandals", name="Birkenstock Arizona Sandals", cat="Shoes",
         low=30, high=70,
         intro="Birkenstock Arizonas resell steadily thanks to durability and consistent demand. Suede, shearling-lined, and limited colors do best.",
         conditions=[("Like new", "$55–$70"), ("Good", "$35–$48"), ("Worn footbed", "$25–$32")],
         factors=["Material (suede/leather/shearling)", "Footbed wear and imprint", "Cork condition", "Size", "Limited or discontinued colors"],
         platforms=["Poshmark", "eBay", "Depop", "Mercari"],
         faqs=[("Do worn Birkenstocks sell?", "Yes, at a discount. A molded footbed reduces value but many buyers seek broken-in pairs; cork and strap condition matter most."),
               ("Which Birkenstock styles resell best?", "Arizona and Boston in suede or shearling-lined versions, plus limited colors, tend to hold value best.")]),
    dict(slug="ray-ban-wayfarer-sunglasses", name="Ray-Ban Wayfarer Sunglasses", cat="Accessories",
         low=40, high=90,
         intro="Ray-Ban Wayfarers have durable resale demand. Authenticity and lens condition drive most of the price on the secondhand market.",
         conditions=[("Excellent / with case", "$70–$90"), ("Good", "$45–$60"), ("Scratched lenses", "$30–$40")],
         factors=["Authenticity (etched logo, engravings)", "Lens scratches", "Original case and cloth", "Frame color/style", "Prescription vs. original lenses"],
         platforms=["eBay", "Poshmark", "Mercari", "Vestiaire"],
         faqs=[("How do I authenticate Ray-Bans?", "Look for the etched 'RB' on the left lens, engraved logo on the arm, and consistent branding. Photos of these details raise buyer trust."),
               ("Do scratched sunglasses still sell?", "Yes, at a lower price. Replacement lenses are available, so scratched frames retain some value.")]),
    dict(slug="kitchenaid-stand-mixer", name="KitchenAid Stand Mixer", cat="Home",
         low=120, high=300,
         intro="KitchenAid stand mixers hold value strongly and sell quickly locally. Working condition, color, and included attachments drive the price.",
         conditions=[("Excellent / extra attachments", "$220–$300"), ("Good working", "$150–$200"), ("Cosmetic wear", "$120–$145")],
         factors=["Working motor and gears", "Included attachments (bowl, whisk, dough hook, extras)", "Color (popular and discontinued colors)", "Cosmetic scratches", "Bowl-lift vs. tilt-head model"],
         platforms=["Facebook Marketplace", "eBay", "Craigslist", "OfferUp"],
         faqs=[("Are KitchenAid mixers worth reselling?", "Yes — they hold value exceptionally well and sell fast locally, especially working models with attachments."),
               ("Does color affect KitchenAid resale value?", "Popular and discontinued colors can add value; unusual limited colors sometimes sell above common ones.")]),
    dict(slug="vintage-band-t-shirt", name="Vintage Band T-Shirt", cat="Clothing",
         low=30, high=200,
         intro="Vintage band tees are one of the most variable — and lucrative — resale categories. Era, band, and tour dates can swing value from tens to hundreds.",
         conditions=[("Rare band / true vintage", "$120–$200"), ("Good vintage", "$50–$90"), ("Newer / reprint", "$25–$40")],
         factors=["Band and tour rarity", "Era (single-stitch, tag brand like Brockum/Giant)", "Fades, holes, and 'thrashed' appeal", "Size (larger sells higher)", "Original vs. reprint"],
         platforms=["Depop", "eBay", "Etsy", "Grailed"],
         faqs=[("How do I know if a band tee is truly vintage?", "Single-stitch hems, tag brands like Brockum, Giant, or Anvil, and a soft, worn feel indicate true vintage. Reprints use modern double-stitch and tags."),
               ("Which band tees sell for the most?", "Rare metal, rap, and grunge tour tees from the '80s–'90s command the highest prices, especially in larger sizes.")]),
]

# ── Grammar helpers (avoid "a The North Face" / "a ... Jeans") ───────────────
PLURAL = {"levis-501-vintage", "dr-martens-1460-boots", "lululemon-align-leggings",
          "birkenstock-arizona-sandals", "ray-ban-wayfarer-sunglasses", "vintage-pyrex-bowls"}

def is_plural(it): return it["slug"] in PLURAL
def base(name): return name[4:] if name.startswith("The ") else name          # drop leading "The "
def obj(it):                                                                  # object phrase w/ article
    b = base(it["name"]);  return b if is_plural(it) else f"a {b}"
def question(it):                                                             # sentence-case H1/FAQ
    b = base(it["name"])
    return f"How much are {b} worth to resell?" if is_plural(it) else f"How much is a {b} worth to resell?"
def answer(it):
    b = base(it["name"])
    verb = "typically resell" if is_plural(it) else "typically resells"
    subj = b if is_plural(it) else f"A {b}"
    return f"{subj} {verb} for ${it['low']}–${it['high']} in the US secondhand market, depending on condition, style, and demand."

# ── Shared chrome ────────────────────────────────────────────────────────────
STYLE = """
@font-face{font-family:'Fraunces';font-style:normal;font-weight:100 900;font-display:swap;src:url('/fonts/fraunces-latin.woff2') format('woff2');}
@font-face{font-family:'Fraunces';font-style:italic;font-weight:100 900;font-display:swap;src:url('/fonts/fraunces-italic-latin.woff2') format('woff2');}
@font-face{font-family:'DM Sans';font-style:normal;font-weight:100 900;font-display:swap;src:url('/fonts/dmsans-latin.woff2') format('woff2');}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
:root{--bg:#FAF7F4;--card:#F3EDE8;--dark:#1C1410;--terracotta:#D96C47;--terra-text:#A94E29;--sage-text:#4F7355;--sage-dim:rgba(122,158,126,0.12);--warm-gray:#6E625B;--border:#E8DDD7;--white:#fff;--text-muted:#726860;}
body{background:var(--bg);color:var(--dark);font-family:'DM Sans',sans-serif;font-size:17px;line-height:1.65;-webkit-font-smoothing:antialiased;}
a{color:var(--terra-text);}
.wrap{max-width:760px;margin:0 auto;padding:0 20px;}
header.site{border-bottom:1px solid var(--border);background:var(--bg);}
header.site .wrap{display:flex;align-items:center;justify-content:space-between;height:64px;}
.logo{font-family:'Fraunces',serif;font-weight:700;font-size:22px;color:var(--dark);text-decoration:none;}
.nav-cta{background:var(--terra-text);color:#fff;text-decoration:none;font-weight:600;font-size:14px;padding:9px 18px;border-radius:10px;}
main{padding:48px 0 24px;}
.crumbs{font-size:14px;color:var(--warm-gray);margin-bottom:20px;}
.crumbs a{color:var(--warm-gray);}
h1{font-family:'Fraunces',serif;font-weight:600;font-size:40px;line-height:1.1;letter-spacing:-1px;color:var(--dark);margin-bottom:12px;}
h2{font-family:'Fraunces',serif;font-weight:600;font-size:26px;color:var(--dark);margin:36px 0 12px;}
p{margin-bottom:16px;}
.lede{font-size:19px;color:var(--dark);}
.range{display:inline-block;background:var(--sage-dim);color:var(--sage-text);font-weight:700;font-family:'Fraunces',serif;font-size:22px;padding:6px 16px;border-radius:12px;margin:6px 0 8px;}
table{width:100%;border-collapse:collapse;margin:8px 0 4px;}
th,td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--border);}
th{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:var(--warm-gray);}
td.val{font-weight:700;color:var(--sage-text);text-align:right;}
ul{margin:0 0 16px 20px;}li{margin-bottom:6px;}
.cta{background:var(--dark);border-radius:20px;padding:28px;margin:36px 0;text-align:center;}
.cta h3{font-family:'Fraunces',serif;color:#fff;font-size:24px;font-weight:600;margin-bottom:8px;}
.cta p{color:#C0B6AB;margin-bottom:18px;}
.cta a{display:inline-block;background:var(--terracotta);color:#fff;text-decoration:none;font-weight:700;padding:13px 26px;border-radius:12px;}
.related{background:var(--card);border-radius:16px;padding:22px 24px;margin:28px 0;}
.related h2{font-size:20px;margin:0 0 10px;}
.related a{display:block;padding:6px 0;}
details{border-bottom:1px solid var(--border);padding:14px 0;}
summary{font-weight:600;cursor:pointer;color:var(--dark);}
details p{margin:10px 0 0;color:var(--text-muted);}
footer.site{border-top:1px solid var(--border);margin-top:40px;padding:28px 0;font-size:14px;color:var(--warm-gray);}
footer.site a{color:var(--warm-gray);margin-right:16px;}
.disclaimer{font-size:13px;color:var(--warm-gray);margin-top:24px;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:20px 0;}
.grid a{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 18px;text-decoration:none;color:var(--dark);font-weight:600;}
.grid a span{display:block;font-size:13px;color:var(--sage-text);font-weight:700;margin-top:4px;}
@media(max-width:600px){h1{font-size:32px;}.grid{grid-template-columns:1fr;}}
"""

def header():
    return (f'<header class="site"><div class="wrap">'
            f'<a class="logo" href="/">SnapWorth</a>'
            f'<a class="nav-cta" href="{APP_STORE}">Get the app</a>'
            f'</div></header>')

def footer():
    return ('<footer class="site"><div class="wrap">'
            '<a href="/">Home</a><a href="/worth">Resale Values</a>'
            '<a href="/support">Support</a>'
            '<a href="https://api.snapworth.eu/privacy">Privacy</a>'
            '<div style="margin-top:12px">© ' + str(YEAR) + ' SnapWorth</div>'
            '</div></footer>')

def cta(name):
    return (f'<div class="cta"><h3>Know your exact value in seconds</h3>'
            f'<p>Typical ranges only go so far. Snap a photo of your {html.escape(name)} and SnapWorth\'s AI checks real sold listings for a value tuned to your exact item and condition.</p>'
            f'<a href="{APP_STORE}">Download SnapWorth — free</a></div>')

def page_html(item, related):
    name = item["name"]; e = html.escape
    title = (f"How Much Are {base(name)} Worth to Resell? ({YEAR} Resale Value)" if is_plural(item)
             else f"How Much Is a {base(name)} Worth to Resell? ({YEAR} Resale Value)")
    desc = f"{name} resale value is typically ${item['low']}–${item['high']} depending on condition. See what affects the price, where to sell, and how to check your exact item."
    url = f"{SITE}/worth/{item['slug']}"

    rows = "".join(f"<tr><td>{e(c)}</td><td class='val'>{e(v)}</td></tr>" for c, v in item["conditions"])
    factors = "".join(f"<li>{e(f)}</li>" for f in item["factors"])
    plats = "".join(f"<li>{e(p)}</li>" for p in item["platforms"])
    faqs_html = "".join(
        f"<details><summary>{e(q)}</summary><p>{e(a)}</p></details>" for q, a in item["faqs"])
    related_html = "".join(
        f'<a href="/worth/{r["slug"]}">{e(r["name"])} <span>${r["low"]}–${r["high"]}</span></a>'
        for r in related)

    faq_ld = {
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in ([(question(item), answer(item))] + item["faqs"])
        ]
    }
    import json
    faq_json = json.dumps(faq_ld, ensure_ascii=False)

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article"><meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}"><meta property="og:url" content="{url}">
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<style>{STYLE}</style>
<script type="application/ld+json">{faq_json}</script>
</head><body>
{header()}
<main><div class="wrap">
<div class="crumbs"><a href="/">Home</a> › <a href="/worth">Resale Values</a> › {e(name)}</div>
<h1>{e(question(item))}</h1>
<p class="lede">{e(item['intro'])}</p>
<div class="range">Typical resale value: ${item['low']}–${item['high']}</div>

<h2>{e(name)} resale value by condition</h2>
<table><thead><tr><th>Condition</th><th style="text-align:right">Typical resale</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="disclaimer">Ranges reflect typical US secondhand prices and are a guide, not an appraisal. Actual value varies by demand, timing, and platform fees.</p>

<h2>What affects the resale value of {e(obj(item))}</h2>
<ul>{factors}</ul>

<h2>Best places to sell {e(obj(item))}</h2>
<ul>{plats}</ul>

{cta(base(name))}

<h2>Frequently asked questions</h2>
{faqs_html}

<div class="related"><h2>Check other items</h2>{related_html}
<a href="/worth" style="color:var(--terra-text);font-weight:700;margin-top:8px">See all resale values →</a></div>
</div></main>
{footer()}
</body></html>"""

def hub_html():
    e = html.escape
    cats = {}
    for it in ITEMS:
        cats.setdefault(it["cat"], []).append(it)
    blocks = ""
    for cat in sorted(cats):
        cards = "".join(
            f'<a href="/worth/{it["slug"]}">{e(it["name"])}<span>${it["low"]}–${it["high"]}</span></a>'
            for it in cats[cat])
        blocks += f"<h2>{e(cat)}</h2><div class='grid'>{cards}</div>"
    title = f"Resale Value Guides — What Your Thrift Finds Are Worth ({YEAR})"
    desc = "Free resale value guides for popular secondhand items — clothing, shoes, bags, and home goods. See typical prices, what affects value, and where to sell."
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<link rel="canonical" href="{SITE}/worth">
<link rel="icon" href="/favicon-32.png" sizes="32x32">
<style>{STYLE}</style></head><body>
{header()}
<main><div class="wrap">
<div class="crumbs"><a href="/">Home</a> › Resale Values</div>
<h1>What are your thrift finds worth?</h1>
<p class="lede">Typical secondhand resale values for popular items, plus what drives the price and where to sell. Want an exact number for your item? Snap a photo with SnapWorth.</p>
{blocks}
{cta('thrift find')}
</div></main>
{footer()}
</body></html>"""

def build():
    # individual pages
    for i, item in enumerate(ITEMS):
        related = [ITEMS[(i + k) % len(ITEMS)] for k in (1, 2, 3, 4)]
        (OUT / f"{item['slug']}.html").write_text(page_html(item, related), encoding="utf-8")
    # hub
    (OUT / "index.html").write_text(hub_html(), encoding="utf-8")
    # sitemap
    urls = [f"{SITE}/", f"{SITE}/worth", f"{SITE}/support"] + [f"{SITE}/worth/{it['slug']}" for it in ITEMS]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sm.append(f"  <url><loc>{u}</loc><lastmod>{TODAY}</lastmod></url>")
    sm.append("</urlset>")
    (ROOT / "sitemap.xml").write_text("\n".join(sm) + "\n", encoding="utf-8")
    # robots
    (ROOT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n", encoding="utf-8")
    print(f"Built {len(ITEMS)} pages + hub + sitemap ({len(urls)} urls) + robots.txt -> {OUT}")

if __name__ == "__main__":
    build()
