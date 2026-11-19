# CLAUDE.md

- Text files are LF (.gitattributes). Scripts write with `newline="\n"`; compare text
  ignoring `\r`.
- The engine depends on Pillow and ijson only, and is installed by the city repos from a
  release tag. The gap layers' shared plumbing lives in `../layer-kit`; a rule the two
  share (the publish push retry, the off-Windows WSL path) is repeated here on purpose,
  so the engine does not pull in `layerkit` and, through it, `addressvault`.
