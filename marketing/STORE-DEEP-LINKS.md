# App Store deep links

What to paste into App Store Connect's **Deep link** field on a custom product
page or an in-app event. When someone who already has SnapWorth taps **Open**
on that page or event, iOS hands the app this URL and it goes straight to the
matching screen. Custom product page deep links need iOS 18 or later on the
viewer's phone; on older systems Open just launches the app.

Needs the build that adds the `pro`, `haul` and `rack` routes (1.9.0 or later).
Don't add one of those three to a page while the live build is older: on an
older build the link opens the app on whatever screen it was last on.

| Link | Opens | Fits |
|---|---|---|
| `snapworth://scan?src=cpp_<name>` | Camera, ready to scan | The default "value anything" page |
| `snapworth://pro?src=cpp_<name>` | Pro paywall (a subscriber just gets the camera) | Pro, free trial, Black Friday |
| `snapworth://haul?src=cpp_<name>` | Haul mode, or its paywall for free users | Resellers, car-boot and thrift hauls |
| `snapworth://rack?src=cpp_<name>` | Rack scan, or its paywall for free users | Charity shops, racks of clothes |
| `snapworth://guess?src=event_<name>` | Daily guess game | A Daily guess in-app event |
| `snapworth://flips?src=cpp_<name>` | My Flips ledger | Profit tracking page |
| `snapworth://history?src=cpp_<name>` | My Finds | |

For an in-app event, use `event_<name>` in place of `cpp_<name>`.

## Naming the `src`

`<name>` is for counting: the app sends `store_link_opened` with `route`,
`kind` (`cpp` or `event`) and `campaign` (the name). It is counted only if it
is 1–24 characters of lowercase letters, digits and hyphens, for example
`cpp_resellers` or `event_black-friday-2026`. Anything else still opens the
screen, but isn't counted. No underscores inside the name, and never put a
person's name or anything personal in it.

Use the same name you gave the page or event in App Store Connect, so the
opens in TelemetryDeck line up with ASC's impressions and downloads for it.

## Limitations

- A brand-new install goes through onboarding first, and the deep link is
  dropped. That's the same as the widget links today. ASC's own per-page
  download numbers cover new installs.
- Opens are counted only when analytics is on (Settings → Share anonymous analytics).
