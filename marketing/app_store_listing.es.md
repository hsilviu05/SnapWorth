# App Store Listing — Español (Spanish (Spain) and Spanish (Mexico))

Spanish metadata for the App Store, pasted onto **1.5.1**. The app has been
in Spanish since 1.4.2; its store page never has been (checked 2026-09-26).

**One text, two localizations.** App Store Connect has two Spanish
localizations and they reach different storefronts: Spanish (Spain) reaches
Spain only; Spanish (Mexico) is the default language of Mexico and fifteen
other Latin American storefronts, and a supported one in Belize, Uruguay and
the US. This file is pasted into both — the reasons, with Apple's sources,
are under *Spanish (Spain) and Spanish (Mexico)* below. That is the most
search surface per unit of work anywhere in this account.

The copy is Peninsular but avoids what reads oddly across the Atlantic: *tú*
rather than *vos*, no *vosotros*, and no regionalism where a neutral word
exists. Where one had to be chosen — *móvil* over *celular*, *ordenador*
avoided entirely — Spain won; *móvil* is understood in Latin America. What
reads as vulgar or as something else in Latin America is out, even where
Spain would not notice (see *Three words were changed* below).

Mirrors `app_store_listing.md`, including its accuracy rules: no claim of sold
listings, comps or market data; the one-scan-a-day limit in every plan mention;
Thrift Flip in the free list rather than the Pro one, because it is free in
full (#128). The full list of claims it does not make is in the English file,
under *Claims this listing does not make*.

## Read this before pasting

**Not live yet (checked 2026-09-26).** The live 1.5.0 carries no Spanish
metadata: the `es` storefront shows the English page. Add **Spanish (Spain)
and Spanish (Mexico)** to the 1.5.1 version page and paste everything below
into each — see *Paste with 1.5.1* in `app_store_listing.md`. The one field
that differs is the promotional text (below).

**Adding a language is version-scoped.** Name, subtitle, keywords, description
and What's New belong to a *version*, so Spanish can only be added to a
version you can still edit: 1.5.1, before it is submitted. Once it is in
review, the next chance is 1.5.2.

**Feature names match the Spanish app.** The description uses the words the
screens carry: a find is a *hallazgo*, Haul mode is the *modo Lote*, and «Por
qué este precio» is *Why this price* (App.json has translated it since the app
went Spanish, 52d2e74). "Thrift Flip" and "Snap → Sell" are brand names,
English in the app, and English here.

**Prices.** No figure appears. Each storefront's price is Apple's own tier in
its own currency, not a conversion of $4.99, and this one text is shown in
Spain, Latin America and the US. A euro figure would be wrong in Mexico, and a
peso figure wrong in Spain.

**Values are in dollars.** The valuation is in USD whatever the phone's region,
so a Spanish user sees "$45–$90". That is what the estimate is. It is also the
first thing a reviewer will ask about — and in Mexico, Argentina, Chile and
Colombia "$" is also the peso sign, so the description's line "Los valores se
muestran en dólares." carries more weight there. Keep it in both.

## Spanish (Spain) and Spanish (Mexico) — decided 2026-09-27

**Paste this file into both Spanish (Spain) and Spanish (Mexico).**

Apple's table of App Store localizations (read 2026-09-27) lists, per
storefront, a default language and any additional ones:

* **Spanish (Spain)** appears for one storefront only: Spain, as its default
  (with Catalan and English (U.K.) as additional languages).
* **Spanish (Mexico)** is the default language of Mexico, Argentina, Bolivia,
  Chile, Colombia, Costa Rica, the Dominican Republic, Ecuador, El Salvador,
  Guatemala, Honduras, Nicaragua, Panama, Paraguay, Peru and Venezuela —
  sixteen storefronts — and an additional language in Belize, Uruguay and the
  United States.

What a Latin American storefront shows when only Spanish (Spain) exists is not
a rule Apple writes down. *Localize app information*, using French as its
example, says a localization "displays to users whose language setting is in
French", and "also displays to users in countries or regions where the App
Store supports French but not English" — which no Latin American storefront
is, since every one of them lists English (U.K.). Then: "If no localization
matches a user's language setting, the next most relevant localization is
used. In other countries or regions, your metadata displays in the primary
language" — English, here. On search it is exact: "Users can search for your
app using localized keywords in all countries or regions where the App Store
supports French." So with Spanish (Spain) alone:

* the Spanish name, subtitle and keywords are searchable in Spain and in no
  Latin American storefront, nor on the US one; and
* whether a Spanish-speaking visitor in Mexico or Argentina gets the Spanish
  page or the English one is left to "the next most relevant localization",
  which Apple does not define.

Adding Spanish (Mexico) with the same text gives the sixteen storefronts whose
default it is — and Spanish-language devices in Belize, Uruguay and the US —
a localization that matches, rather than leaving them to "the next most
relevant". By the search sentence it also makes the Spanish name, subtitle
and keywords searchable on all nineteen, the US included. It costs one more
locale to paste and nothing else. Once 1.5.1 is live, the `mx` lookup
(`app_store_listing.md`, *Paste with 1.5.1*) confirms it.

Sources (both read 2026-09-27; the storefront table was parsed from the page's
own HTML):
[App Store localizations](https://developer.apple.com/help/app-store-connect/reference/app-store-localizations/) ·
[Localize app information](https://developer.apple.com/help/app-store-connect/manage-app-information/localize-app-information)

**What differs in Spanish (Mexico): the promotional text only.** Paste the
*offer-led* alternative there, not the primary: the primary names a price in
euros ("4 €"), and says *chaqueta* (below), which is wrong on every storefront
Spanish (Mexico) reaches. Spain keeps the primary.

**Three words were changed so that one text reads right in both.** Each is
neutral in Spain.

* «¿Alguna vez has *cogido* algo en un mercadillo…?» now says *encontrado*:
  *coger* is vulgar in Mexico and Argentina.
* «una *chaqueta*» in the description's second paragraph now says *un
  abrigo*: in Mexico *chaqueta* is slang for masturbation, and a jacket there
  is a *chamarra*.
* «Para quien va a *rastros*…» now says *mercados de pulgas*: in Mexico a
  *rastro* is a slaughterhouse. *Ventas de garaje* joins it, the English
  line's yard sales, which reads naturally in Latin America.

*Mercadillo*, *zapatillas* (heeled shoes, in Mexico) and *vaciados de casas*
stay: Spain's words, understood or harmless elsewhere. A Mexican reader may
still want them changed; that is the native-reader check below.

**TODO(owner), not for 1.5.1:** a keyword line of Spanish (Mexico)'s own.
The Spain line carries Spain-only words (`chollo`, `mercadillo`) where Mexico
searches others; which ones is a native reader's call, and a keyword is a
claim. For 1.5.1 the same line goes into both.

## Native reader

**TODO(owner):** record who read these once before pasting, or "no native
reader". Spanish (Mexico) wants a second reader from Mexico or elsewhere in
Latin America, for the description as a whole: it is Peninsular copy, and the
words changed below are the ones found, not a guarantee there are no others.

New or changed for 1.5.1:

* The Pro list's Haul line (*Modo Lote — …*) and portfolio line (*Historial
  del valor de tu colección y las tendencias*). Both are the paywall's own
  words from `App.json`, so they were translated with the app; read them
  here as listing copy.
* «Por qué este precio» in the Pro list, where the listing had the English
  «Why this price».
* The trial line: *la anual incluye una prueba gratis, y verás cuánto dura
  antes de suscribirte*.
* *encontrado*, *un abrigo*, and *mercados de pulgas, ventas de garaje* in
  the description (see *Three words were changed* above).

The confidence, Thrift Flip and widgets lines from #196 have not been read
either (`app_store_listing.md`, end).

---

## App name (30 chars max)
SnapWorth: Precio Reventa

25 characters. Indexes `precio` and `reventa`, the two words a
Spanish-speaking reseller types, and keeps the brand in front.

## Subtitle (30 chars max)
Escanea y revende segunda mano

30 characters, the limit exactly. `segunda mano` is the phrase this market
searches with, and it is two words that no single term replaces. `escanea` and
`revende` are the verbs.

**Alternatives**, if the search-terms report argues otherwise:

* `Cuánto vale tu segunda mano` (27) — the question a shopper asks, indexes `vale`.
* `Escáner de ropa de segunda` (26) — narrower, clothes-first, indexes `ropa`.

---

## Promotional Text (170 chars — update anytime without resubmitting)

**Primary — pain-led (163 chars).** Spanish (Spain) only: see *What differs in
Spanish (Mexico)* above.

Esa chaqueta de 4 € puede valer 90. Escanea cualquier cosa de segunda mano y descubre en segundos cuánto vale en reventa, antes de pagar. Un escaneo gratis al día.

**Alternative — offer-led (143 chars).** The one to paste into Spanish
(Mexico).

Un escaneo gratis cada día. Apunta con la cámara a cualquier cosa de segunda mano y descubre al instante cuánto vale. Sin cuenta y sin tarjeta.

**Alternative — short (119 chars).**

Descubre cuánto vale antes de comprarlo. Una foto, una estimación, unos segundos. Un escaneo gratis al día, sin cuenta.

---

## Description (4000 chars max; 3,336 with line breaks)

¿Alguna vez has encontrado algo en un mercadillo y te has preguntado si vale algo? SnapWorth te lo dice al instante.

Apunta con la cámara a cualquier cosa de segunda mano — un abrigo, unas zapatillas, una cámara vintage, un bolso de firma — y la IA la reconoce y estima en segundos un intervalo de precio de reventa.

Se acabó adivinar. Se acabó dejar en la estantería cosas que dan dinero.

--------------------------

CÓMO FUNCIONA

1. Apunta con la cámara a cualquier cosa de segunda mano
2. Pulsa el disparador, o elige una foto de tu galería
3. Recibes al instante una estimación de precio de reventa
4. Copias el anuncio ya escrito y lo publicas hoy mismo

--------------------------

QUÉ INCLUYE

• Precio de reventa al instante — un intervalo estimado, de mínimo a máximo
• Nivel de confianza — cuánto respaldan la foto y el reconocimiento la estimación
• Anuncio escrito por la IA — título y descripción listos para publicar, en cada escaneo
• Thrift Flip — escanea el artículo, añade el precio de la tienda y mira cuánto ganarías tras las comisiones de la plataforma, antes de comprarlo
• Historial — cada hallazgo se guarda solo, con su valor
• Total — cuánto vale todo lo que has escaneado, de un vistazo
• Widgets — el total de tu colección en la pantalla bloqueada; tus últimos hallazgos, los escaneos que te quedan y el escaneo con un toque en la pantalla de inicio
• Stickers para iMessage — 20 stickers de Tag, nuestra mascota (cuatro animados), para presumir de tus hallazgos

--------------------------

PARA QUIÉN

• Para quien recorre mercadillos y tiendas de segunda mano buscando reventa
• Para quien vende en Vinted, eBay, Poshmark, Mercari, Depop y Facebook Marketplace
• Para quien va a mercados de pulgas, ventas de garaje, subastas y vaciados de casas
• Para cualquiera que se haya preguntado alguna vez «¿merece la pena?»

--------------------------

GRATIS Y PRO

SnapWorth se usa gratis y sin cuenta. Tienes un escaneo gratis cada día, para siempre. Cada hallazgo se guarda en tu móvil con su valor, y el historial es tuyo pagues o no.

Pro añade:
• Escaneos ilimitados (sujetos a un uso razonable)
• Modo Lote — fotografía artículo tras artículo mientras se valora cada uno, con el total a la vista
• Anuncios reescritos para la plataforma que elijas — Vinted, eBay, Poshmark, Mercari, Depop o Facebook Marketplace
• «Por qué este precio» — la explicación completa detrás de la estimación, con los niveles de precio y qué ha pesado
• Lectura de la etiqueta — fotografía la etiqueta de cuidado para afinar la estimación
• Historial del valor de tu colección y las tendencias
• Registro de ganancias — lo que pagaste, por cuánto vendiste y lo que te quedó tras comisiones, con exportación a CSV

• Suscripción mensual o anual; la anual incluye una prueba gratis, y verás cuánto dura antes de suscribirte. El precio aparece en la app, en la página de suscripción.

Puedes cancelar cuando quieras desde los ajustes del iPhone.

--------------------------

PRIVACIDAD

Las fotos se procesan en tiempo real y no se guardan en nuestros servidores. El historial se queda en tu móvil. No vendemos tus datos. Nunca.

--------------------------

Los valores se muestran en dólares.

LEGAL

Política de privacidad: https://api.snapworth.eu/privacy
Términos del servicio: https://api.snapworth.eu/terms

snapworth.eu

---

## Keywords (100 chars max)
vinted,ebay,ropa,vintage,tasar,valor,ganancia,mercadillo,usado,chollo,revender,segundamano

90 characters. Words already in the name or subtitle (precio, reventa,
escanea, revende, segunda, mano) are indexed from there and are not repeated.
`segundamano` as one word is a real search and a different token from the
two-word phrase in the subtitle.

Vinted and eBay are here because they are the two Spanish-market platforms the
app actually supports, with real fee tables. **Wallapop and Milanuncios are
deliberately absent** — they dominate Spanish resale and it is tempting, but
the app has no fee table for either, and a keyword is a claim.

---

## What's New (Version 1.5.0) — Español

```
Novedad: stickers de Tag, nuestra mascota, para iMessage. Son 20 y cuatro están animados; los encontrarás con tus stickers, en el teclado de emojis.
```

## What's New (Version 1.4.3) — Español — ARCHIVED, do not paste

1.4.3 is approved and live; kept for the record.

```
Te presentamos a Tag.

Tag, nuestra nueva mascota, te acompaña mientras SnapWorth analiza tu artículo, y te espera en Mis hallazgos y Mis reventas hasta que guardes algo.

Thrift Flip ahora lo enseña todo gratis: la ganancia neta, el ROI y el desglose completo de comisiones de la plataforma que elijas. Guarda una reventa en Mis reventas directamente desde el veredicto, con Pro o sin él.

Pro: «Limpiar la foto», en Snap → Sell, separa tu artículo del fondo de la tienda en tu propio móvil y lo coloca sobre blanco o gris claro con el formato de cada plataforma: cuadrado para casi todas y 4:5 para Depop.
```

## What's New (Version 1.4.2) — Español — ARCHIVED, do not paste

1.4.2 is approved and live; kept for the record.

```
SnapWorth ya está en español.

Toda la app: la cámara, los resultados, el registro de ganancias y los widgets.

Y widgets: el total de tu colección en la pantalla bloqueada, tus últimos hallazgos y los escaneos que te quedan en la pantalla de inicio, y la ganancia del mes si tienes Pro.

Empieza una ruta al entrar en una tienda y el total de esa ruta se queda en la pantalla bloqueada y en la Dynamic Island mientras escaneas.
```

---

## Category, age rating, URLs

Unchanged from the English listing — these are not per-locale fields.

## Screenshots

None uploaded for `es`, so Apple falls back to the English set. Worth revisiting
once the Spanish app has shipped: screenshots of an app in a language the
viewer does not read are the single biggest conversion leak on a localized page.
