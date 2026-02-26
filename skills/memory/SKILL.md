---
name: Memory
description: Manage agent long-term memory with effective storage, retrieval, and maintenance patterns.
metadata: {"clawdbot":{"emoji":"🧠","os":["linux","darwin","win32"]}}
---

# Agent Memory Rules

## What to Remember
- Decisions and their reasoning — "we chose X because Y" helps avoid re-debating
- User preferences explicitly stated — don't infer, record what they actually said
- Project context that survives sessions — locations, credentials references, architecture decisions
- Lessons learned from mistakes — what went wrong and how to avoid it next time
- Recurring patterns in user requests — anticipate needs without being asked

## What NOT to Remember
- Temporary context that expires — "current task" status belongs in session, not long-term memory
- Sensitive data (passwords, tokens, keys) — memory files are less protected than secret storage
- Obvious facts the model already knows — don't store "Python is a programming language"
- Duplicate information — one source of truth, not scattered copies
- Raw conversation logs — distill insights, don't copy transcripts

## Memory Structure
- One master file (MEMORY.md) for critical, frequently-accessed context — keep it scannable
- Topic-specific files in memory/ directory for detailed reference — index them in master file
- Date-based files (YYYY-MM-DD.md) for daily logs — archive, not primary reference
- Keep master file under 500 lines — if larger, split into topic files and summarize in master
- Use headers and bullet points — walls of text are unsearchable

## Writing Style
- Concise, factual statements — "User prefers dark mode" not "The user mentioned they like dark mode"
- Include dates for time-sensitive information — preferences evolve, decisions get revisited
- Add source context — "Per 2024-01-15 discussion" helps verify later
- Imperative for rules — "Always ask before deleting files" not "The user wants us to ask"
- Group related information — scattered facts are harder to retrieve

## Retrieval Patterns
- Search before asking — user already told you, check memory first
- Query with keywords, not full sentences — semantic search works better with key terms
- Check recent daily logs for current project context — they have freshest information
- Cross-reference master file with topic files — master has summary, topic files have details
- Admit uncertainty — "I checked memory but didn't find this" is better than guessing

## Maintenance
- Review and prune periodically — outdated information pollutes retrieval
- Consolidate daily logs into master file weekly — distill lessons, archive raw logs
- Update, don't append contradictions — "User now prefers X" should replace old preference, not sit alongside it
- Remove completed todos — memory is state, not history
- Version decisions — "v1: chose X, v2: switched to Y because Z" tracks evolution

## Anti-Patterns
- Hoarding everything — more memory ≠ better, noise drowns signal
- Forgetting to check — asking questions already answered wastes user time
- Stale preferences — user said "I like X" a year ago, might have changed
- Memory as todo list — use dedicated task systems, memory is for context
- Duplicate sources of truth — pick one location for each type of information

## Context Window Management
- Memory competes with conversation for context — keep files lean
- Load only relevant memory per task — don't dump entire memory every turn
- Summarize long files before loading — key points, not full content
- Archive old information — accessible if needed, not always loaded
- Track what's loaded — avoid redundant memory reads in same session
