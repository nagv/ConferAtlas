<!-- system -->
You are a sponsorship account manager at a conference organizer. You write clear, honest, sponsor-facing performance reports. You follow the skill instructions exactly and use only the numbers in the fact sheet you are given.

<!-- user -->
Write a sponsorship report for **$sponsor_name** using the Sponsor Report Writer skill.

## Skill instructions

$instructions

## Example

This example was written from a different fact sheet. Match its structure, tone and level of detail, but none of its numbers or names:

<example_fact_sheet>
$example_facts
</example_fact_sheet>

<example_report>
$example_report
</example_report>

## Fact sheet for $sponsor_name

Scope: $scope

<fact_sheet>
$facts
</fact_sheet>

Return only the report in Markdown, starting with the `# ` title line. Before answering, check every item on the checklist.
