# App Store Listing — Română (storefront `ro`)

Romanian **metadata** for the App Store. Nothing here touches the binary: the
app has no `.lproj` anywhere and its interface stays English. This is the
localization that costs nothing to ship and adds a second search surface —
name, subtitle and keywords are indexed per locale, so a Romanian listing is a
fresh 100-character keyword field, not a translation of the old one.

Mirrors `app_store_listing.md`, including its accuracy rules: no claim of sold
listings, comps or market data; the one-scan-a-day limit is stated wherever a
plan is; the Thrift Flip line keeps the free buy-or-skip verdict separate from
the Pro numbers behind it.

## Read this before pasting

**Adding a language is version-scoped.** Name, subtitle, keywords, description
and What's New belong to a *version*, so Romanian can only be added to a
version you can still edit. 1.4.1 is in review — either pull it from review,
add `ro`, and resubmit, or hold this for 1.4.2. **Promotional text is the
exception**: it can be changed on a live version at any time, but only for
locales the version already has, so it cannot be used to sneak Romanian in
early.

**Screenshots are optional per locale.** With none uploaded for `ro`, Apple
falls back to the English set. Leave them — English screenshots of an English
app are honest, and the v2 set is still 3 of 8 built.

**Feature names match the Romanian app.** The interface is Romanian as of the
version this listing ships with, so the description uses the names the buttons
actually carry: *Găselnițe*, *Flipuri*, *Scanări rămase*. The two that stayed
English in the app — "Thrift Flip" and "Why this price" — stay English here for
the same reason, which is that a reader looking for them will be looking for
those words.

**Prices.** The text does not name a figure. Romanian tiers are Apple's RON
tiers, not a conversion of $4.99 and $39.99, and this environment cannot read
App Store Connect. If you want the numbers in the text, take them from the
Romania row of each subscription's price schedule and substitute them into the
`GRATUIT ȘI PRO` block — don't convert the dollar prices.

---

## App name (30 chars max)
SnapWorth: Preț Revânzare

25 characters. Indexes `preț` and `revânzare` — the two terms a Romanian
reseller actually types — and keeps the brand in front, so the icon and the
name still match what English-storefront word of mouth points at.

## Subtitle (30 chars max)
Scanner second hand și flip

27 characters. `second hand` is the phrase this market searches with; it is
written as two words in Romanian usage and is worth more than any single word
here. `scanner` is the English spelling because that is what people type on a
phone keyboard — the correct Romanian `scaner` is carried in the keyword field
instead, so both spellings are covered.

**Alternatives**, if the search-terms report argues for a different angle:

* `Scanner second hand: cât face` (29) — colloquial, promises the answer
  rather than naming the category. Drops `flip`.
* `Cât valorează la second hand` (28) — the question a shopper asks, indexes
  `valorează`. Drops `scanner`.

---

## Promotional Text (170 chars — update anytime without resubmitting)

**Primary — pain-led (164 chars).** The Romanian reading of the English hook.

Geaca aia de 25 de lei poate face 350. Scanează orice găsești la second hand și afli în câteva secunde cât valorează la revânzare. O scanare gratuită în fiecare zi.

**Alternative — offer-led (131 chars).**

O scanare gratuită în fiecare zi. Îndreaptă camera spre orice lucru second hand și afli pe loc cât valorează. Fără cont, fără card.

**Alternative — short (130 chars).**

Află cât valorează înainte să-l cumperi. O poză, o estimare de preț, câteva secunde. O scanare gratuită în fiecare zi — fără cont.

---

## Description

Ai luat vreodată ceva dintr-un second hand și te-ai întrebat dacă valorează ceva? SnapWorth îți spune pe loc.

Îndreaptă camera spre orice lucru la mâna a doua — o geacă, o pereche de sneakers, un aparat foto vintage, o geantă de firmă — iar AI-ul îl recunoaște și estimează în câteva secunde un interval de preț la revânzare.

Gata cu ghicitul. Gata cu lăsat pe raft lucruri care fac bani.

--------------------------

CUM FUNCȚIONEAZĂ

1. Îndreaptă camera spre orice lucru second hand
2. Apasă pe declanșator — sau alege o poză din galerie
3. Primești pe loc o estimare de preț la revânzare
4. Copiază anunțul gata scris și pune-l la vânzare azi

--------------------------

CE PRIMEȘTI

• Preț de revânzare pe loc — un interval estimat, de la minim la maxim
• Scor de încredere — cât de clar a reușit AI-ul să identifice lucrul din poză
• Anunț scris de AI — titlu și descriere gata de postat, la fiecare scanare
• Istoricul scanărilor — fiecare găselniță salvată automat, cu valoarea ei
• Total — cât valorează tot ce ai scanat, dintr-o privire

--------------------------

