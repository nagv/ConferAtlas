---
name: sponsor-report-writer
description: Write a sponsor-facing performance report (Markdown) for one sponsor from a computed fact sheet. Use when a conference organizer needs to show a sponsor what they got from their sponsorship, using only figures that were calculated beforehand.
---

# Sponsor Report Writer

Turns a **fact sheet** of pre-computed figures for one sponsor into a short, professional report that the organizer can send to that sponsor. The report must be trustworthy: every number comes from the fact sheet, and every claim is tied to a named event.

## Inputs

1. **Fact sheet**: JSON with the sponsor profile, the scope (filters applied), totals, one entry per event the sponsor attended, the audience reached at those events, and engagement at those events. In ConferAtlas it is built by `core/ai/sponsor_report.py::build_sponsor_facts` from `core/analysis.py` results.
2. **Prompt template**: `prompt.md` in this folder. `$`-placeholders are filled in by code.
3. **Example**: `example_report.md`, written from `example_facts.json`. It shows the expected tone, length and level of detail.

## Workflow

1. Read the whole fact sheet. Note the scope, so the report says which period or selection it covers.
2. Write the report using exactly the output structure below: same headings, same order, nothing else at heading level 2.
3. Run the checklist. Fix anything that fails before returning the report.

## Output structure

The report starts with a level-1 title, `# <Sponsor name>: sponsorship report`, then a one-line scope note in italics. These level-2 sections follow, in this order:

- Executive summary
- Audience reached
- Engagement
- Leads vs. target
- Highlights
- Recommendations

Section guidance:
- **Executive summary**: 2-3 sentences. Overall leads against target, how many events, and the single most important takeaway.
- **Audience reached**: registrations and attendees at the sponsor's events, show-up rate, and the largest seniority, role, industry and country groups (with their shares).
- **Engagement**: session ratings, NPS, and engagement per attendee at those events. Booth visits are event-wide traffic, so say so.
- **Leads vs. target**: one bullet per event (event name, leads, target, % of target, cost per lead), then the total. Name any event below 80% of target plainly.
- **Highlights**: 2-4 bullets on the strongest results, each citing an event name.
- **Recommendations**: 2-4 concrete, forward-looking suggestions (package, event mix, lead capture). Base them on the figures, but do not invent new numbers. Write no percentages or targets that are not in the fact sheet.

## Rules

- Use **only numbers that appear in the fact sheet**. You may round them or show a share (0.7754) as a percentage (78%). Do not add, subtract, average or project new figures.
- Cite events by their **exact names** from the fact sheet, and mention every event the sponsor attended at least once.
- If something is missing from the fact sheet, leave it out rather than guessing.
- Write for the sponsor: "you" for the sponsor, "we" for the organizer. Stay confident but honest, and do not hide shortfalls.
- Plain Markdown only: headings, short paragraphs, bullet lists. No tables, HTML or code blocks.
- About 300-450 words.

## Checklist

- [ ] All six sections are present, in order, with the exact headings
- [ ] Every number appears in the fact sheet (rounding and percentages are fine)
- [ ] Every event in the fact sheet is cited by its exact name
- [ ] Events below 80% of target are named, not hidden
- [ ] Booth visits are described as event-wide traffic
- [ ] No placeholders, guesses, or "TBD" left in the text
