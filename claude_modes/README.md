# Claude Code permission mode templates

This directory holds **reference** Claude Code settings files, one per
operating mode in the §8.5 build order of `docs/STRATEGY_RESEARCH_ROADMAP.md`.
Templates here are **not** active by default. To activate a mode you copy the
template to `.claude/settings.json` (project) or `.claude/settings.local.json`
(your machine only — gitignored).

See `docs/CLAUDE_CODE_PERMISSIONS.md` for the full model, gaps, and discipline.

## Status

| Mode | Template | Roadmap stage | Shipped here? |
|---|---|---|---|
| **research** | `settings.research.json` | §8.4 (now) | **yes** |
| validation | `settings.validation.json` | §8.5 step 5 | not yet — add when `fetch_market_data.sh` is written |
| paper | `settings.paper.json` | §8.5 step 3 / 8 | not yet — add when dry-run mode is built |
| live-assist | `settings.liveassist.json` | §8.5 step 9 | not yet — needs reviewed wrapper scripts first |

We ship one mode at a time, in §8.5 order. Adding a template *before* the
corresponding stage exists creates an attractive nuisance.

## Activate research mode

```bash
# Project-level (committed; everyone in the repo sees this mode):
cp claude_modes/settings.research.json .claude/settings.json

# OR machine-local only (not committed):
mkdir -p .claude
cp claude_modes/settings.research.json .claude/settings.local.json
```

Verify the PreToolUse hook is wired:

```bash
./scripts/check_guard.sh
```

The self-test asserts the guard blocks/allows the expected fixtures in **all
four** modes (the hook covers all four even though only the research settings
template ships today).

## Switching modes later

1. Make sure the new mode's template exists in this directory (add when the
   §8.5 stage is built).
2. Replace your `.claude/settings.json` (or `.claude/settings.local.json`)
   with the new template.
3. Restart Claude Code so the new settings + hook are picked up.
4. Run `./scripts/check_guard.sh` to confirm the hook fires correctly.

## What this is and is not

- This is **a backstop against accidents**, not adversarial defense.
- The primary controls remain: (a) application code with no order paths,
  (b) sandbox network policy, (c) the settings allow/deny lists, (d) your
  own review of what you ask Claude to do.
- The guard hook is a **fourth** layer. It catches the obvious classes
  (fund movement, exchange hosts, free-form network, paper-vs-live flag
  collisions) but cannot inspect code Python will run at runtime.
