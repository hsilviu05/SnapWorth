# App Store Listing — 简体中文 (storefront `zh-Hans`, mainland China)

Simplified Chinese metadata, shipping alongside the Chinese app and the Xianyu
marketplace support that made it worth doing.

## Why this one is different

China was 12 of 98 installs — the second-largest cohort after the US, and the
only one that is unambiguously organic: no Chinese ASO, no Chinese listing, no
Chinese interface, and nobody in the developer's circle is there. It was the
strongest per-effort signal in the data and it was still held back a release,
because every fee table and listing generator in the app pointed at eBay,
Poshmark, Mercari, Depop, Vinted, OLX and Facebook. A Chinese reseller sells on
闲鱼. A translated app that tells them to list on Poshmark is worse than no
translation at all.

闲鱼 is in the app now, with its own fee entry and a backend prompt that writes
the listing in Chinese. That is what this page is allowed to promise.

## Read this before pasting

**Adding a language is version-scoped.** It ships with the version that carries
the Chinese app and the Xianyu support. All three go together.

**The backend must accept `xianyu` before that build reaches anyone.** It
deploys on merge to `main` and the app goes through review, so the order is
automatic here — but if the app were ever to ship first, every Snap → Sell tap
on 闲鱼 would come back a 400.

**Prices are in US dollars.** The valuation is in USD whatever the storefront,
and the generated 闲鱼 listing carries a dollar figure. This is the weakest part
of the Chinese launch and the page says so rather than hiding it. Fixing it
needs an FX rate the backend does not have; a made-up yuan number would be
worse.

**Mainland distribution has its own requirements** (ICP filing) that are
unaffected by adding a localization. The app is already distributed there, so
this changes nothing — but it is not something a translation can grant.

---

## App name (30 chars max)
SnapWorth 二手估价

Chinese counts as one character per character, so 30 is generous here. The
brand stays Latin — it is what the icon says and what the 12 existing users
already have installed.

## Subtitle (30 chars max)
拍一张，秒知二手转卖价

`二手` and `转卖` are the two terms this market searches with; `拍一张` says what
you do in three characters.

**Alternatives:**

* `闲鱼卖家的估价助手` — names the platform, narrower and much more specific.
* `淘二手前先看值多少` — the shopper's moment rather than the seller's.

---

## Promotional Text (170 chars — update anytime without resubmitting)

**Primary — pain-led.**

那件 30 块的外套，可能值 600。看到二手好物先拍一张，几秒就知道转手能卖多少，再决定掏不掏钱。每天一次免费扫描。

**Alternative — offer-led.**

每天一次免费扫描。把相机对准任何二手物品，立刻知道它值多少。不用注册，不用绑卡。

**Alternative — 闲鱼-led.**

准备在闲鱼出二手？先拍一张，看清转卖价和扣掉手续费后到底剩多少，描述也帮你写好。每天一次免费扫描。

---

## Description

在二手店、市集或者闲鱼上看到一件东西，拿在手里不知道到底值不值？SnapWorth 立刻告诉你。

把相机对准任何二手物品——一件外套、一双球鞋、一台老相机、一个名牌包——AI 会识别它，几秒内给出一个转卖价区间。

不用再猜，也不用再把能赚钱的东西留在货架上。

--------------------------

怎么用

1. 把相机对准任何二手物品
2. 按下快门，或从相册里选一张照片
3. 立刻拿到转卖价估算
4. 复制写好的商品描述，今天就能发出去

--------------------------

你会得到什么

• 立刻出转卖价——一个从低到高的估算区间
• 置信度——AI 识别得有多清楚，一目了然
• AI 写好的商品描述——每次扫描都附带标题和正文
• 扫描历史——每件好物自动保存，连同它的价值
• 总值——扫过的全部东西加起来值多少，一眼看清

--------------------------

适合谁

• 逛二手店、市集、旧货摊，想转手赚点差价的人
• 在闲鱼、eBay、Vinted 等平台出二手的人
• 常跑跳蚤市场和旧物清仓的人
• 任何一个拿起东西想过“这值得买吗”的人

--------------------------

免费与 Pro

SnapWorth 免费使用，不用注册。每天一次免费扫描，长期有效。每件好物都带着它的价值存在你自己的手机里，历史记录是你的，付不付费都一样。

Pro 增加：
• 无限扫描
• 按你选的平台重写商品描述——闲鱼、eBay、Vinted、Poshmark、Mercari、Depop 或 Facebook Marketplace。闲鱼的描述用中文写。
• “为什么是这个价”——估价背后的完整说明，包括四个价格档位和影响因素
• 读洗标——拍一下护理标签，估价更准
• 收藏总值、走势，以及二手市场的风向
• 利润账本——入手价、售出价，以及扣掉手续费后实际到手多少，可导出 CSV

• 可按月或按年订阅，年费包含 3 天免费试用。价格在 App 的订阅页面上显示。

随时可以在 iPhone 的设置里取消。

--------------------------

隐私

照片实时处理，不会存在我们的服务器上。扫描历史留在你自己的手机里。我们不卖你的数据，永远不会。

--------------------------

估价以美元显示。

法律条款

隐私政策：https://api.snapworth.eu/privacy
服务条款：https://api.snapworth.eu/terms

snapworth.eu

---

## Keywords (100 chars max)
闲鱼,二手,估价,转卖,中古,古着,球鞋,名牌,包包,潮牌,闲置,旧货,捡漏,回收,奢侈品,值多少,卖闲置,二手交易,拍照识物

63 characters. Chinese keywords are short, so this field goes much
further than in any Latin language — nineteen terms fit where the Spanish page
managed twelve. Commas are ASCII: App Store Connect splits on those, not on
`、`.

Not repeated from the name and subtitle, which are indexed from there: 二手,
估价, 转卖 appear once each above and are kept here only where they form a
different compound (出二手, 二手交易).

闲鱼 is here because the app genuinely supports it now, with a fee entry and a
Chinese listing generator. It would have been the single highest-value keyword
on this page and it would have been a lie a week ago.

Deliberately absent: 转转 and 得物, both large Chinese resale platforms the app
has no fee table for. Also absent: 鉴定 (authentication), which is searched
heavily around 名牌 and 球鞋 — the app reads a photo and says so plainly
（“仅凭这张照片判断，不是鉴定结论”）, so claiming it in a keyword would contradict
the app's own screen. Same rule as Wallapop on the Spanish page and
Kleinanzeigen on the German one — a keyword is a claim.

---

## What's New — 简体中文

```
SnapWorth 现在支持中文，也支持闲鱼了。

整个 App 都是中文：相机、结果页、利润账本和小组件。

闲鱼：按闲鱼的风格用中文写好标题和描述，手续费也按闲鱼算——个人卖家零佣金。

还有小组件：锁定屏幕上的收获总值，主屏幕上的最近好物和剩余扫描次数，Pro 用户还能看到本月利润。

进店时开一趟淘货行程，这趟的合计会一直留在锁定屏幕和灵动岛上。
```

---

## Category, age rating, URLs

Unchanged from the English listing — these are not per-locale fields.

## Screenshots

None uploaded for `zh-Hans`, so Apple falls back to the English set. This is a
worse fallback here than anywhere else on the account: a Chinese buyer looking
at five English screenshots has no reason to believe the app is in Chinese, and
the listing's main claim is that it is. If one locale gets its own screenshots,
this is the one.
