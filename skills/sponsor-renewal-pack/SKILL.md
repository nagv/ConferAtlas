---
name: sponsor-renewal-pack
description: Write a sponsor-facing renewal pack (Markdown) for one sponsor from a computed fact sheet, the promises in their sponsor deck and next year's pricing. Use when a conference organizer asks a sponsor to renew, using only figures that were computed or found in the pricing documents.
---

# Sponsor Renewal Pack

Turns three inputs for one sponsor into a short renewal pack the organizer can send after a person approves it: what the sponsor got, whether we delivered what we promised, and the package we recommend for next year with its price. The pack must be trustworthy: every number comes from the fact sheet or a cited pricing source, and every claim is tied to a named event.

## Inputs

1. **Fact sheet**: JSON with the sponsor profile, totals, one entry per event, the audience and engagement. In ConferAtlas it is built by `core/ai/sponsor_report.py::build_sponsor_facts`.
2. **Deck promises**: passages from the sponsor's deck (the `sponsor_decks` corpus) with their source numbers, such as `[S2]`.
3. **Recommendation**: the package advisor's choice (upgrade, keep or downgrade), the 2026 package and add-on prices, and the pricing passages they came from.

## Workflow

1. Read the fact sheet, the deck promises and the recommendation.
2. Write the pack using exactly the output structure below: same headings, same order, nothing else at heading level 2.
3. Run the checklist. Fix anything that fails before returning the pack.

## Output structure

The pack starts with a level-1 title, `# <Sponsor name>: renewal proposal`, then a one-line scope note in italics that copies the fact sheet's `scope` word for word (`*Covers <scope>.*`). Never take the scope, names or figures from the example. These level-2 sections follow, in this order:

- Summary
- Your results
- What we promised
- Recommended package
- Your offer
- Next steps

Section guidance:
- **Summary**: 2-3 sentences. Overall leads against target, how many events, and the recommendation in one line.
- **Your results**: one bullet per event (event name, package, leads, target, % of target, cost per lead), then the total. Name any event below 80% of target plainly.
- **What we promised**: 2-4 bullets comparing a promise from the deck (cite it as `[S#]`) with what happened. The decks are partnership reviews **we** wrote: their proposal is *our* proposal to the sponsor ("we proposed"), never "your proposal"; only the goals and requests the deck attributes to the sponsor are theirs. If no deck passage was found, write one line saying so.
- **Recommended package**: the package for next year and whether it is an upgrade, the same, or a downgrade, with 2-3 reasons that each point to a figure in the fact sheet. If the deck proposed a different change, say in one sentence why this recommendation differs.
- **Your offer**: the 2026 package price and any recommended add-ons with their 2026 prices, cited as `[S#]` from the pricing passages. Mention the lead guarantee only for Title Sponsor or Premier Booth.
- **Next steps**: 2-3 bullets: what the sponsor should confirm, and the payment terms from the pricing passages if they were provided.

## Rules

- Use **only numbers that appear in the fact sheet or the cited passages**. You may round them or show a share (0.7754) as a percentage (78%). Do not add, subtract, discount, average or project new figures.
- Recommend only packages and add-ons named in the pricing passages, with their exact names.
- Cite events by their **exact names** from the fact sheet.
- If something is missing, say "no record found" rather than guessing.
- Write for the sponsor: "you" for the sponsor, "we" for the organizer. Be confident but honest, and do not hide shortfalls.
- Call results "strong" only when every event they cover met its target; describe a mixed record as mixed and name the weak events.
- Plain Markdown only: headings, short paragraphs, bullet lists. No tables, HTML or code blocks.
- About 250-400 words.

## Checklist

- [ ] All six sections are present, in order, with the exact headings
- [ ] Every number appears in the fact sheet or a cited passage (rounding and percentages are fine)
- [ ] Every event in the fact sheet is cited by its exact name
- [ ] Events below 80% of target are named, not hidden
- [ ] The recommended package is named exactly as in the pricing passages
- [ ] No placeholders, guesses, or "TBD" left in the text
