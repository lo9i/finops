"""Detector logic tests (in-memory DB, no API)."""
from __future__ import annotations

from pathlib import Path

from app.detectors import run_all
from app.ingest import ingest_billing_file, ingest_inventory_file
from app.models import Finding, ReleasedResource

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _seed(session):
    ingest_billing_file(
        session,
        "aws_history.csv",
        (SAMPLES / "aws_cur_history_sample.csv").read_bytes(),
    )
    ingest_billing_file(
        session, "aws.csv", (SAMPLES / "aws_cur_sample.csv").read_bytes()
    )
    ingest_billing_file(
        session, "azure.json", (SAMPLES / "azure_export_sample.json").read_bytes()
    )
    ingest_inventory_file(
        session,
        "aws_inv.json",
        (SAMPLES / "aws_inventory_sample.json").read_bytes(),
    )
    ingest_inventory_file(
        session,
        "azure_inv.json",
        (SAMPLES / "azure_inventory_sample.json").read_bytes(),
    )


def test_all_detectors_fire_on_sample(session):
    _seed(session)
    run = run_all(session)
    findings = session.query(Finding).all()
    detectors = {f.detector for f in findings}
    expected = {
        "orphan_ebs_volume",
        "orphan_azure_disk",
        "idle_ec2",
        "idle_azure_vm",
        "old_ebs_snapshot",
        "idle_elb",
        "unassociated_eip",
        "idle_eip_by_billing",
    }
    assert expected.issubset(detectors), f"missing: {expected - detectors}"
    assert run.findings_count == len(findings)
    assert run.monthly_waste > 0


def test_orphan_ebs_finds_two_unattached(session):
    _seed(session)
    run_all(session)
    findings = (
        session.query(Finding).filter(Finding.detector == "orphan_ebs_volume").all()
    )
    rids = {f.resource.resource_id for f in findings}
    assert rids == {"vol-0a1b2c3d4e5f60001", "vol-0a1b2c3d4e5f60002"}
    for f in findings:
        assert "delete-volume" in f.remediation_command
        assert "--dry-run" in f.remediation_command


def test_healthy_resources_do_not_fire(session):
    _seed(session)
    run_all(session)
    rids = {f.resource.resource_id for f in session.query(Finding).all()}
    healthy = {
        "vol-0a1b2c3d4e5f60003",
        "i-0aabbccddeeff0002",
        "snap-04a5b6c7d8e9f0002",
        "eipalloc-0aabb22222ccceeee",
        "healthy-disk-1",
        "vm-prod-1",
    }
    assert healthy.isdisjoint(rids)


def test_idle_ec2_command_has_dry_run(session):
    _seed(session)
    run_all(session)
    f = session.query(Finding).filter(Finding.detector == "idle_ec2").one()
    assert f.remediation_command.startswith("aws ec2 stop-instances")
    assert "--dry-run" in f.remediation_command
    assert f.monthly_cost_estimate > 0


def test_idle_azure_vm_command(session):
    _seed(session)
    run_all(session)
    f = session.query(Finding).filter(Finding.detector == "idle_azure_vm").one()
    assert f.remediation_command.startswith("az vm deallocate")
    assert "rg-prod-eus" in f.remediation_command


def test_old_snapshot_threshold(session):
    _seed(session)
    run_all(session)
    findings = (
        session.query(Finding).filter(Finding.detector == "old_ebs_snapshot").all()
    )
    assert len(findings) == 1
    assert findings[0].resource.resource_id == "snap-04a5b6c7d8e9f0001"


def test_released_resource_is_suppressed_on_next_run(session):
    _seed(session)
    run_all(session)
    target = (
        session.query(Finding).filter(Finding.detector == "orphan_ebs_volume").first()
    )
    assert target is not None
    rid = target.resource.resource_id

    # Mark this one released.
    session.add(
        ReleasedResource(
            resource_id=rid,
            provider="aws",
            resource_type="EBS_VOLUME",
            region="us-east-1",
            detector="orphan_ebs_volume",
            monthly_cost_saved=target.monthly_cost_estimate,
            remediation_command=target.remediation_command,
        )
    )
    session.flush()

    # Re-run: that resource must NOT appear as a finding anymore.
    run_all(session)
    open_rids = {
        f.resource.resource_id
        for f in session.query(Finding)
        .filter(Finding.detector == "orphan_ebs_volume")
        .all()
    }
    assert rid not in open_rids


def test_run_all_clears_previous_findings(session):
    _seed(session)
    run1 = run_all(session)
    run2 = run_all(session)
    assert run1.findings_count == run2.findings_count
    assert session.query(Finding).count() == run2.findings_count


def test_billing_only_resource_is_inferred(session):
    """A resource that appears only in billing (no inventory) should be inferred."""
    from pathlib import Path
    from app.ingest import ingest_billing_file
    from app.models import Resource

    SAMPLES = Path(__file__).resolve().parent.parent / "samples"
    ingest_billing_file(
        session, "aws_cur_history_sample.csv", (SAMPLES / "aws_cur_history_sample.csv").read_bytes()
    )

    r = (
        session.query(Resource)
        .filter(Resource.resource_id == "i-billed-only-deadbeef00")
        .one()
    )
    assert r.is_inferred is True
    assert r.resource_type == "EC2_INSTANCE"
    assert r.first_seen_at is not None
    assert r.last_seen_at is not None


def test_explicit_inventory_promotes_inferred_resource(session):
    """Inventory upload over an inferred resource flips is_inferred to False."""
    from pathlib import Path
    import json
    from app.ingest import ingest_billing_file, ingest_inventory_file
    from app.models import Resource

    SAMPLES = Path(__file__).resolve().parent.parent / "samples"

    # Ingest billing first — the vol from history will be inferred.
    ingest_billing_file(
        session, "aws_cur_history_sample.csv", (SAMPLES / "aws_cur_history_sample.csv").read_bytes()
    )
    r = session.query(Resource).filter(
        Resource.resource_id == "vol-0a1b2c3d4e5f60001"
    ).one()
    assert r.is_inferred is True
    first_seen = r.first_seen_at

    # Now the inventory upload promotes it.
    ingest_inventory_file(
        session, "aws_inventory_sample.json", (SAMPLES / "aws_inventory_sample.json").read_bytes()
    )
    session.refresh(r)
    assert r.is_inferred is False
    assert r.state == "available"
    assert r.attachments == []
    # first_seen_at must be preserved (it comes from billing)
    assert r.first_seen_at == first_seen


def test_unmonitored_long_running_fires_on_billed_only(session):
    _seed(session)  # loads history (i-billed-only) and main sample
    run_all(session)
    finds = (
        session.query(Finding)
        .filter(Finding.detector == "unmonitored_long_running")
        .all()
    )
    rids = {f.resource.resource_id for f in finds}
    assert "i-billed-only-deadbeef00" in rids


def test_idle_eip_by_billing_fires(session):
    _seed(session)
    run_all(session)
    f = (
        session.query(Finding)
        .filter(Finding.detector == "idle_eip_by_billing")
        .one_or_none()
    )
    assert f is not None
    assert f.resource.resource_id == "eipalloc-0aabb12345cccdddd"
    assert "release-address" in f.remediation_command
