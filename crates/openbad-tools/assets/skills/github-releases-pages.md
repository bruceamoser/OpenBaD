---
name: github-releases-pages
description: Cut releases, upload assets, and manage GitHub Pages with the github tool.
---

# Releases and Pages

## Releases

1. Confirm the version, tag, and target commit with the project manifest and
   `git_log`.
2. Create an annotated tag locally, then push it.
3. `github` `area: "release"`, `action: "create"` with `tag`, `title`, `notes`
   (or `generate_notes: true`), `draft`, `prerelease`, and `target`.
4. Attach assets with `action: "upload"` and workspace-relative `files`.
5. Verify with `action: "view"`; publish the draft only when the human asks.
6. Editing notes uses `action: "edit"`. Deleting a release or its tag requires
   `confirm: true`.

Never overwrite an existing release asset unless `clobber: true` and the human
approved replacing it.

## Pages

- `area: "pages"`, `action: "status"` shows the site configuration and URL.
- `action: "builds"` lists recent builds; `action: "build"` requests a new
  build after pushing to the Pages branch.
- `action: "unpublish"` takes the site down and requires `confirm: true`.
- If Pages is not enabled, explain the repository setting the human must
  change rather than guessing at a workaround.
