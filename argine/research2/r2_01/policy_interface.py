"""The causal / non-causal policy interface R2-02 plugs into.

R2-01 does NOT define a new policy interface: Research Part 1's
`argine.research.policies.Policy` already distinguishes the two (section
13 of the R2-01 task explicitly asks to reuse, not duplicate, existing
policy logic):

* CAUSAL  (`Policy.needs_future == False`, the default): `Policy.access()`
  is called once per (step, layer) IN STEP ORDER by `replay.replay()`. No
  future step is visible at call time -- not because the policy promises
  not to look, but because `replay.replay()` structurally never passes it
  anything beyond the current and past events.
* NON-CAUSAL / ORACLE (`Policy.needs_future == True`, e.g.
  `argine.research.oracle._BeladyBase`): explicitly built from the FULL
  event list (`oracle.event_list()` / `oracle.next_uses()`) at
  CONSTRUCTION time, before replay starts. That is the only sanctioned path
  future information can reach a policy; `replay.replay()`'s own per-step
  loop is identical for both kinds and never special-cases either.

This module adds no new policy, implements no Oracle algorithm, and runs
no policy comparison (that is R2-02's job). It only asserts and tests that
the separation above actually holds, so R2-02 can rely on it.
"""

from __future__ import annotations

from ...research.policies import Policy


class PolicyInterfaceError(ValueError):
    pass


def assert_causal(policy: Policy) -> None:
    """Raise if `policy` is not a structurally causal policy."""
    if getattr(policy, "needs_future", False):
        raise PolicyInterfaceError(
            f"{policy.name}: needs_future=True -- not a causal policy")


def assert_non_causal(policy: Policy) -> None:
    """Raise if `policy` does not declare itself as needing future
    information (the only sanctioned way to receive it)."""
    if not getattr(policy, "needs_future", False):
        raise PolicyInterfaceError(
            f"{policy.name}: needs_future=False -- not declared non-causal")
