# buildstrategies

Backtesting engine + strategies for Indian markets (NIFTY options, NSE equities).
Convention: analysis scripts live in `analysis/` and commit alongside their `_out.txt` output.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Backtest / test / validate a GIVEN trading strategy on our data → invoke /backtest-strategy (the edge is found by the backtest on our data, NOT by brainstorming). See diagrams/test-new-strategy-flow.mmd.
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
