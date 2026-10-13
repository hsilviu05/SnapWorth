# Rack scan test set (#89)

Twenty photos of racks, rails, shelves and bins, run through the shipping
rack prompt and reviewed by a person. The bar from the issue: at least 15 of
the 20 return at least one sensible candidate, and **none** returns a
candidate that is not in the photo.

The photos are not in git. They are taken in shops, so they can show other
shoppers and staff, and this repository is public. Put them in `images/`
here, which `.gitignore` excludes, the way the gold set's photos are kept
out (`backend/eval/data/images/`).

## Taking them

- A phone camera, the way the app takes them: portrait, from where a shopper
  stands. Not edited, not cropped.
- A mix: clothing rails, shoe shelves, a bin of bags, a shelf of
  electronics or homewares, a dim back room, one blurry shot, one rack with
  nothing worth picking up. The last two should come back empty, and an empty
  answer counts as passing the "not in the photo" half.
- No faces you can recognise. Crop or retake rather than blur.

## Running and reviewing

```sh
cd backend
python -m eval.rack run --photos eval/data/rack/images --out eval/data/rack/runs/$(date +%F)
```

That needs `GEMINI_API_KEY` and makes one model call per photo. It writes
`results.json` and one copy of each photo with the pins drawn on it,
numbered as the app numbers them. `runs/` is ignored too.

Look at each marked-up copy, then add a line per photo to `review.jsonl`
here:

```json
{"photo": "rail-01.jpg", "sensible": 2, "not_in_photo": 0}
```

- `sensible`: pins on a real item that is plausibly worth a closer look.
- `not_in_photo`: pins on nothing, on the wrong item for the name it was
  given, or on an item that is not there at all. One of these fails the
  set.

Then score it:

```sh
python -m eval.rack score eval/data/rack/review.jsonl
```

It exits 0 when the bar is met. `review.jsonl` holds no photo and no
location, so it can be committed with the prompt version it was run on.
