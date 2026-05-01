# Cover-renderer fonts

Drop these three OTF files into this directory before running cover_renderer:

| File | Source | Purpose |
|---|---|---|
| `SourceHanSansTC-Bold.otf` | <https://github.com/adobe-fonts/source-han-sans/tree/release/OTF/TraditionalChinese> | title + topic chip |
| `SourceHanSansTC-Regular.otf` | same repo, same dir | brand bar |
| `SourceHanSerifTC-Light.otf` | <https://github.com/adobe-fonts/source-han-serif/tree/release/OTF/TraditionalChinese> | subtitle |

Adobe Source Han is OFL-licensed (commercially permissive). The files are
~10 MB each — leave them out of git commits, the renderer reads them at
runtime.

If the files are missing, ``cover_renderer`` falls back to PIL's default
font (Latin-only). CJK characters render as placeholder boxes — that's
intentional, makes the missing-font situation visually obvious in dev.

Clean-room download (one-time setup):

```bash
cd assets/fonts
curl -L -o SourceHanSansTC-Bold.otf \
  https://github.com/adobe-fonts/source-han-sans/raw/release/OTF/TraditionalChinese/SourceHanSansTC-Bold.otf
curl -L -o SourceHanSansTC-Regular.otf \
  https://github.com/adobe-fonts/source-han-sans/raw/release/OTF/TraditionalChinese/SourceHanSansTC-Regular.otf
curl -L -o SourceHanSerifTC-Light.otf \
  https://github.com/adobe-fonts/source-han-serif/raw/release/OTF/TraditionalChinese/SourceHanSerifTC-Light.otf
```
