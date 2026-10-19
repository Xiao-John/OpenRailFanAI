# FUCK_CR

> **China Railway Compatibility & Unsolicited Innovation Recovery Layer**
>
> *Making yesterday's perfectly valid assumptions work again, one breaking change at a time.*

> 资料索引 / Reference index: [`INDEX.md`](INDEX.md) ｜ 状态：尚未实施，首批 11 月下旬

## Overview

This directory contains compatibility patches for unexpected, unsolicited, and occasionally inspirational changes to the Chinese railway train numbering system.

These patches do not introduce new features. They merely restore functionality that was working perfectly fine before someone discovered that changing letters could be considered progress.

We would like to express our sincere gratitude to **China Railway (CR)** for providing a continuous supply of real-world regression testing scenarios.

Without their visionary contributions, this directory would not exist.

Neither would the bugs it fixes.

## Design Philosophy

China Railway has demonstrated that a train number is not merely an identifier.

It is a dynamic cultural artifact, whose meaning may evolve according to administrative requirements, institutional preferences, and other forces beyond the scope of computer science.

Accordingly, this project adopts the following principles:

1. **Never assume that a letter means what it meant yesterday.**
2. Never assume that a letter is pronounced the way its meaning suggests.
3. Never assume that preserving a pronunciation implies preserving its meaning.
4. Never assume that preserving a meaning implies preserving its pronunciation.
5. Never assume that a working system is safe from improvement.

These principles are based on practical experience, not theoretical speculation.

## Why Does This Directory Exist?

Traditionally, software engineers distinguish between backward compatibility and breaking changes.

China Railway has kindly introduced a third category:

**Backward Contradiction™**

Under this innovative paradigm, existing identifiers may retain their spelling while acquiring entirely different meanings.

For example:

| Prefix | Previous Interpretation | Revised Interpretation |
|---|---|---|
| T | Express conventional train | A new category of EMU |
| K | Fast conventional train | Another category of EMU |
| Z | Direct express, pronounced *Zhi* | Still associated with direct service, but no longer pronounced *Zhi* |

This remarkable design ensures that neither semantic continuity nor phonetic continuity can be taken for granted.

We congratulate CR on discovering a new dimension of software compatibility.

## Economic Contributions

The maintainers recognize the broader economic significance of these reforms.

A single administrative decision may generate demand for:

- Software redevelopment
- Database migrations
- Documentation updates
- Staff retraining
- Regression testing
- Historical data reconciliation
- Psychological counseling for open-source maintainers

This project is proud to participate, involuntarily, in the modernization of China's railway information infrastructure.

**We previously believed that technical debt was created by developers. We thank CR for correcting this misconception.**

## Compatibility Policy

OpenRailFanAI remains committed to supporting historical and contemporary Chinese railway data.

Unlike the institutions responsible for defining these identifiers, we cannot simply declare that yesterday's data has ceased to exist.

Therefore:

- Historical train numbers retain their historical interpretations.
- New numbering rules are explicitly versioned.
- Pronunciation and semantic classification are stored independently.
- Ambiguous identifiers must not be silently reinterpreted.
- Future administrative innovations are presumed possible.

We regret that time travel has become a prerequisite for parsing train numbers correctly.

## Frequently Asked Questions

**Q: Why not just update the train number parser?**

A: We did. Then we discovered that the parser was not the only component that believed words had meanings.

**Q: Why is this directory named `FUCK_CR`?**

A: The original proposal was `china_railway_unplanned_compatibility_adjustments_and_associated_technical_debt`.

The maintainers selected the shorter identifier to reduce memory usage and improve readability.

We trust CR will appreciate our commitment to intuitive naming.

**Q: Could these changes have been implemented without redefining existing prefixes?**

A: Several alternative designs appear technically conceivable.

Unfortunately, this repository does not have access to the administrative considerations that made the selected approach uniquely desirable.

We therefore respectfully acknowledge the possibility that the advantages are simply too sophisticated for us to understand.

**Q: Will this directory eventually be removed?**

A: Certainly.

It will be removed as soon as railway identifiers are guaranteed to have stable meanings across time.

No estimated completion date is available.

## Acknowledgments

Special thanks to China Railway for reminding us that software maintenance is not merely a technical challenge.

It is an exercise in humility, institutional adaptability, and the permanent abandonment of reasonable expectations.

We look forward to future reforms with the same enthusiasm that database administrators reserve for unannounced schema migrations.

---

*Disclaimer: This directory is an independent open-source compatibility project. It is not affiliated with, endorsed by, or responsible for the decisions of China Railway.*

*All technical debt generated by external administrative innovation remains the intellectual property of its respective creators.*

**73, and may your identifiers remain meaningful.**
