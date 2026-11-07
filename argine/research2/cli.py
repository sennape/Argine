"""`argine research2 ...` -- the Research Part 2 automation CLI family.

    argine research2 status            cheap, read-only
    argine research2 doctor            read-only environment diagnostic
    argine research2 next              next eligible experiment
    argine research2 run R2-01         generic run contract (NOT_IMPLEMENTED for now)
    argine research2 analyse R2-01     generic analysis contract (NOT_IMPLEMENTED for now)
    argine research2 audit             infrastructure consistency check

R2-M0.1 implements the infrastructure only: no R2-01..R2-12 scientific logic.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import runner

EXIT_OK = 0
EXIT_USAGE = 2


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=False))


def cmd_status(args) -> int:
    _print(runner.status_report())
    return EXIT_OK


def cmd_doctor(args) -> int:
    report = runner.doctor_report()
    _print(report)
    return EXIT_OK if report["ok"] else runner.EXIT_CONFIG_INVALID


def cmd_next(args) -> int:
    from . import roadmap as roadmap_mod, store
    rm = roadmap_mod.load()
    nxt = runner.next_eligible(rm, store.load_state())
    _print(nxt if nxt is not None else {"next_eligible": None,
                                        "message": "every experiment is COMPLETED"})
    return EXIT_OK


def cmd_run(args) -> int:
    try:
        result = runner.run_experiment(args.experiment_id, allow_dirty=args.allow_dirty,
                                       trace_source_arg=getattr(args, "trace_source", None))
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return runner.EXIT_UNKNOWN_ID
    _print(result)
    outcome = result.get("outcome")
    if outcome == "REFUSED_DIRTY_TREE":
        return runner.EXIT_DIRTY_REFUSED
    if outcome == "NOT_IMPLEMENTED":
        return runner.EXIT_NOT_IMPLEMENTED
    if outcome in ("TRACE_SOURCE_MISSING", "TRACE_SOURCE_AMBIGUOUS"):
        return runner.EXIT_BLOCKED
    if outcome == "FAIL":
        return runner.EXIT_SCIENTIFIC_FAIL
    return EXIT_OK


def cmd_analyse(args) -> int:
    try:
        result = runner.analyse_experiment(args.experiment_id, run_id=getattr(args, "run_id", None))
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return runner.EXIT_UNKNOWN_ID
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return runner.EXIT_BLOCKED
    _print(result)
    if result.get("outcome") == "NOT_IMPLEMENTED":
        return runner.EXIT_NOT_IMPLEMENTED
    return EXIT_OK


def cmd_audit(args) -> int:
    report = runner.audit_report()
    _print(report)
    return EXIT_OK if report["ok"] else runner.EXIT_AUDIT_FAILED


def register(sub, core) -> None:
    """Add `research2` to the main parser. `core` is argine.cli, kept as a
    parameter for symmetry with research.cli.register even though R2-M0.1
    does not need shared runtime args yet."""
    rp = sub.add_parser("research2", help="Argine Research Part 2 automation "
                        "(status, doctor, next, run, analyse, audit)",
                        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    rs = rp.add_subparsers(dest="research2_command", metavar="research2-command")

    p = rs.add_parser("status", help="cheap, read-only status (baseline, experiment states, next)")
    p.set_defaults(func=cmd_status)

    p = rs.add_parser("doctor", help="read-only environment/readiness diagnostic")
    p.set_defaults(func=cmd_doctor)

    p = rs.add_parser("next", help="report the next eligible experiment")
    p.set_defaults(func=cmd_next)

    p = rs.add_parser("run", help="generic experiment-run contract")
    p.add_argument("experiment_id", metavar="EXPERIMENT_ID", help="e.g. R2-01")
    p.add_argument("--allow-dirty", action="store_true",
                   help="proceed despite a dirty working tree; the run is permanently "
                        "marked NON-CANDIDATE")
    p.add_argument("--trace-source", default=None, metavar="PATH",
                   help="(R2-01) explicit source trace path; takes precedence over any "
                        "pinned local-state or documented-canonical source")
    p.set_defaults(func=cmd_run)

    p = rs.add_parser("analyse", help="generic offline-analysis contract")
    p.add_argument("experiment_id", metavar="EXPERIMENT_ID", help="e.g. R2-01")
    p.add_argument("--run-id", default=None, metavar="RUN_ID",
                   help="analyse this specific run instead of the latest completed one "
                        "(required if 'latest' would be ambiguous)")
    p.set_defaults(func=cmd_analyse)

    p = rs.add_parser("audit", help="validate Research Part 2 infrastructure consistency")
    p.set_defaults(func=cmd_audit)

    rp.set_defaults(func=lambda a: (rp.print_help(), EXIT_USAGE)[1]
                    if not getattr(a, "research2_command", None) else None)
