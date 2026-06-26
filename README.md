# Zotero Skill for Claude Code

Operate Zotero Desktop local library from Claude Code — search items, export BibTeX, insert citations into LaTeX/Markdown, read indexed full text, and import references.

## Origin

Adapted from [openai/plugins/zotero](https://github.com/openai/plugins/tree/main/plugins/zotero) (MIT) for Claude Code.

Changes from original:
- `python3` → `python` (Windows compatibility)
- Hardcoded skill path for Claude Code global skills directory
- Codex-specific files removed (`.codex-plugin/`, `agents/`)

## Install

```bash
git clone https://github.com/<your-username>/openai_plugins-zotero-for_claude_code.git ~/.claude/skills/zotero
```

Requires Zotero Desktop with local API enabled (port 23119).

## Usage

```powershell
python C:\Users\Nan\.claude\skills\zotero\scripts\zotero.py status --json
python C:\Users\Nan\.claude\skills\zotero\scripts\zotero.py search "keyword"
python C:\Users\Nan\.claude\skills\zotero\scripts\zotero.py cite --query "Title" --markdown draft.md --bib refs.bib --marker '<cite>'
```

See `SKILL.md` for full command reference.

## License

MIT — same as original openai/plugins.
