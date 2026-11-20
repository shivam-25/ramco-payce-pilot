# Deck

Fifteen HTML slides in `slides/`, converted to `ramco-payce-pilot.pptx` by `build.js`.

## Building

`build.js` needs two things that are not in the repository:

```sh
cd docs/deck
npm install pptxgenjs playwright sharp
npx playwright install chromium
cp ~/.claude/skills/doc-pptx/scripts/html2pptx.js .   # vendored, not ours
cp ../screenshots/0*.jpg slides/                      # slides reference them by bare name
node build.js
```

The screenshots live in `docs/screenshots/` and are copied in rather than referenced
up a directory, because html2pptx resolves image paths relative to the slide file.

To check layout without PowerPoint, screenshot the slides directly — the HTML is the
source of truth for position, and `build.js` fails loudly on vertical overflow anyway.
