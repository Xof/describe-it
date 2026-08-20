---
id: 0013
title: Detect refusals with a guarded heuristic rather than letting them through
date: 2026-08-19
status: Accepted
summary: A refusal phrase opening a short or run-on reply raises DescriptionRefusedError, which carries the cleaned text; the guard keeps descriptions that merely start "I cannot see".
---

# 0013. Detect refusals with a guarded heuristic rather than letting them through

## Context

A refusal is a valid string. Spec §2.6 step 5 describes what that costs: the
check "converts the common 'I'm sorry, but I can't describe this image' failure
into an exception instead of garbage alt text". Given the use case in ADR 0001
— images a commercial service would reject — refusals are a common outcome, not
an edge case.

But a refusal cannot be recognised reliably, and a genuine description may open
the same way. Spec §8 Q4 put the question to sign-off: adopt a guarded
heuristic, "or drop the heuristic entirely and let refusals through as plain
strings?"

## Decision

The guard from spec §8 Q4's Resolution: "fire only when a refusal phrase starts
the cleaned text *and* (the text is under 200 characters *or* it has no '.'
before index 60). `DescriptionRefusedError.response` carries the cleaned text."

The phrase list is spec §2.6 step 5's: `i'm sorry`, `i cannot`, `i can't`,
`i am unable`, `i'm unable`, `i apologi[sz]e`, `as an ai`.

## Alternatives considered

- **Dropping the heuristic and returning refusals as plain strings** — offered
  in spec §8 Q4 and rejected by its Resolution.
- **An unguarded match** — spec §8 Q4 states the failure it avoids: a guard is
  needed so the check does not fire on "a description that happens to start
  with 'I cannot see…'".

## Consequences

- The check is a heuristic and is documented as one. Spec §2.6 step 5: "This is
  a heuristic and is documented as such"; callers "who want the text anyway can
  read it off the exception".
- Spec §6.1 accepts a specific false positive: "a *short* description opening
  with such a phrase is treated as a refusal — accepted cost, since the prompt
  forbids that opening and refusals are the common case."
- The guard is an OR, so a long refusal that runs on without an early sentence
  break still fires (spec §8 Q4 Resolution; spec §6.1's guard cases).

## Addendum (2026-08-20)

Corrections from the unit-1 review, recorded in the unit-1 errata:

- The rule is start-anchored and runs on the **cleaned** text. "§2.6 step 5
  originally said 'against the first 80 characters' and 'raw text'; corrected
  above to the start-anchored, guarded rule and cleaned text." Decoration sits
  in front of the phrase and would defeat an anchored pattern otherwise.
- The regex "also accepts U+2018/U+2019 apostrophes and `i am sorry`". Commit
  `9693b3d` records why: "A model saying 'I'm sorry' with U+2019 was passed
  straight through as alt text."
- Spec §6.1's own example of the guard was wrong and was replaced: "§6.1's
  refusal guard example originally described a 99-character text as passing
  through; under the OR guard it raises."