PENTRU CINE

• Pentru cine umblă prin second hand și vrea să revândă cu profit
• Pentru vânzătorii de pe OLX, Vinted, eBay, Poshmark, Mercari, Depop și Facebook Marketplace
• Pentru cine merge la talciocuri, anticariate și licitații
• Pentru oricine s-a întrebat vreodată „merită să iau asta?”

--------------------------

GRATUIT ȘI PRO

SnapWorth se folosește gratuit, fără cont. Primești o scanare gratuită în fiecare zi, pentru totdeauna. Fiecare găselniță se salvează pe telefonul tău cu valoarea ei, iar istoricul rămâne al tău, fie că plătești sau nu.

Pro adaugă:
• Scanări nelimitate
• Anunțuri rescrise pentru platforma pe care o alegi — OLX, Vinted, eBay, Poshmark, Mercari, Depop sau Facebook Marketplace
• „Why this price” — explicația completă din spatele estimării, inclusiv scara de prețuri și ce a cântărit
• Citirea etichetei — fotografiază eticheta de întreținere pentru o estimare mai exactă
• Valoarea portofoliului, evoluția lui și trendurile din second hand
• Registrul de profit — cât ai dat, cu cât ai vândut și cât ți-a rămas după comisioane, cu export CSV
• Cifrele din „Thrift Flip” — verdictul „iei sau lași” e gratuit; Pro arată profitul net, ROI-ul și comisioanele din spatele lui

• Abonament lunar sau anual; cel anual include 3 zile gratuite. Prețul în lei apare în aplicație, pe pagina de abonament.

Poți anula oricând din setările iPhone-ului.

--------------------------

CONFIDENȚIALITATE

Pozele sunt procesate în timp real și nu sunt stocate pe serverele noastre. Istoricul scanărilor rămâne pe telefonul tău. Nu îți vindem datele. Niciodată.

--------------------------

LEGAL

Politica de confidențialitate: https://api.snapworth.eu/privacy
Termeni de utilizare: https://api.snapworth.eu/terms

snapworth.eu

---

## Keywords (100 chars max)
talcioc,haine,vintage,olx,vinted,okazii,chilipir,valoare,profit,evaluare,revinde,scaner,estimare

96 characters. Words already in the Romanian name or subtitle (preț, revânzare, scanner,
second, hand, flip) are indexed from there and are not repeated. `scaner` is
here as the correct-spelling twin of the subtitle's `scanner`; Apple normalises
diacritics in most locales, but the two spellings differ by a letter, not an
accent, so both are worth the characters. OLX and Vinted are the two Romanian
marketplaces this audience sells on and both are genuinely supported — same
rule the English list follows for Poshmark, Mercari and Depop.

Dropped for space, in the order I would add them back: `anticariat`,
`bazar`, `sneakers`, `licitatie`.

---

## What's New (Version 1.4.1) — Română

```
Corecții pentru widgeturi.

• „Scans left” arată acum „Unlimited” pentru abonații Pro, în loc de o cifră care nu însemna nimic.

• „Recent finds” umple tot widgetul — mai multe găselnițe pe dimensiunea medie și pe cea mare.

• Stabilitate la salvarea scanărilor.
```

## What's New (Version 1.4.0) — Română

Here in case Romanian first ships attached to a version that still carries the
widgets — and as the translation of the entry the English listing already has.

```
Widgeturi, și încă multe.

Totalul găselnițelor tale pe Lock Screen — ca cerc, ca panou sau pe un singur rând, lângă ceas.

„Recent finds” pe Home Screen: ultimele lucruri scanate și cât valorează fiecare.

Câte scanări gratuite ți-au mai rămas azi, dintr-o privire.

Profitul lunii din ce ai vândut, pentru abonații Pro.

Și thrift runs. Pornești una când intri în magazin, iar totalul turei rămâne pe Lock Screen și în Dynamic Island cât timp scanezi — așa știi cât face haul-ul înainte să te hotărăști la următorul lucru.

Pe iPhone 15 Pro și mai nou poți pune Scan pe Action Button sau în Control Centre și intri direct în cameră.
```

---

## Category, age rating, URLs

Unchanged from the English listing — these are not per-locale fields.

## What is *not* localized

**Values are in dollars.** `NumberFormatter.snapCurrency` is pinned to
`en_US`/USD because the valuation is in dollars whatever the phone's region, so
a Romanian user sees "$45–$90". That is what the estimate is, not a formatting
bug — but it is the first thing a reviewer will ask about.

**The privacy policy and the terms** are the English documents served at
api.snapworth.eu. The links in the listing point at them.

**The listing text the app generates** is written in English by the backend.
Snap → Sell produces an English title and description whatever the interface
language, which matters for a seller posting to OLX — see
`ios/Localization/README.md`.
