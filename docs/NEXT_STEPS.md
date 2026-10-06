# Owner next steps

> **Steps 2–5 are done.** The closeout ran, and the private repository exists at
> <https://github.com/rinatrizvanov/seq2lead>. The remaining decisions are
> consolidated in [`OWNER_DECISIONS.md`](OWNER_DECISIONS.md), which supersedes
> steps 6, 7 and 9 below; this file is kept for the interface detail in steps 1,
> 3, 5 and 8.

1. Review `paper/Seq2Lead_scientific_manuscript.docx`, especially title/author details and interpretation. Keep it an unreviewed draft.
2. Download `seq2lead-closeout-private-ready-v2.zip` to `/Users/rinatrizvanov/Downloads`.
3. In Terminal, open the real project and launch Claude Code with Downloads access:

```bash
cd "/Users/rinatrizvanov/Desktop/Projects/Project 2 - Seq2Lead"
claude --add-dir "/Users/rinatrizvanov/Downloads"
```

4. Paste this instruction into Claude Code (ZIP files are local archives to inspect/extract, not text attachments):

```text
Use /Users/rinatrizvanov/Downloads/seq2lead-closeout-private-ready-v2.zip for the Seq2Lead closeout. Extract it into a temporary directory and read seq2lead/docs/CLAUDE_CLOSEOUT_PROMPT.md. Follow that prompt against this actual project, comparing before applying. Include the Word draft, Markdown, figures and figure source script. You are authorised to create and push a PRIVATE GitHub repository using my existing authenticated account; leave it private. Preserve scientific artifacts and my current work. Do not refit, rebuild features, dock, download datasets, make the repo public or delete ZIPs. Return the private URL, checks actually run, original-artifact digest comparison and exact proposed ZIP retention list.
```

5. If GitHub authentication is absent, run `gh auth login` in Terminal, then ask Claude to resume the repository step. If `gh` is absent, install the official GitHub CLI first. Use your own account and existing Git identity; do not create an organisation or replace a remote silently. Claude should create the reviewed commit before `gh repo create seq2lead --private --source . --remote origin --push`, then verify visibility and a disposable clone.
6. Review the returned private repository: README figure rendering, reproduction commands, demo prerequisites, references, attribution/licences and exclusion of secrets/bulk data. Saved-result verification must distinguish available from unavailable checks. Send the URL or updated review archive for a final review.
7. After your approval, release the repository publicly and add its actual URL to the resume and manuscript. Public release and manuscript posting are separate decisions. Do not list the manuscript as peer-reviewed or submitted.
8. Optional paper figures require more evidence: numerical evaluation pKi for prediction-versus-measurement plots; ESM vectors for embedding geometry; saved pose/crystal coordinates for structural overlays. Scope those analyses separately and keep current published scores unchanged.
9. Only after Git preservation and a separate backup of excluded data/models, approve a specific ZIP deletion list. The repo does not contain databases, source archives, cache vectors, checkpoints or pose directories. ZIPs are review snapshots, not all scientific evidence.

References for these interface steps: https://code.claude.com/docs/en/cli-reference and https://cli.github.com/manual/gh_repo_create.
