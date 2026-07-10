import os

from triage.parsing.log_signature import build_failure_signature_file, extract_failure_events

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "failure_logs")


def _sig(name):
    return build_failure_signature_file(name, os.path.join(FIXTURES, f"{name}.log"))


def test_extracts_events_ignoring_info_lines():
    events = extract_failure_events(
        "UVM_INFO tb.sv(1) @ 0: reporter [TB] hello\n"
        "UVM_ERROR foo.sv(10) @ 100: uvm_test_top.env.x [BAD_THING] oops\n"
    )
    assert len(events) == 1
    e = events[0]
    assert e.severity == "UVM_ERROR"
    assert e.file == "foo.sv"
    assert e.line == 10
    assert e.sim_time == 100
    assert e.hierarchy == "uvm_test_top.env.x"
    assert e.msg_id == "BAD_THING"
    assert e.message == "oops"


def test_signature_fields():
    sig = _sig("uvm_test_alu_overflow")
    assert sig.test_name == "uvm_test_alu_overflow"
    assert sig.msg_ids == ("SCOREBOARD_MISMATCH",)
    assert sig.hierarchy_paths == ("uvm_test_top.env.scoreboard",)
    assert sig.first_error_time == 4200
    assert len(sig.events) == 2


def test_fatal_outranks_error_for_worst_severity():
    sig = _sig("uvm_test_fifo_full_write")
    assert sig.severity == "UVM_FATAL"


def test_same_root_cause_shares_feature_key():
    """The core clustering assumption (Objective #2): tests failing from the
    same seeded bug should produce identical structured feature keys, even
    when one test surfaces more symptomatic errors than the other."""
    overflow_a = _sig("uvm_test_alu_overflow")
    overflow_b = _sig("uvm_test_alu_overflow_neg")
    fifo_a = _sig("uvm_test_fifo_full_write")
    fifo_b = _sig("uvm_test_fifo_almost_full")

    # both fifo tests hit the same msg_id/hierarchy -> should cluster together
    assert fifo_a.msg_ids == fifo_b.msg_ids
    assert fifo_a.hierarchy_paths == fifo_b.hierarchy_paths

    # alu overflow tests share the mismatch signature too
    assert overflow_a.msg_ids == overflow_b.msg_ids

    # but the two different root causes must NOT collide
    assert overflow_a.feature_key() != fifo_a.feature_key()


def test_apb_reset_seed2_has_superset_of_msg_ids():
    """apb_reset_seed2 has an extra secondary symptom — clustering should
    still be able to relate it to apb_reset via the shared primary msg_id,
    which is why the Clusterer Agent uses similarity, not exact-match."""
    reset_a = _sig("uvm_test_apb_reset")
    reset_b = _sig("uvm_test_apb_reset_seed2")
    assert set(reset_a.msg_ids).issubset(set(reset_b.msg_ids))
    assert reset_a.hierarchy_paths == reset_b.hierarchy_paths
