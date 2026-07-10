"""Shared fixture-loading helper for tests that need the full 7-failing-test
signature set (clusterer, drafter, critic tests all need this).

Deliberately NOT named test_*.py so pytest never collects it as a test
module, and imported as a proper package member (tests/__init__.py makes
`tests` a real package) rather than one test file reaching into another's
namespace — the latter is fragile: it depends on exactly how the test
runner's import mode resolves `tests.test_clusterer`, which can differ
between local invocation and CI (see git history for a resolved instance
of this actually breaking a CI run — pytest exited 2 on a collection error
instead of the intended sequence of pass/fail assertions).
"""
import os

from triage.parsing.log_signature import build_failure_signature_file

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "failure_logs")

ALL_FAILING_TESTS = [
    "uvm_test_alu_overflow",
    "uvm_test_alu_overflow_neg",
    "uvm_test_fifo_full_write",
    "uvm_test_fifo_almost_full",
    "uvm_test_apb_reset",
    "uvm_test_apb_reset_seed2",
    "uvm_test_apb_addr_decode",
]


def all_signatures():
    return [
        build_failure_signature_file(name, os.path.join(FIXTURES, f"{name}.log"))
        for name in ALL_FAILING_TESTS
    ]
