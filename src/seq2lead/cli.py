"""Command-line entry point.

Subcommands are added milestone by milestone. Heavy imports (torch, rdkit,
transformers) stay inside the commands that need them so that `db ping` and
`version` remain fast.
"""

from __future__ import annotations

import json
import pathlib
import platform
import sys
import time

import typer

from seq2lead import __version__
from seq2lead.config import DbConfig

app = typer.Typer(
    help="Seq2Lead 2.0 — sequence-to-lead ranking over a reproducible bioactivity database.",
    no_args_is_help=True,
    add_completion=False,
)

db_app = typer.Typer(help="Database operations.", no_args_is_help=True)
env_app = typer.Typer(help="Environment diagnostics.", no_args_is_help=True)
ingest_app = typer.Typer(help="Download and load pinned sources.", no_args_is_help=True)
profile_app = typer.Typer(help="Measure throughput and storage.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(env_app, name="env")
app.add_typer(ingest_app, name="ingest")
curate_app = typer.Typer(help="Build the curated layer.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")
endpoint_app = typer.Typer(help="Build Ki endpoint tables.", no_args_is_help=True)
app.add_typer(curate_app, name="curate")
analyze_app = typer.Typer(help="Analyse the curated endpoint.", no_args_is_help=True)
app.add_typer(endpoint_app, name="endpoint")
split_app = typer.Typer(help="Build leakage-controlled splits.", no_args_is_help=True)
app.add_typer(analyze_app, name="analyze")
app.add_typer(split_app, name="split")
asof_app = typer.Typer(help="As-of snapshot comparison (M11).", no_args_is_help=True)
app.add_typer(asof_app, name="asof")


def _run_verification() -> list[tuple[str, str]]:
    """Run the project checks and capture their real output for the report.

    Executed rather than transcribed: a verification section is worthless if the
    results were copied in by hand.
    """
    import subprocess  # noqa: S404 - running this project's own tooling

    commands = [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "ruff", "format", "--check", "."],
        # No -q here: pyproject's addopts already sets it, and -qq suppresses the
        # summary line, leaving the report with a row of progress dots.
        ["uv", "run", "pytest"],
    ]
    results: list[tuple[str, str]] = []
    for argv in commands:
        try:
            proc = subprocess.run(  # noqa: S603
                argv, capture_output=True, text=True, timeout=900, check=False
            )
            out = [ln for ln in (proc.stdout or proc.stderr).splitlines() if ln.strip()]
            # Prefer a line that states the outcome over pytest's progress dots.
            summary = next(
                (
                    ln
                    for ln in reversed(out)
                    if any(w in ln for w in ("passed", "failed", "error", "checks", "formatted"))
                ),
                out[-1] if out else f"exit {proc.returncode}",
            )
            results.append((" ".join(argv), summary.strip()))
        except (OSError, subprocess.SubprocessError) as exc:
            results.append((" ".join(argv), f"could not run: {exc}"))
    return results


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the seq2lead version."""
    typer.echo(__version__)


@db_app.command("ping")
def db_ping() -> None:
    """Connect to PostgreSQL and print its version."""
    from seq2lead.db import DatabaseUnreachable, server_version

    cfg = DbConfig.from_env()
    try:
        version_string = server_version(cfg)
    except DatabaseUnreachable as exc:
        raise _fail(str(exc)) from exc
    typer.secho(f"ok  {cfg.describe()}", fg=typer.colors.GREEN)
    typer.echo(version_string)


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply outstanding schema migrations. Idempotent."""
    from seq2lead.db import transaction
    from seq2lead.db.migrations import apply_pending

    with transaction() as conn:
        performed = apply_pending(conn)
    if not performed:
        typer.secho("schema up to date", fg=typer.colors.GREEN)
        return
    for version, name, baselined in performed:
        how = "baselined against existing tables" if baselined else "applied"
        typer.echo(f"  {version:04d}  {name}  ({how})")
    typer.secho("schema ready", fg=typer.colors.GREEN)


@db_app.command("init")
def db_init() -> None:
    """Alias for `db migrate`, kept because the README and CI reference it."""
    db_migrate()


@db_app.command("releases")
def db_releases() -> None:
    """List loaded raw releases."""
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, source_name, version, subset, rows_loaded, rows_excluded, "
            "sha256, sha256_pinned, ingested_at FROM source_release ORDER BY id"
        ).fetchall()
    if not rows:
        typer.echo("no releases loaded")
        return
    for r in rows:
        pin = "pinned" if r[7] else "UNPINNED"
        typer.echo(
            f"[{r[0]}] {r[1]} {r[2]}/{r[3]}  loaded={r[4]:,} excluded={r[5]:,}  "
            f"{r[6][:16]}… {pin}  {r[8]:%Y-%m-%d %H:%M}"
        )


@asof_app.command("export")
def asof_export(
    release_id: int = typer.Option(..., help="source_release.id of the release to export."),
    out: str = typer.Option(..., help="Destination path (.jsonl.gz)."),
) -> None:
    """Export one release's curated observations for the as-of matcher.

    Runs inside a READ ONLY transaction, so this is safe to point at the accepted
    corpus: the database refuses a write for the duration. Output is gzipped JSON
    Lines, one curated observation per line, and is byte-reproducible.
    """
    from seq2lead.asof.export import export_observations, read_only_transaction
    from seq2lead.db import connect

    with connect() as conn:
        read_only_transaction(conn)
        try:
            summary = export_observations(conn, release_id, pathlib.Path(out))
        except LookupError as exc:
            raise _fail(str(exc)) from exc
        conn.rollback()

    typer.echo(f"{summary.source_release}  (release {summary.source_release_id})")
    typer.echo(f"  rows            {summary.rows:,}")
    typer.echo(f"  entry doi       {summary.with_entry_doi:,} ({summary.entry_doi_coverage:.3%})")
    typer.echo(f"  distinct dois   {summary.distinct_entry_dois:,}")
    typer.echo(f"  source locator  {summary.with_locator:,} ({summary.locator_coverage:.3%})")
    for mtype, n in sorted(summary.measurement_types.items()):
        typer.echo(f"  {mtype:<15} {n:,}")
    for status, n in sorted(summary.assay_join_status.items()):
        typer.echo(f"  assay {status:<9} {n:,}")
    typer.echo(f"  wrote {summary.path} ({summary.path.stat().st_size:,} bytes)")


@asof_app.command("match")
def asof_match(
    a_export: str = typer.Option(..., help="Earlier snapshot's observation export (.jsonl.gz)."),
    b_export: str = typer.Option(..., help="Later snapshot's observation export (.jsonl.gz)."),
    work: str = typer.Option(..., help="Working directory for shards and detail streams."),
    shards: int = typer.Option(32, help="Shard count. Does not change the result."),
    a_sha256: str = typer.Option("", help="Expected digest of the earlier export."),
    b_sha256: str = typer.Option("", help="Expected digest of the later export."),
) -> None:
    """Diff two snapshot exports in bounded memory, by slot-hash sharding.

    Every shard pair goes through the same `diff_snapshots` the unsharded path
    uses, so the matching rules are not restated. The shard count is a resource
    knob and is asserted not to change the result.

    Descriptive matching only: no Ki aggregation, no pair eligibility, no
    endpoint build, no fitting, no scores.
    """
    import hashlib

    from seq2lead.asof.sharded import diff_sharded, partition, peak_rss_bytes

    def verify(path: pathlib.Path, expected: str) -> None:
        if not expected:
            typer.echo(f"  {path.name}: no expected digest given, not verified")
            return
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        if h.hexdigest() != expected:
            raise _fail(f"{path}: digest {h.hexdigest()} != expected {expected}")
        typer.echo(f"  {path.name}: digest VERIFIED")

    a_path, b_path, work_dir = pathlib.Path(a_export), pathlib.Path(b_export), pathlib.Path(work)
    verify(a_path, a_sha256)
    verify(b_path, b_sha256)

    stats = {}
    for label, path in (("a", a_path), ("b", b_path)):
        st = partition(path, work_dir / f"shards-{label}", shards, label)
        stats[label] = st
        if not st.reconciles:
            raise _fail(f"partition of {path} did not reconcile")
        typer.echo(
            f"  partitioned {label}: {st.rows_read:,} rows, shards "
            f"{min(st.per_shard):,}-{st.max_shard:,}, {st.seconds:,.0f}s"
        )
    pairs = [stats["a"].per_shard[i] + stats["b"].per_shard[i] for i in range(shards)]
    typer.echo(f"  largest shard pair: {max(pairs):,} observations")

    summary = diff_sharded(
        work_dir / "shards-a", work_dir / "shards-b", work_dir / "detail", shards
    )
    if not summary.reconciles:
        raise _fail("input accounting does not reconcile; refusing to report the diff")
    body = summary.as_dict()
    typer.echo(
        f"  unchanged {summary.unchanged:,}  added {summary.additions:,}  "
        f"removed {summary.removals:,}"
    )
    typer.echo(
        f"  A = unchanged + removals  = {summary.a_reconstructed:,} (in {summary.a_rows_in:,})"
    )
    typer.echo(
        f"  B = unchanged + additions = {summary.b_reconstructed:,} (in {summary.b_rows_in:,})"
    )
    typer.echo(
        f"  corrections {summary.correction_candidates:,} (provisional)  "
        f"conflicts {summary.identifier_conflicts:,}  "
        f"ambiguous {summary.ambiguous_slots:,}"
    )
    for mtype, counts in body["by_measurement_type"].items():
        typer.echo(
            f"  {mtype:<6} A {counts['a_observations']:>9,}  B {counts['b_observations']:>9,}  "
            f"+{counts['additions']:,} -{counts['removals']:,}"
        )
    out = work_dir / "matching-summary.json"
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(f"  {summary.seconds:,.0f}s, peak RSS {peak_rss_bytes() / 2**30:.2f} GB")
    typer.echo(f"  wrote {out}; detail streams in {work_dir / 'detail'}")


@asof_app.command("aggregate-ki")
def asof_aggregate_ki(
    a_export: str = typer.Option(..., help="Earlier snapshot's observation export."),
    b_export: str = typer.Option(..., help="Later snapshot's observation export."),
    work: str = typer.Option(..., help="Working directory for pair shards and detail."),
    shards: int = typer.Option(64, help="Pair-shard count. Does not change the result."),
    threshold: float = typer.Option(6.0, help="Activity threshold in pKi."),
    excluded_locators: str = typer.Option(
        "", help="JSON file of addition locators to exclude in the sensitivity arm."
    ),
) -> None:
    """Aggregate KI evidence to pairs and report eligibility. No fitting, no scores.

    Shards on the compound-target pair, because eligibility is a property of a
    pair and one pair owns many slots. The diff inside each shard runs on all
    four measurement types so its classifications match the descriptive run, and
    only its outputs are restricted to KI.
    """
    from seq2lead.asof.ki_aggregation import aggregate, partition_by_pair

    work_dir = pathlib.Path(work)
    for label, path in (("a", pathlib.Path(a_export)), ("b", pathlib.Path(b_export))):
        stats = partition_by_pair(path, work_dir / f"shards-{label}", shards, label)
        if not stats.reconciles:
            raise _fail(f"partition of {path} did not reconcile")
        typer.echo(
            f"  partitioned {label}: {stats.rows_read:,} rows ({stats.ki_rows:,} KI), "
            f"{stats.seconds:,.0f}s"
        )

    excluded = frozenset()
    if excluded_locators:
        excluded = frozenset(
            json.loads(pathlib.Path(excluded_locators).read_text(encoding="utf-8"))["locators"]
        )
        typer.echo(f"  sensitivity exclusions loaded: {len(excluded):,} locators")

    agg = aggregate(
        work_dir / "shards-a",
        work_dir / "shards-b",
        shards,
        threshold=threshold,
        excluded_locators=excluded,
        pair_detail_path=work_dir / f"pairs-pki{threshold:g}.jsonl",
    )
    body = agg.as_dict()
    if not body["increment_observation_accounting"]["reconciles"]:
        raise _fail("increment observations do not reconcile; refusing to report eligibility")
    if not agg.training_unchanged_by_b:
        raise _fail("A's training labels differ when B is present; refusing to report")

    typer.echo(f"  KI observations: A {agg.a_ki_rows:,}  B {agg.b_ki_rows:,}")
    typer.echo(
        f"  pairs with KI evidence: {agg.pairs_seen:,} ({agg.historical_pairs:,} present in A)"
    )
    typer.echo("  A's training evidence unchanged by B: yes")
    for name in ("primary", "cross_slot_sensitivity"):
        arm = body["arms"][name]
        cb = arm["class_balance_among_eligible"]
        rank = arm["per_target_rankability"]["rankable_at"]
        typer.echo(
            f"  [{name}] eligible {arm['eligible_pairs']:,} pairs, "
            f"{arm['coverage']['distinct_targets_among_eligible']:,} targets, "
            f"rankable@5 {rank['5']:,}, positive rate {cb['positive_rate']}"
        )
        typer.echo(f"      excluded: {arm['excluded_pairs_by_reason']}")
    out = work_dir / f"ki-aggregation-pki{threshold:g}.json"
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(f"  {agg.seconds:,.0f}s, peak RSS {body['resources']['peak_rss_gb']} GB")
    typer.echo(f"  wrote {out}")


@env_app.command("check")
def env_check() -> None:
    """Report the toolchain this machine will actually run the pipeline on."""
    typer.echo(f"python     {sys.version.split()[0]}")
    typer.echo(f"platform   {platform.platform()}  ({platform.machine()})")

    try:
        import torch

        backend = "mps" if torch.backends.mps.is_available() else "cpu"
        typer.echo(f"torch      {torch.__version__}  backend={backend}")
    except Exception as exc:  # noqa: BLE001 - diagnostics must never crash
        typer.secho(f"torch      unavailable: {exc}", fg=typer.colors.YELLOW)

    try:
        import rdkit
        from rdkit import Chem

        smiles = Chem.MolToSmiles(Chem.MolFromSmiles("CN=C=O"))
        typer.echo(f"rdkit      {rdkit.__version__}  canonical-smiles-check={smiles}")
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"rdkit      unavailable: {exc}", fg=typer.colors.YELLOW)

    typer.echo(f"db target  {DbConfig.from_env().describe()}")


@ingest_app.command("header")
def ingest_header(subset: str = typer.Option("pdspki", help="Registered subset name.")) -> None:
    """Download if needed and print the TSV header — no database required."""
    from seq2lead import ingest as ing

    source = ing.get(subset)
    result = ing.fetch(source)
    header = ing.read_header(result.path, source.archive_member)
    ing.validate_header(header, source.required_columns)
    typer.echo(f"{len(header)} columns in {source.archive_member}")
    for i, name in enumerate(header, start=1):
        typer.echo(f"{i:4d}  {name}")


def _acquire(subset: str):
    """Resolve a subset, download it, and verify it against the frozen manifest."""
    from seq2lead import ingest as ing

    try:
        source = ing.get(subset)
    except KeyError as exc:
        raise _fail(str(exc)) from exc

    # Refuse BEFORE downloading. `ingest()` also refuses, but by then the bytes are
    # already on disk — pointless for a 566 MB archive we have decided not to trust.
    if not source.is_pinned:
        raise _fail(
            f"{source.source_name} {source.version}/{source.subset} has no "
            "expected_sha256 in the source manifest, so it will not be downloaded "
            "for loading.\n"
            f"Run:  seq2lead ingest inspect --subset {source.subset}\n"
            "then record the observed digest as expected_sha256 in "
            "src/seq2lead/ingest/sources.py and re-run this command."
        )

    try:
        result = ing.fetch(source)
    except ing.ChecksumMismatch as exc:
        raise _fail(str(exc)) from exc
    return source, result


def _load(source, result):
    """Ingest, timing the whole thing including the commit."""
    from seq2lead import ingest as ing
    from seq2lead.db import transaction

    started = time.perf_counter()
    try:
        with transaction() as conn:
            report = ing.ingest(conn, source, result)
    except (ing.ReleaseConflict, ing.HeaderError, ing.UnpinnedSource) as exc:
        raise _fail(str(exc)) from exc
    elapsed = time.perf_counter() - started

    if not report.was_noop:
        # Measured after the commit returned, so it includes it.
        with transaction() as conn:
            ing.record_total_seconds(conn, report.source_release_id, elapsed)
        report.total_seconds = elapsed
    return report


@ingest_app.command("inspect")
def ingest_inspect(
    subset: str = typer.Option(..., help="Registered subset name."),
    sample: int = typer.Option(2, help="Data rows to preview."),
) -> None:
    """Download an artifact and examine it WITHOUT writing to the database.

    This is the sanctioned route for an unpinned source: it reports the digest to
    freeze in the manifest. It cannot write a row.
    """
    from seq2lead import ingest as ing

    try:
        source = ing.get(subset)
    except KeyError as exc:
        raise _fail(str(exc)) from exc
    try:
        result = ing.fetch(source)
    except ing.ChecksumMismatch as exc:
        raise _fail(str(exc)) from exc

    info = ing.inspect(source, result, sample=sample)

    typer.echo(f"url            {source.url}")
    typer.echo(f"bytes          {result.archive_bytes:,}")
    typer.echo(f"sha256         {result.sha256}")
    typer.echo(
        f"md5            {result.md5} "
        f"({'matches publisher' if result.md5_verified else 'no published md5'})"
    )
    if info.pin_matches is None:
        typer.secho(
            "manifest pin   ABSENT — record the sha256 above as expected_sha256",
            fg=typer.colors.YELLOW,
        )
    elif info.pin_matches:
        typer.secho("manifest pin   matches", fg=typer.colors.GREEN)
    else:
        raise _fail("manifest pin   MISMATCH — refusing to treat this as the pinned artifact")

    typer.echo("")
    for m in info.members:
        typer.echo(f"member         {m.name}  {m.uncompressed_bytes:,} B uncompressed")
    if info.members and source.kind != "fasta":
        typer.echo(f"compression    {info.compression_ratio:.2f}x")

    if info.fasta_records is not None:
        typer.echo(f"fasta records  {info.fasta_records:,}")
        typer.echo(f"first header   {(info.fasta_first_header or '')[:100]}")
    if info.header is not None:
        typer.echo(f"columns        {len(info.header)}")
        for i, name in enumerate(info.header, start=1):
            typer.echo(f"  {i:4d}  {name}")
        for row in info.sample_rows:
            preview = " | ".join(v[:40] for v in row[: min(8, len(row))])
            typer.echo(f"  sample: {preview}")


@ingest_app.command("bindingdb")
def ingest_bindingdb(
    subset: str = typer.Option("pdspki", help="Registered subset name."),
) -> None:
    """Download, verify and load a pinned BindingDB subset into the raw layer."""
    source, result = _acquire(subset)
    typer.echo(f"source   {source.filename}")
    typer.echo(
        f"sha256   {result.sha256} "
        f"({'matches frozen manifest pin' if result.sha256_pinned else 'NOT PINNED in manifest'})"
    )
    typer.echo(
        f"md5      {result.md5} "
        f"({'matches publisher' if result.md5_verified else 'no published md5'})"
    )
    typer.echo(
        f"bytes    {result.archive_bytes:,}"
        f"{'  (reused existing download)' if result.reused_existing else ''}"
    )

    report = _load(source, result)
    typer.echo("")

    if report.was_noop:
        typer.secho(
            f"release {report.source_release_id} already loaded from identical bytes — "
            "verified no-op, nothing written",
            fg=typer.colors.GREEN,
        )
        typer.echo(f"rows     {report.loaded:,} loaded, {report.excluded:,} excluded")
        return

    typer.echo(f"release  {report.source_release_id}")
    typer.echo(f"columns  {report.column_count:,}")
    typer.echo(f"lines    {report.data_lines:,}")
    typer.echo(f"loaded   {report.loaded:,}")
    typer.echo(f"excluded {report.excluded:,}")
    typer.echo(f"copy     {report.copy_seconds:,.2f}s ({report.rows_per_second:,.0f} rows/s)")
    typer.echo(f"indexes  {report.index_seconds_total:,.2f}s")
    typer.echo(f"total    {report.total_seconds:,.2f}s (incl. commit)")
    if report.reconciles:
        typer.secho("reconciled: loaded + excluded == data lines", fg=typer.colors.GREEN)
    else:
        raise _fail("RECONCILIATION FAILED")


@ingest_app.command("m2")
def ingest_m2(
    subsets: str = typer.Option(
        "", help="Comma-separated subsets. Default: the full M2 set, in order."
    ),
) -> None:
    """Load the full M2 artifact set into the raw layer, measuring as it goes.

    Loads only. No curation, no VACUUM FULL, no embedding cache.
    """
    from seq2lead import ingest as ing
    from seq2lead.db import connect, transaction
    from seq2lead.db.migrations import apply_pending
    from seq2lead.profiling import m2_report
    from seq2lead.profiling import storage as storage_mod
    from seq2lead.profiling import wal as wal_mod

    names = [s.strip() for s in subsets.split(",") if s.strip()] or list(ing.M2_SUBSETS)

    with transaction() as conn:
        apply_pending(conn)

    outcomes: list[m2_report.ArtifactOutcome] = []
    for name in names:
        source, result = _acquire(name)
        typer.secho(f"\n=== {name} ===", bold=True)
        typer.echo(f"  sha256 {result.sha256}  ({result.archive_bytes:,} bytes)")

        loader = (
            ing.ingest_fasta
            if source.kind == "fasta"
            else (ing.ingest if name == "all" else ing.ingest_tsv)
        )

        with connect() as probe:
            before = wal_mod.snapshot(probe)

        started = time.perf_counter()
        try:
            with transaction() as conn:
                report = loader(conn, source, result)
        except (ing.ReleaseConflict, ing.HeaderError, ing.UnpinnedSource) as exc:
            raise _fail(str(exc)) from exc
        elapsed = time.perf_counter() - started

        with connect() as probe:
            after = wal_mod.snapshot(probe)
            wal_delta = wal_mod.delta(probe, before, after)

        if not report.was_noop:
            with transaction() as conn:
                ing.record_total_seconds(conn, report.source_release_id, elapsed)
                conn.execute(
                    "UPDATE source_release SET wal_bytes=%s, wal_records=%s, wal_fpi=%s, "
                    "checkpoints_timed=%s, checkpoints_requested=%s WHERE id=%s",
                    (
                        wal_delta.wal_bytes,
                        wal_delta.wal_records,
                        wal_delta.wal_fpi,
                        wal_delta.checkpoints_timed,
                        wal_delta.checkpoints_requested,
                        report.source_release_id,
                    ),
                )
        else:
            with connect() as probe:
                stored = probe.execute(
                    "SELECT wal_bytes, wal_records, wal_fpi, checkpoints_timed, "
                    "checkpoints_requested FROM source_release WHERE id=%s",
                    (report.source_release_id,),
                ).fetchone()
            if stored and stored[0] is not None:
                wal_delta = wal_mod.WalDelta(
                    wal_bytes=int(stored[0]),
                    wal_records=int(stored[1] or 0),
                    wal_fpi=int(stored[2] or 0),
                    checkpoints_timed=int(stored[3] or 0),
                    checkpoints_requested=int(stored[4] or 0),
                    buffers_checkpoint=0,
                )
            else:
                # Loaded before WAL accounting existed: report nothing rather than
                # the ~0 delta of this no-op re-run, which would be a lie.
                wal_delta = None

        with connect() as probe:
            rules = dict(
                probe.execute(
                    "SELECT rule_code, count(*) FROM ingest_exclusion "
                    "WHERE source_release_id = %s GROUP BY rule_code",
                    (report.source_release_id,),
                ).fetchall()
            )
            # Enough detail to say what a quarantined record *is*, not just that
            # one existed. all-NUL is the signature worth calling out.
            detail = [
                (int(r[0]), str(r[1]), int(r[2]), bool(r[3]))
                for r in probe.execute(
                    "SELECT line_no, rule_code, byte_length, "
                    # A NUL cannot appear in a text literal, so trim NUL bytes off
                    # both ends and ask whether anything is left.
                    "       (byte_length > 0 AND "
                    "        octet_length(btrim(raw_bytes, '\\x00'::bytea)) = 0) AS all_nul "
                    "FROM ingest_exclusion WHERE source_release_id = %s "
                    "ORDER BY line_no LIMIT 50",
                    (report.source_release_id,),
                ).fetchall()
            ]
            distinct_keys = None
            if name == "all":
                row = probe.execute(
                    "SELECT count(DISTINCT payload ->> 'BindingDB Reactant_set_id') "
                    "FROM raw_measurement WHERE source_release_id = %s",
                    (report.source_release_id,),
                ).fetchone()
                distinct_keys = int(row[0]) if row else None

        status = "verified no-op" if report.was_noop else f"{elapsed:,.1f}s"
        typer.echo(
            f"  release {report.source_release_id}: {report.loaded:,} loaded, "
            f"{report.excluded:,} quarantined, {status}"
        )
        if not report.reconciles:
            raise _fail(f"RECONCILIATION FAILED for {name}")

        outcomes.append(
            m2_report.ArtifactOutcome(
                source=source,
                download=result,
                report=report,
                wal=wal_delta,
                exclusions_by_rule={str(k): int(v) for k, v in rules.items()},
                quarantine_detail=detail,
                distinct_keys=distinct_keys,
                total_seconds=elapsed if not report.was_noop else report.total_seconds,
            )
        )

    typer.secho("\n=== storage (allocated; no VACUUM FULL) ===", bold=True)
    storage: dict[str, object] = {}
    with connect() as conn:
        for relation in ("raw_measurement", "raw_record", "raw_sequence"):
            try:
                s = storage_mod.measure(conn, relation, compacted=False)
            except LookupError:
                continue
            storage[relation] = s
            typer.echo(
                f"  {relation:18} {s.rows:>10,} rows  "
                f"{s.total / 1024**3:>6.2f} GiB  {s.per_row(s.total):>6,.0f} B/row"
            )

    typer.secho("\n=== verification ===", bold=True)
    checks = _run_verification()
    for command, result in checks:
        typer.echo(f"  $ {command}\n    {result}")

    path = m2_report.write(m2_report.render(outcomes, storage, checks))
    typer.secho(f"\nwrote {path}", fg=typer.colors.GREEN)


@curate_app.command("assays")
def curate_assays() -> None:
    """Populate `assay` and `assay_link` from the two mapping artifacts."""
    from seq2lead.curate import build_assays
    from seq2lead.db import transaction

    with transaction() as conn:
        rel = dict(conn.execute("SELECT subset, id FROM source_release").fetchall())
        for need in ("assays", "rsid_eaids"):
            if need not in rel:
                raise _fail(f"release '{need}' is not loaded; run `seq2lead ingest m2` first")
        assays, links = build_assays(conn, rel["assays"], rel["rsid_eaids"])
        typer.echo(f"assays      {assays:,} source rows")
        typer.echo(f"assay_link  {links:,} reactant ids linked")
    typer.secho("done", fg=typer.colors.GREEN)


@curate_app.command("run")
def curate_run(
    subset: str = typer.Option("all", help="Raw release subset to curate."),
    batch_size: int = typer.Option(20_000, help="Rows per transaction."),
    workers: int = typer.Option(8, help="Processes for RDKit standardization."),
    max_batches: int = typer.Option(0, help="Stop after N batches (0 = run to completion)."),
) -> None:
    """Curate a raw release into compound/target/assay/publication/activity.

    Restartable: each batch commits its own progress marker, so an interrupted
    run resumes from the last committed row rather than starting over.
    """
    from seq2lead.curate import CURATOR_VERSION, curate_release
    from seq2lead.db import connect, transaction

    with connect() as conn:
        row = conn.execute("SELECT id FROM source_release WHERE subset=%s", (subset,)).fetchone()
        if row is None:
            raise _fail(f"release '{subset}' is not loaded")
        release_id = int(row[0])
        linked = conn.execute("SELECT count(*) FROM assay_link").fetchone()
        if not linked or linked[0] == 0:
            raise _fail("assay_link is empty; run `seq2lead curate assays` first")

    typer.echo(f"release  {release_id} ({subset})")
    typer.echo(f"curator  {CURATOR_VERSION}")

    started = time.perf_counter()

    def _progress(counters, last_id: int) -> None:
        rate = counters.rows_seen / max(time.perf_counter() - started, 1e-9)
        typer.echo(
            f"  seen {counters.rows_seen:>9,}  curated {counters.rows_curated:>9,}  "
            f"excluded {counters.rows_excluded:>7,}  activities {counters.activities:>9,}  "
            f"{rate:>7,.0f} rows/s  last_id {last_id:,}"
        )

    counters = curate_release(
        transaction,
        release_id,
        batch_size=batch_size,
        workers=workers,
        max_batches=max_batches or None,
        progress=_progress,
    )
    typer.echo("")
    typer.echo(f"rows seen        {counters.rows_seen:,}")
    typer.echo(f"rows curated     {counters.rows_curated:,}")
    typer.echo(f"rows excluded    {counters.rows_excluded:,}")
    typer.echo(f"activities       {counters.activities:,}")
    typer.echo(f"structures std.  {counters.standardized:,}")
    typer.echo(f"elapsed          {counters.seconds:,.1f}s")
    typer.secho("batch complete", fg=typer.colors.GREEN)


@curate_app.command("rebuild")
def curate_rebuild(
    subset: str = typer.Option("all", help="Raw release subset to rebuild."),
    yes: bool = typer.Option(False, "--yes", help="Confirm clearing the derived layer."),
) -> None:
    """Clear and rebuild the derived M3 layer from the pinned raw release.

    For use when an identity fix changes compound or target ids, so the layer has
    to be regenerated rather than patched. **Raw releases are never touched** --
    the rebuild reads the same checksummed bytes.
    """
    from seq2lead.curate import clear_derived
    from seq2lead.db import connect, transaction

    with connect() as conn:
        row = conn.execute("SELECT id FROM source_release WHERE subset=%s", (subset,)).fetchone()
        if row is None:
            raise _fail(f"release '{subset}' is not loaded")
        release_id = int(row[0])
    if not yes:
        raise _fail("refusing without --yes: this clears the whole derived M3 layer")

    with transaction() as conn:
        for table, n in clear_derived(conn, release_id).items():
            typer.echo(f"  cleared {n:>10,} rows from {table}")
    typer.secho("derived layer cleared; run `seq2lead curate run`", fg=typer.colors.GREEN)


@curate_app.command("finalize")
def curate_finalize() -> None:
    """Summarize target annotations after a curation run."""
    from seq2lead.curate import finalize_targets
    from seq2lead.db import transaction

    with transaction() as conn:
        stats = finalize_targets(conn)
    for k, v in stats.items():
        typer.echo(f"  {k:<20} {v:,}")
    typer.secho("done", fg=typer.colors.GREEN)


@curate_app.command("index")
def curate_index() -> None:
    """Build the activity indexes after curation."""
    from seq2lead.curate import build_indexes
    from seq2lead.db import transaction

    with transaction() as conn:
        for name, secs in build_indexes(conn).items():
            typer.echo(f"  {name:<28} {secs:,.2f}s")
    typer.secho("done", fg=typer.colors.GREEN)


@curate_app.command("report")
def curate_report(
    subset: str = typer.Option("all", help="Curated release subset."),
) -> None:
    """Write reports/curation.md from the curated layer."""
    from seq2lead.db import connect
    from seq2lead.profiling import curation_report

    with connect() as conn:
        row = conn.execute("SELECT id FROM source_release WHERE subset=%s", (subset,)).fetchone()
        if row is None:
            raise _fail(f"release '{subset}' is not loaded")
        content = curation_report.render(conn, int(row[0]))
    path = curation_report.write(content)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


@endpoint_app.command("build")
def endpoint_build(
    subset: str = typer.Option("all", help="Curated release subset."),
    name: str = typer.Option(..., help="Endpoint version name, e.g. ki-pki6-v1."),
    threshold: float = typer.Option(6.0, help="Predeclared classification threshold (pKi)."),
    discordance: float = typer.Option(1.0, help="pKi spread above which a pair is discordant."),
) -> None:
    """Build versioned Ki pair_regression and pair_label tables.

    The threshold is predeclared: it is fixed here, before any split exists and
    before any model is trained. Sensitivity versions at 7.0 and 8.0 are built as
    separate named versions, never by mutating this one.
    """
    from seq2lead.curate import CURATOR_VERSION
    from seq2lead.db import connect, transaction
    from seq2lead.endpoint import build_endpoint

    with connect() as conn:
        row = conn.execute("SELECT id FROM source_release WHERE subset=%s", (subset,)).fetchone()
        if row is None:
            raise _fail(f"release '{subset}' is not loaded")
        release_id = int(row[0])

    def _progress(counters) -> None:
        typer.echo(
            f"  pairs {counters.pairs:>9,}  regression {counters.regression_pairs:>9,}  "
            f"labels {counters.label_pairs:>9,}  discordant {counters.discordant:>7,}"
        )

    try:
        with transaction() as conn:
            endpoint_id, counters = build_endpoint(
                conn,
                release_id,
                name=name,
                threshold=threshold,
                discordance=discordance,
                curator_version=CURATOR_VERSION,
                progress=_progress,
            )
    except ValueError as exc:
        raise _fail(str(exc)) from exc

    typer.echo("")
    typer.echo(f"endpoint         {endpoint_id} ({name}) at pKi >= {threshold}")
    typer.echo(f"Ki activities in {counters.activities_in:,}")
    typer.echo(f"unusable value   {counters.unusable_magnitude:,}")
    typer.echo(f"pairs            {counters.pairs:,}")
    typer.echo(f"pair_regression  {counters.regression_pairs:,}")
    typer.echo(f"pair_label       {counters.label_pairs:,}")
    typer.echo(f"discordant       {counters.discordant:,}")
    typer.echo(f"labels           {counters.by_label}")
    typer.echo(f"statuses         {counters.by_status}")
    typer.echo(f"elapsed          {counters.seconds:,.1f}s")
    typer.secho("done", fg=typer.colors.GREEN)


@endpoint_app.command("index")
def endpoint_index() -> None:
    """Build indexes on the endpoint tables."""
    import time as _t

    from seq2lead.db import transaction
    from seq2lead.db.m4_schema import M4_INDEX_SQL

    with transaction() as conn:
        for nm, stmt in M4_INDEX_SQL.items():
            t0 = _t.perf_counter()
            conn.execute(stmt)
            typer.echo(f"  {nm:<32} {_t.perf_counter() - t0:,.2f}s")
    typer.secho("done", fg=typer.colors.GREEN)


@endpoint_app.command("report")
def endpoint_report(
    name: str = typer.Option(..., help="Endpoint version name to report on."),
) -> None:
    """Write reports/endpoint.md."""
    from seq2lead.db import connect
    from seq2lead.profiling import endpoint_report as report_mod

    with connect() as conn:
        row = conn.execute("SELECT id FROM endpoint_version WHERE name=%s", (name,)).fetchone()
        if row is None:
            raise _fail(f"endpoint '{name}' does not exist")
        content = report_mod.render(conn, int(row[0]))
    path = report_mod.write(content)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


@analyze_app.command("assay-variance")
def analyze_assay_variance(
    endpoint: str = typer.Option("ki-pki6-v2", help="Endpoint version name."),
    allow_superseded: bool = typer.Option(
        False, help="Analyse a superseded endpoint anyway. Off by default for a reason."
    ),
) -> None:
    """M5: does Ki pool across assays? Writes reports/assay_variance.md.

    Read-only. Builds no splits, trains nothing, and alters no M4 data.
    """
    from seq2lead.analysis.assay_variance import SupersededEndpoint
    from seq2lead.db import connect
    from seq2lead.profiling import variance_report

    with connect() as conn:
        try:
            content = variance_report.render(conn, endpoint)
        except SupersededEndpoint as exc:
            raise _fail(str(exc)) from exc
        except LookupError as exc:
            raise _fail(str(exc)) from exc
    path = variance_report.write(content)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


def _endpoint_id(conn, name: str) -> int:
    from seq2lead.analysis.assay_variance import SupersededEndpoint, resolve_endpoint

    try:
        endpoint_id, _, _ = resolve_endpoint(conn, name)
    except (SupersededEndpoint, LookupError) as exc:
        raise _fail(str(exc)) from exc
    return endpoint_id


@split_app.command("cluster-targets")
def split_cluster_targets(
    identity: float = typer.Option(0.40, help="MMseqs2 minimum sequence identity."),
) -> None:
    """Cluster target sequences for cold_protein."""
    from seq2lead.db import transaction
    from seq2lead.splits import clustering

    if not clustering.mmseqs_available():
        raise _fail("mmseqs is not installed. `brew install mmseqs2` and retry.")
    with transaction() as conn:
        seqs = {
            int(r[0]): str(r[1]) for r in conn.execute("SELECT id, sequence FROM target").fetchall()
        }
        typer.echo(f"clustering {len(seqs):,} sequences at {identity:.0%} identity …")
        membership = clustering.cluster_sequences(seqs, identity=identity)
        n = clustering.store_target_clusters(conn, membership, identity)
    clusters = len(set(membership.values()))
    typer.echo(f"  {n:,} targets -> {clusters:,} clusters")
    typer.secho("done", fg=typer.colors.GREEN)


@split_app.command("cluster-compounds")
def split_cluster_compounds(
    workers: int = typer.Option(8, help="Processes for scaffold computation."),
    endpoint: str = typer.Option("ki-pki6-v2", help="Restrict to this endpoint's compounds."),
) -> None:
    """Compute Bemis-Murcko scaffolds for chemistry_disjoint."""
    from seq2lead.db import connect, transaction
    from seq2lead.splits import clustering

    with connect() as conn:
        endpoint_id = _endpoint_id(conn, endpoint)
        rows = conn.execute(
            "SELECT DISTINCT c.id, c.canonical_smiles FROM compound c "
            "JOIN pair_label l ON l.compound_id = c.id WHERE l.endpoint_id=%s",
            (endpoint_id,),
        ).fetchall()
    compounds = {int(r[0]): str(r[1]) for r in rows}
    typer.echo(f"computing scaffolds for {len(compounds):,} compounds …")
    membership = clustering.cluster_compounds(compounds, workers=workers)
    with transaction() as conn:
        n = clustering.store_compound_clusters(conn, membership)
    typer.echo(f"  {n:,} compounds -> {len(set(membership.values())):,} scaffolds")
    typer.secho("done", fg=typer.colors.GREEN)


@split_app.command("build")
def split_build(
    kind: str = typer.Option(
        ..., help="random_pair|cold_protein|chemistry_disjoint|label_reversal|temporal_proxy|all"
    ),
    endpoint: str = typer.Option("ki-pki6-v2", help="Endpoint version name."),
    prefix: str = typer.Option("v1", help="Suffix for the split name."),
    seed: int = typer.Option(20260929, help="Seed for reproducible assignment."),
) -> None:
    """Build one split (or all five) and run its leakage assertions."""
    from seq2lead.db import connect, transaction
    from seq2lead.splits import assertions, build

    builders = {
        "random_pair": build.build_random_pair,
        "cold_protein": build.build_cold_protein,
        "chemistry_disjoint": build.build_chemistry_disjoint,
        "label_reversal": build.build_label_reversal,
        "temporal_proxy": build.build_temporal_proxy,
    }
    kinds = list(builders) if kind == "all" else [kind]
    for name in kinds:
        if name not in builders:
            raise _fail(f"unknown split kind {name!r}. Known: {', '.join(builders)}")

    with connect() as conn:
        endpoint_id = _endpoint_id(conn, endpoint)

    for name in kinds:
        split_name = f"{name}-{prefix}"
        typer.secho(f"\n=== {split_name} ===", bold=True)
        builder = builders[name]
        try:
            with transaction() as conn:
                kwargs = {} if name == "temporal_proxy" else {"seed": seed}
                counters = builder(conn, endpoint_id, split_name, **kwargs)
        except (ValueError, RuntimeError) as exc:
            raise _fail(str(exc)) from exc

        typer.echo(f"  pairs      {counters.pairs:,}")
        for partition in ("train", "validation", "test", "excluded"):
            n = counters.by_partition.get(partition, 0)
            if n:
                typer.echo(f"    {partition:<11} {n:>9,}")
        if counters.groups:
            typer.echo(f"  groups     {counters.groups:,}")
        if counters.activities:
            typer.echo(f"  activities {counters.activities:,}")
        for stratum, n in sorted(counters.by_stratum.items()):
            typer.echo(f"    stratum {stratum:<9} {n:>9,}")

        with connect() as conn:
            try:
                checks = assertions.assert_no_leakage(conn, counters.split_id)
            except assertions.LeakageError as exc:
                raise _fail(str(exc)) from exc
        typer.secho(f"  {len(checks)} leakage assertions passed", fg=typer.colors.GREEN)


@split_app.command("report")
def split_report(
    endpoint: str = typer.Option("ki-pki6-v2", help="Endpoint version name."),
    similarity: bool = typer.Option(
        True, help="Measure nearest-train similarity for the cold splits (slow)."
    ),
) -> None:
    """Write reports/splits.md."""
    from seq2lead.db import connect
    from seq2lead.profiling import split_report as report_mod
    from seq2lead.splits import similarity as sim

    with connect() as conn:
        endpoint_id = _endpoint_id(conn, endpoint)
        distributions = []
        if similarity:
            for split_type, fn in (
                ("cold_protein", sim.target_identity_to_train),
                ("chemistry_disjoint", sim.compound_tanimoto_to_train),
            ):
                row = conn.execute(
                    "SELECT id FROM split_version WHERE split_type=%s AND endpoint_id=%s "
                    "AND superseded_by IS NULL ORDER BY id DESC LIMIT 1",
                    (split_type, endpoint_id),
                ).fetchone()
                if row is None:
                    continue
                typer.echo(f"  measuring nearest-train similarity for {split_type}...")
                distributions.append(fn(conn, int(row[0])))
        audit = None
        if similarity:
            row = conn.execute(
                "SELECT id FROM split_version WHERE split_type='cold_protein' "
                "AND endpoint_id=%s AND superseded_by IS NULL ORDER BY id DESC LIMIT 1",
                (endpoint_id,),
            ).fetchone()
            if row is not None:
                typer.echo("  auditing high-identity cross-partition hits...")
                hits = sim.high_identity_audit(conn, int(row[0]))
                held = sim.partition_target_count(conn, int(row[0]))
                audit = (hits, held)
        content = report_mod.render(conn, endpoint_id, distributions, audit)
    path = report_mod.write(content)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


feature_app = typer.Typer(help="M7 feature caches.")
app.add_typer(feature_app, name="features")


@feature_app.command("build")
def features_build(
    kind: str = typer.Option(..., help="ecfp4|esm2|activity|all"),
    endpoint: str = typer.Option("ki-pki6-v2", help="Endpoint version name."),
    split: str = typer.Option("temporal_proxy-v4", help="Split for activity features."),
    population: str = typer.Option(
        "release:117", help="Declared input population, e.g. release:117 or all_rows."
    ),
    device: str = typer.Option("", help="Force a torch device (default: auto)."),
    chirality: bool = typer.Option(True, help="Include stereochemistry in fingerprints."),
    length_policy: str = typer.Option("full", help="full | truncate:<n>"),
    limit: int = typer.Option(0, help="Cap entities. Produces a PARTIAL cache."),
    force: bool = typer.Option(False, help="Rebuild even if a valid cache exists."),
) -> None:
    """Build content-addressed feature caches, reusing any that already match."""
    from seq2lead.curate import CURATOR_VERSION
    from seq2lead.db import connect, transaction
    from seq2lead.features import BUILDER_VERSION, activity, ecfp, plm, store, validate

    kinds = ["ecfp4", "esm2", "activity"] if kind == "all" else [kind]
    cap = limit or None

    for name in kinds:
        typer.secho(f"\n=== {name} ===", bold=True)

        # ---- reuse check, BEFORE any fingerprinting or model load
        with connect() as conn:
            if name == "ecfp4":
                spec = ecfp.ecfp_spec(CURATOR_VERSION, chirality=chirality)
                from seq2lead.features.manifest import build_manifest

                manifest, expected = build_manifest(conn, "compound", population, limit=cap)
            elif name == "esm2":
                spec = plm.esm_spec(length_policy=length_policy)
                from seq2lead.features.manifest import build_manifest

                manifest, expected = build_manifest(conn, "target", population, limit=cap)
            else:
                spec = manifest = expected = None

            if spec is not None and manifest is not None and not force:
                hit = store.find_reusable(conn, spec, manifest, require_full=cap is None)
                if hit is not None:
                    typer.secho(
                        f"  reusing {hit[1]} (feature_version {hit[0]}) -- identical spec "
                        "and identical inputs, nothing recomputed",
                        fg=typer.colors.GREEN,
                    )
                    continue

        # ---- build
        with connect() as conn:
            if name == "ecfp4":
                built, manifest, expected = ecfp.build_compound_features(
                    conn, CURATOR_VERSION, population, chirality=chirality, limit=cap
                )
            elif name == "esm2":
                built, manifest, expected = plm.build_target_features(
                    conn,
                    population,
                    device=device or None,
                    length_policy=length_policy,
                    limit=cap,
                )
            elif name == "activity":
                split_id = int(
                    conn.execute("SELECT id FROM split_version WHERE name=%s", (split,)).fetchone()[
                        0
                    ]
                )
                activity.assert_no_held_out_activity_in_support(conn, split_id)
                built, manifest, expected = activity.build_entity_activity_features(
                    conn, split_id, "target"
                )
            else:
                raise _fail(f"unknown feature kind {name!r}")

            try:
                validate(built, manifest, expected)
            except Exception as exc:
                raise _fail(f"cache failed validation, refusing to register: {exc}") from exc

        with transaction() as conn:
            split_id = None
            if name == "activity":
                split_id = int(
                    conn.execute("SELECT id FROM split_version WHERE name=%s", (split,)).fetchone()[
                        0
                    ]
                )
            feature_id = store.register(
                conn, built, builder_version=BUILDER_VERSION, split_id=split_id
            )
        typer.echo(f"  name        {built.name}")
        typer.echo(f"  population  {manifest.population}  ({manifest.completeness})")
        typer.echo(f"  manifest    {manifest.digest()[:16]}…")
        typer.echo(f"  entities    {built.ids.shape[0]:,}   dim {built.dim:,}")
        typer.echo(f"  seconds     {built.seconds:,.1f}")
        if built.flags:
            counts: dict[str, int] = {}
            for entries in built.flags.values():
                for flag, _ in entries:
                    counts[flag] = counts.get(flag, 0) + 1
            for flag, n in sorted(counts.items()):
                typer.echo(f"  flagged     {flag}: {n:,}")
        typer.secho(f"  registered as feature_version {feature_id}", fg=typer.colors.GREEN)


@feature_app.command("coverage")
def features_coverage(
    strict: bool = typer.Option(True, help="Exit non-zero if any scored entity is uncovered."),
) -> None:
    """Assert every scored entity in every active split has a feature vector."""
    from seq2lead.db import connect
    from seq2lead.features.coverage import CoverageError, assert_feature_coverage, check_all_splits

    with connect() as conn:
        checks = check_all_splits(conn)
        typer.echo(
            f"  {'split':<24} {'entity':<9} {'scored':>9} {'covered':>9} "
            f"{'missing':>8} {'unusable':>9}"
        )
        for check in checks:
            mark = "" if check.passed else "   <-- FAIL"
            typer.echo(
                f"  {check.split:<24} {check.entity:<9} {check.scored:>9,} "
                f"{check.covered:>9,} {check.missing:>8,} {check.flagged:>9,}{mark}"
            )
        if strict:
            try:
                assert_feature_coverage(conn)
            except CoverageError as exc:
                raise _fail(str(exc)) from exc
    typer.secho(
        f"  {len(checks)} coverage checks passed across {len({c.split for c in checks})} splits",
        fg=typer.colors.GREEN,
    )


@feature_app.command("probe")
def features_probe(
    which: str = typer.Option("all", help="length|drift|similarity|all"),
    device: str = typer.Option("", help="Force a torch device (default: auto)."),
    n_targets: int = typer.Option(16, help="Targets for the prefix-drift probe."),
    n_pairs: int = typer.Option(4000, help="Pairs per side for the similarity probe."),
    seed: int = typer.Option(20260929, help="Selects which entities are measured."),
) -> None:
    """Run the embedding probes and write their artifacts to reports/probes/."""
    from seq2lead.db import connect
    from seq2lead.features import probes

    wanted = ["length", "drift", "similarity", "chirality"] if which == "all" else [which]
    with connect() as conn:
        for name in wanted:
            typer.secho(f"\n=== probe: {name} ===", bold=True)
            if name == "length":
                artifact = probes.probe_length_feasibility(conn, device=device or None)
            elif name == "drift":
                artifact = probes.probe_prefix_drift(
                    conn, n_targets=n_targets, seed=seed, device=device or None
                )
            elif name == "similarity":
                row = conn.execute(
                    "SELECT name FROM feature_version WHERE kind='esm2' "
                    "AND superseded_by IS NULL AND completeness='full' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    raise _fail("no complete esm2 cache; build it before this probe")
                artifact = probes.probe_cluster_similarity(
                    conn, str(row[0]), n_pairs=n_pairs, seed=seed
                )
            elif name == "chirality":
                achiral = conn.execute(
                    "SELECT name FROM feature_version WHERE kind='ecfp4' "
                    "AND superseded_by IS NOT NULL ORDER BY id LIMIT 1"
                ).fetchone()
                chiral = conn.execute(
                    "SELECT name FROM feature_version WHERE kind='ecfp4' "
                    "AND superseded_by IS NULL AND completeness='full' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if achiral is None or chiral is None:
                    raise _fail("need both an achiral and a chiral ecfp4 cache")
                artifact = probes.probe_chirality_impact(conn, str(achiral[0]), str(chiral[0]))
            else:
                raise _fail(f"unknown probe {name!r}")
            path = artifact.write()
            typer.echo(f"  measurements {len(artifact.measurements):,}")
            if artifact.failures:
                typer.secho(f"  failures     {len(artifact.failures):,}", fg=typer.colors.YELLOW)
            typer.secho(f"  wrote {path}", fg=typer.colors.GREEN)


@feature_app.command("report")
def features_report(
    endpoint: str = typer.Option("ki-pki6-v2", help="Endpoint version name."),
    split: str = typer.Option("temporal_proxy-v4", help="Split for activity features."),
) -> None:
    """Write reports/features.md."""
    from seq2lead.db import connect
    from seq2lead.profiling import feature_report as report_mod

    with connect() as conn:
        endpoint_id = _endpoint_id(conn, endpoint)
        content = report_mod.render(conn, endpoint_id, split)
    path = report_mod.write(content)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


library_app = typer.Typer(help="Frozen candidate libraries.")
app.add_typer(library_app, name="library")


@library_app.command("build")
def library_build(
    name: str = typer.Option("curated-ki-25k-v1", help="Library version name."),
    release: int = typer.Option(117, help="Pinned source release id."),
    cap: int = typer.Option(25000, help="Member cap."),
) -> None:
    """Freeze a candidate library from the curated database."""
    from seq2lead.db import transaction
    from seq2lead.models import library as lib

    with transaction() as conn:
        built = lib.build(conn, release, name=name, cap=cap)
    typer.secho(f"library {built.name}", bold=True)
    typer.echo(f"  members      {built.n_members:,}")
    typer.echo(f"  digest       {built.member_sha256}")
    typer.echo(f"  rule         {built.selection_rule}")


@library_app.command("list")
def library_list() -> None:
    from seq2lead.db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT name, n_members, member_sha256, created_at FROM candidate_library ORDER BY id"
        ).fetchall()
    if not rows:
        typer.echo("  none built")
        return
    for name, members, digest, created in rows:
        typer.echo(f"  {name:<24} {int(members):>8,} members  {digest[:16]}…  {created:%Y-%m-%d}")


@app.command("rank")
def rank(
    sequence_file: str = typer.Option(..., help="FASTA file holding the query protein."),
    library: str = typer.Option("curated-ki-25k-v1", help="Frozen library version."),
    model: str = typer.Option(..., help="Dual-encoder checkpoint."),
    top_k: int = typer.Option(50, help="How many compounds to return."),
    evidence_mode: str = typer.Option(
        "evaluation", help="evaluation | demo. `demo` also shows prior measured evidence."
    ),
    device: str = typer.Option("", help="Force a torch device."),
    output: str = typer.Option("", help="Write the table here as well as stdout."),
) -> None:
    """Rank a frozen compound library against one protein sequence."""
    from seq2lead.db import connect
    from seq2lead.models.rank import SequenceError, rank_library, read_fasta, render

    if evidence_mode not in {"evaluation", "demo"}:
        raise _fail("evidence_mode must be 'evaluation' or 'demo'")
    try:
        header, sequence = read_fasta(pathlib.Path(sequence_file))
    except SequenceError as exc:
        raise _fail(str(exc)) from exc

    with connect() as conn:
        try:
            result = rank_library(
                conn,
                sequence,
                pathlib.Path(model),
                library,
                top_k=top_k,
                device=device or None,
                show_evidence=evidence_mode == "demo",
            )
        except (SequenceError, LookupError, ValueError) as exc:
            raise _fail(str(exc)) from exc

    if header:
        typer.echo(f"# {header}")
    text = render(result, evidence_mode)
    typer.echo(text)
    if output:
        destination = pathlib.Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((f"# {header}\n" if header else "") + text, encoding="utf-8")
        typer.secho(f"wrote {destination}", fg=typer.colors.GREEN)


m9_app = typer.Typer(help="M9 dual encoder.")
app.add_typer(m9_app, name="m9")


@m9_app.command("select")
def m9_select(
    experiment: str = typer.Option("m9-dual-encoder-v1", help="Experiment config."),
    seed: int = typer.Option(20260930, help="Seed for the selection fits."),
    label: str = typer.Option("default", help="Label scoping this selection's records."),
    selection_path: str = typer.Option(
        "", help="Where to freeze the selection (default: reports/results/m9_selection.json)."
    ),
    overwrite: bool = typer.Option(False, help="Discard an existing selection."),
) -> None:
    """Phase one: fit every declared configuration and select on validation only."""
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.eval.features import FeatureBank
    from seq2lead.models.m9_runner import SELECTION_PATH as SELECTION_PATH_DEFAULT
    from seq2lead.models.m9_runner import select, write_selection

    with connect() as conn:
        config = load_experiment(conn, experiment)
        typer.secho(f"{config.version}  status={config.status}", bold=True)
        for kind, ref in config.caches.items():
            typer.echo(f"  cache {kind:<7} {ref.name}  verified")
        bank = FeatureBank.load(conn, config)
        dims = tuple(config.search.get("projection_dim", ())) or (256, 512)
        typer.echo(f"  configurations: projection_dim in {list(dims)}")
        destination = pathlib.Path(selection_path) if selection_path else None
        try:
            record = select(
                conn,
                config,
                bank,
                dims,
                seed,
                progress=True,
                label=label,
                selection_path=destination,
                overwrite=overwrite,
            )
        except Exception as exc:
            raise _fail(str(exc)) from exc
    path = write_selection(record, destination or SELECTION_PATH_DEFAULT, overwrite=overwrite)

    typer.echo(f"\n  {'split':<24} {'h':>5} {'val RMSE':>9} {'epoch':>6} {'chosen':>7}")
    for entry in record.entries:
        mark = "yes" if entry.chosen else ""
        note = f"  (fallback from {entry.fallback_from})" if entry.fallback_from else ""
        typer.echo(
            f"  {entry.split:<24} {entry.projection_dim:>5} "
            f"{entry.validation_rmse:>9.4f} {entry.best_epoch:>6} {mark:>7}{note}"
        )
    typer.secho(f"\n  frozen selection written to {path}", fg=typer.colors.GREEN)
    typer.echo("  test partitions were not touched in this phase")


@m9_app.command("test")
def m9_test(
    experiment: str = typer.Option("m9-dual-encoder-v1", help="Experiment config."),
    results_version: str = typer.Option("m9/v1", help="Result version to publish."),
    seeds: str = typer.Option("", help="Override the config seed list."),
    allow_legacy: bool = typer.Option(
        False, help="Accept a selection frozen before the binding fields existed."
    ),
    selection_path: str = typer.Option(
        "", help="Which frozen selection to read (default: reports/results/m9_selection.json)."
    ),
) -> None:
    """Phase two: score the test partitions once, using the frozen selection."""
    from seq2lead.db import connect, transaction
    from seq2lead.eval import load_experiment
    from seq2lead.eval.features import FeatureBank
    from seq2lead.eval.results import publish
    from seq2lead.models.m9_runner import read_selection, score_test

    with connect() as conn:
        config = load_experiment(conn, experiment)
        selection = (
            read_selection(pathlib.Path(selection_path)) if selection_path else read_selection()
        )
        typer.echo(f"  selection: {selection_path or 'reports/results/m9_selection.json'}")
        dims = tuple(config.search.get("projection_dim", ())) or (256, 512)
        try:
            from dataclasses import asdict

            from seq2lead.models.m9_artifacts import check_selection_binding

            legacy_notes = check_selection_binding(
                asdict(selection), config, dims, allow_legacy=allow_legacy
            )
        except ValueError as exc:
            raise _fail(str(exc)) from exc
        for note in legacy_notes:
            typer.secho(f"  warning: {note}", fg=typer.colors.YELLOW)
        typer.secho(f"frozen selection from {selection.frozen_at}", bold=True)
        for split, dim in sorted(selection.chosen.items()):
            typer.echo(f"  {split:<24} projection_dim={dim}")
        bank = FeatureBank.load(conn, config)
        seed_list = tuple(int(s) for s in seeds.split(",")) if seeds else tuple(config.seeds)
        typer.echo(f"  seeds: {list(seed_list)}")
        # Built from the verified experiment and embedded in every checkpoint, so a
        # newly fitted model resolves its own binding without needing a sidecar.
        from seq2lead.models.binding import from_experiment

        binding = from_experiment(
            conn,
            config,
            provenance=f"recorded at fit time by `seq2lead m9 test` ({config.version})",
        )
        typer.echo(f"  binding: compound={binding.compound_cache} protein={binding.protein_cache}")
        results = score_test(
            conn, config, bank, selection, seed_list, progress=True, binding=binding
        )

    with transaction() as conn:
        config = load_experiment(conn, experiment)
        entry = publish(
            results,
            version=results_version,
            filename=f"{results_version.replace('/', '_')}_summary.json",
            metric_version="m8/v2",
            config=config,
            note="M9 dual encoder, exploratory comparison on inspected test sets",
        )
    typer.secho(
        f"\n  published {entry.version} -> {entry.filename} ({len(results)} runs)",
        fg=typer.colors.GREEN,
    )


eval_app = typer.Typer(help="M8 baselines and evaluation.")
app.add_typer(eval_app, name="eval")


@eval_app.command("run")
def eval_run(
    experiment: str = typer.Option("baseline-v1", help="Experiment config version."),
    models: str = typer.Option("all", help="Comma-separated model names, or 'all'."),
    splits: str = typer.Option("all", help="Comma-separated split names, or 'all'."),
    seeds: str = typer.Option("", help="Override the config seed list."),
    results_version: str = typer.Option(
        ..., help="Version to publish under, e.g. m8/v3. Required: results are named."
    ),
) -> None:
    """Fit the baseline suite and score the test partitions."""
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.eval.baselines import SUITE
    from seq2lead.eval.features import FeatureBank
    from seq2lead.eval.recompute import METRIC_VERSION as EVAL_METRIC_VERSION
    from seq2lead.eval.runner import run_one, write_results

    chosen_models = [c for c in SUITE if models == "all" or c.name in models.split(",")]
    if not chosen_models:
        raise _fail(f"no models matched {models!r}. Known: {[c.name for c in SUITE]}")

    with connect() as conn:
        config = load_experiment(conn, experiment)
        typer.secho(f"experiment {config.version}  status={config.status}", bold=True)
        for kind, ref in config.caches.items():
            typer.echo(f"  cache {kind:<7} {ref.name}  digests verified")
        bank = FeatureBank.load(conn, config)

        wanted = [s.name for s in config.splits]
        if splits != "all":
            wanted = [s for s in wanted if s in splits.split(",")]
        seed_list = [int(s) for s in seeds.split(",")] if seeds else list(config.seeds)

        results = []
        for split_name in wanted:
            for model_cls in chosen_models:
                probe = model_cls()
                run_seeds = [seed_list[0]] if probe.deterministic else seed_list
                for seed in run_seeds:
                    label = "deterministic" if probe.deterministic else f"seed {seed}"
                    typer.echo(f"\n  {probe.name} / {split_name} / {label}")
                    result = run_one(conn, config, bank, model_cls, split_name, seed)
                    results.append(result)
                    auroc = result.ranking["auroc"]["mean"]
                    rmse = result.regression["rmse"]["mean"]
                    typer.echo(
                        f"    fit {result.fit_seconds:7.1f}s  "
                        f"AUROC {auroc:.4f}  RMSE {rmse:.3f}  "
                        f"targets {result.ranking['n_targets_scored']:,}"
                    )
        full, brief = write_results(
            results, config, version=results_version, metric_version=EVAL_METRIC_VERSION
        )
    typer.secho(
        f"\npublished {results_version}: {brief} ({len(results)} runs); full record {full}",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  render it with: uv run seq2lead eval report --results-version {results_version}")


@eval_app.command("recompute")
def eval_recompute(
    experiment: str = typer.Option("baseline-v1", help="Experiment config version."),
    source_version: str = typer.Option(..., help="Result version to compare against."),
    target_version: str = typer.Option(..., help="Result version to publish."),
) -> None:
    """Recompute corrected metrics from **verified** saved predictions. Fits nothing."""
    from seq2lead.db import connect
    from seq2lead.eval import load_experiment
    from seq2lead.eval.recompute import METRIC_VERSION, recompute_all, write

    with connect() as conn:
        config = load_experiment(conn, experiment)
        typer.secho(f"recomputing {config.version} at metric version {METRIC_VERSION}", bold=True)
        corrected, report = recompute_all(conn, config, source_version)
        for line in report.verification_checks:
            typer.echo(f"  verified  {line}")
    corrected_path, record_path = write(corrected, report, config, version=target_version)

    changed = report.changed()
    typer.echo(f"  runs recomputed      {report.n_runs}")
    typer.echo(f"  prediction files     {len(report.prediction_digests)}")
    typer.echo(f"  metric values moved  {len(changed)} of {len(report.comparisons)}")
    for metric in sorted(report.unchanged_metrics()):
        typer.echo(f"    unchanged: {metric}")
    moved = sorted({c.metric for c in changed})
    for metric in moved:
        deltas = [
            abs(c.new - c.old) for c in changed if c.metric == metric and None not in (c.new, c.old)
        ]
        if deltas:
            typer.echo(
                f"    changed:   {metric}  max |delta| {max(deltas):.6f}  "
                f"median {sorted(deltas)[len(deltas) // 2]:.6f}"
            )
    typer.secho(f"  wrote {corrected_path}", fg=typer.colors.GREEN)
    typer.secho(f"  wrote {record_path}", fg=typer.colors.GREEN)


@eval_app.command("report")
def eval_report(
    experiment: str = typer.Option("baseline-v1", help="Experiment config version."),
    results_version: str = typer.Option(
        ..., help="Which result version to render. Required: no fallback, no recency."
    ),
) -> None:
    """Write reports/leaderboard.md from one explicitly named result version."""
    from seq2lead.db import connect
    from seq2lead.eval import artifacts as artifact_mod
    from seq2lead.eval import load_experiment
    from seq2lead.profiling import leaderboard as report_mod

    with connect() as conn:
        config = load_experiment(conn, experiment)
        content = report_mod.render(conn, config, results_version)
        manifest = artifact_mod.render(conn, config)
    path = report_mod.write(content)
    manifest_path = artifact_mod.write(manifest)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)
    typer.secho(f"wrote {manifest_path}", fg=typer.colors.GREEN)


@profile_app.command("wal")
def profile_wal(
    subset: str = typer.Option("rsid_eaids", help="Pinned artifact to load as the probe."),
) -> None:
    """Measure WAL and checkpoint cost of a bulk COPY, attributably.

    Loads a real pinned artifact under a scratch release name, measures the
    cluster-wide WAL/checkpoint delta across it, then removes the scratch release.
    This exists because an immutable release cannot be re-loaded to measure it, so
    a comparable load is the honest substitute.
    """
    import dataclasses
    import time as _time

    from seq2lead.db import connect, transaction
    from seq2lead.ingest import auxiliary
    from seq2lead.profiling import wal as wal_mod

    source, result = _acquire(subset)
    scratch = dataclasses.replace(source, subset=f"{source.subset}__walprobe")

    with connect() as conn:
        before = wal_mod.snapshot(conn)
        max_wal = conn.execute("SHOW max_wal_size").fetchone()
    started = _time.perf_counter()
    with transaction() as conn:
        report = auxiliary.ingest_tsv(conn, scratch, result)
    elapsed = _time.perf_counter() - started
    with connect() as conn:
        after = wal_mod.snapshot(conn)
        d = wal_mod.delta(conn, before, after)
        payload = conn.execute(
            "SELECT coalesce(sum(pg_column_size(payload)),0) FROM raw_record "
            "WHERE source_release_id=%s",
            (report.source_release_id,),
        ).fetchone()
    payload_bytes = int(payload[0]) if payload else 0

    with transaction() as conn:
        conn.execute(
            "DELETE FROM raw_record WHERE source_release_id=%s", (report.source_release_id,)
        )
        conn.execute("DELETE FROM source_release WHERE id=%s", (report.source_release_id,))

    lines = [
        "# WAL and checkpoint cost of a bulk COPY",
        "",
        f"Probe artifact: `{source.subset}` ({report.loaded:,} rows), loaded under a "
        "scratch release and then removed.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Rows | {report.loaded:,} |",
        f"| Wall time | {elapsed:,.1f} s |",
        f"| WAL generated | {d.wal_bytes / 1024**2:,.1f} MiB |",
        f"| WAL records | {d.wal_records:,} |",
        f"| Full-page images | {d.wal_fpi:,} |",
        f"| WAL per row | {d.wal_bytes / max(report.loaded, 1):,.0f} B |",
        f"| Payload stored | {payload_bytes / 1024**2:,.1f} MiB |",
        f"| **WAL amplification** | **{d.wal_bytes / max(payload_bytes, 1):.2f}x** |",
        f"| Checkpoints, timed | {d.checkpoints_timed} |",
        f"| **Checkpoints, requested** | **{d.checkpoints_requested}** |",
        f"| `max_wal_size` | {max_wal[0] if max_wal else 'unknown'} |",
        "",
    ]
    lines += [
        "**Scope of this probe.** These figures characterise this load and no other. "
        "The artifact was chosen for convenient size, not because it resembles any "
        "particular production load, so its payload shape and checkpoint count must "
        "not be read across to a different ingest. Where another load's cost matters, "
        "measure that load: migration `0003_release_wal_accounting` records these "
        "counters per release.",
        "",
    ]
    if d.checkpoints_requested:
        lines += [
            f"**{d.checkpoints_requested} requested checkpoint(s)** occurred here. A "
            "requested checkpoint means WAL filled `max_wal_size` before the timed "
            "interval elapsed, so the cluster checkpointed under pressure. That is a "
            "reason to watch a comparable load, not by itself evidence that "
            "`max_wal_size` must be raised for one.",
            "",
            "What it does rule out is chunking the transaction: that would surrender "
            "the all-or-nothing guarantee and would not reduce WAL volume by a byte, "
            "since the same rows are written either way.",
            "",
        ]
    else:
        lines += ["No requested checkpoints: WAL did not outrun `max_wal_size`.", ""]
    lines += [
        "Caveat: `pg_stat_wal` and the checkpoint counters are cluster-wide. This is a "
        "dedicated development database with no concurrent workload, so the delta is "
        "attributable to the probe; on a shared cluster it would not be.",
        "",
    ]
    path = pathlib.Path("reports/wal_probe.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    typer.echo(
        f"rows {report.loaded:,}  WAL {d.wal_bytes / 1024**2:,.1f} MiB  "
        f"amplification {d.wal_bytes / max(payload_bytes, 1):.2f}x  "
        f"checkpoints req {d.checkpoints_requested}"
    )
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


@profile_app.command("report")
def profile_report(
    subset: str = typer.Option("pdspki", help="Registered subset name."),
    sequences: int = typer.Option(24, help="How many sequences to embed for timing."),
    skip_esm2: bool = typer.Option(False, help="Skip the embedding benchmark."),
    compact: bool = typer.Option(
        False,
        "--compact",
        help=(
            "VACUUM FULL before measuring. Takes an ACCESS EXCLUSIVE lock for the "
            "whole rewrite, needs free disk up to the size of the table plus its "
            "indexes, and on a full release takes minutes. Off by default; without "
            "it, sizes are ALLOCATED, not live."
        ),
    ),
) -> None:
    """Measure the loaded pilot and write reports/profile.md.

    Non-destructive: if the release is already loaded from identical bytes this
    reuses it and reports the timings recorded at that load.
    """
    from seq2lead import profiling
    from seq2lead.db import connect
    from seq2lead.profiling import storage as storage_mod

    source, result = _acquire(subset)
    report = _load(source, result)
    typer.echo(
        f"ingest   release {report.source_release_id}, {report.loaded:,} rows"
        f"{'  (verified no-op, not reloaded)' if report.was_noop else ''}"
    )

    cfg = DbConfig.from_env()
    if compact:
        typer.secho(
            "storage  VACUUM FULL takes an ACCESS EXCLUSIVE lock and rewrites the "
            "table — nothing else can read it meanwhile",
            fg=typer.colors.YELLOW,
        )
        store = storage_mod.compact_and_measure(cfg, "raw_measurement")
        reclaimed = store.reclaimed or 0
        typer.echo(
            f"storage  {store.per_row(store.total):,.0f} B/row live "
            f"({reclaimed / 1024 / 1024:,.1f} MiB reclaimed)"
        )
    else:
        with connect() as conn:
            store = storage_mod.measure(conn, "raw_measurement", compacted=False)
        typer.echo(
            f"storage  {store.per_row(store.total):,.0f} B/row ALLOCATED "
            f"({store.dead_tuples:,} dead tuples; pass --compact for live size)"
        )

    with connect() as conn:
        stats = profiling.collect(conn, report.source_release_id)
        lengths = profiling.sequence_length_stats(conn, report.source_release_id)
        sample = profiling.sample_sequences(conn, report.source_release_id, sequences)
    typer.echo(f"stats    {stats.rows:,} rows, {stats.distinct_sequences:,} distinct sequences")

    esm2_profile = None
    if not skip_esm2 and sample:
        from seq2lead.profiling import esm2 as esm2_mod

        typer.echo(
            f"esm2     embedding {len(sample)} sequences "
            "(weights ~2.6 GB, downloaded once then cached)"
        )
        esm2_profile = esm2_mod.measure(sample)
        typer.echo(
            f"esm2     {esm2_profile.sequences_per_second:,.2f} seq/s on {esm2_profile.device}"
        )

    path = profiling.write(
        profiling.render(
            source,
            result,
            report,
            stats,
            store,
            esm2_profile,
            lengths=lengths,
            total_ingest_seconds=report.total_seconds,
        )
    )
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


# ---------------------------------------------------------------- M10 docking

dock_app = typer.Typer(help="M10 docking validation gate.", no_args_is_help=True)
app.add_typer(dock_app, name="dock")


@dock_app.command("verify")
def dock_verify() -> None:
    """Check the contract, the engine identity and the pinned structure."""
    from seq2lead.dock.artifacts import sha256_file
    from seq2lead.dock.config import load_docking_config
    from seq2lead.dock.engine import VINA_BIN, verify_engine

    config = load_docking_config()
    typer.echo(f"contract   {config.version}  {config.config_sha256[:16]}…")
    typer.echo(f"selection  {config.selection_sha256()[:16]}…")
    typer.secho(f"engine     {verify_engine(config, VINA_BIN)}", fg=typer.colors.GREEN)
    structures = pathlib.Path("data/m10/structures")
    for name, expected in config.raw["structure"]["sha256"].items():
        path = structures / name
        if not path.exists():
            typer.secho(f"structure  {name}: MISSING", fg=typer.colors.RED)
            raise typer.Exit(1)
        actual = sha256_file(path)
        ok = actual == expected
        typer.secho(
            f"structure  {name}: {'ok' if ok else 'DIGEST MISMATCH'}  {actual[:16]}…",
            fg=typer.colors.GREEN if ok else typer.colors.RED,
        )
        if not ok:
            raise typer.Exit(1)


@dock_app.command("cohort")
def dock_cohort(
    path: str = typer.Option("", help="Where to freeze it (default: the contract's path)."),
    overwrite: bool = typer.Option(False, help="Replace an existing frozen cohort."),
) -> None:
    """Freeze the measured cohort. Must happen before any docking score exists."""
    from seq2lead.db import connect
    from seq2lead.dock.cohort import COHORT_PATH, build_cohort, write_cohort
    from seq2lead.dock.config import load_docking_config

    config = load_docking_config()
    destination = pathlib.Path(path) if path else COHORT_PATH
    with connect() as conn:
        cohort = build_cohort(conn, config)
    written = write_cohort(cohort, destination, overwrite=overwrite)
    typer.echo(f"  members    {len(cohort.members):,}")
    typer.echo(f"  breakdown  {cohort.evidence_breakdown}")
    typer.echo(f"  pool       {cohort.pool_active:,} active / {cohort.pool_inactive:,} inactive")
    typer.echo(f"  membership {cohort.membership_sha256()[:16]}…")
    typer.secho(f"froze {written}", fg=typer.colors.GREEN)


@dock_app.command("qa")
def dock_qa(
    receptor: str = typer.Option(..., help="Prepared receptor PDBQT."),
    resname: str = typer.Option("SUA", help="Reference ligand residue name."),
    smiles: str = typer.Option(..., help="Reference ligand SMILES."),
) -> None:
    """Redock the reference ligand. Pose recovery only -- never part of the gate."""
    import json

    from seq2lead.dock.config import load_docking_config
    from seq2lead.dock.qa import redock_reference
    from seq2lead.dock.receptor import residue_centroid

    config = load_docking_config()
    structure = (
        pathlib.Path("data/m10/structures")
        / f"{str(config.raw['structure']['pdb_id']).lower()}.pdb"
    )
    result = redock_reference(
        config,
        receptor_pdbqt=pathlib.Path(receptor),
        work_dir=pathlib.Path("data/m10/runs/qa-redock"),
        structure=structure,
        resname=resname,
        smiles=smiles,
        metal_point=residue_centroid(structure, "ZN"),
    )
    out = pathlib.Path("reports/results/m10_qa_redock.json")
    out.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    colour = typer.colors.GREEN if result.passed else typer.colors.YELLOW
    typer.secho(
        f"  top-pose RMSD {result.top_pose_rmsd} A  (threshold {result.threshold})", fg=colour
    )
    typer.echo(f"  {result.note}")
    typer.secho(f"wrote {out}", fg=typer.colors.GREEN)


@dock_app.command("run")
def dock_run(
    run_label: str = typer.Option(..., help="Run label; scopes every artifact."),
    workers: int = typer.Option(9, help="Ligands docked in parallel."),
    limit: int = typer.Option(0, help="Dock only the first N members (a pilot, not a gate)."),
    box_scale: float = typer.Option(1.0, help="Scale the box -- a SENSITIVITY run, labelled."),
    overwrite: bool = typer.Option(False, help="Replace an existing run."),
) -> None:
    """Dock the frozen cohort and apply the frozen gate rule."""
    import json

    from seq2lead.dock.config import load_docking_config
    from seq2lead.dock.runner import attrition_by_class, run_cohort

    config = load_docking_config()
    size = None
    if box_scale != 1.0:
        size = tuple(v * box_scale for v in config.box_size)
        typer.secho(
            f"SENSITIVITY RUN: box scaled x{box_scale} -> {size}. Not the primary result.",
            fg=typer.colors.YELLOW,
        )
    paths, gate, extra = run_cohort(
        config,
        run_label,
        workers=workers,
        limit=limit or None,
        box_size=size,
        overwrite=overwrite,
        progress=True,
    )
    colour = {"pass": typer.colors.GREEN, "fail": typer.colors.RED}.get(
        gate.decision, typer.colors.YELLOW
    )
    typer.secho(f"\n  GATE: {gate.decision.upper()}", fg=colour, bold=True)
    typer.echo(f"  {gate.reason}")
    typer.echo(f"  attrition: {json.dumps(attrition_by_class(paths.scores, paths.failures))}")
    typer.secho(f"wrote {paths.scores} and {paths.record}", fg=typer.colors.GREEN)


@dock_app.command("report")
def dock_report(
    sensitivity: str = typer.Option(
        "", help="Comma-separated label=path pairs for box-size sensitivity runs."
    ),
) -> None:
    """Render reports/docking.md from the recorded artifacts."""
    from seq2lead.dock.report import write

    extra: dict[str, pathlib.Path] = {}
    for pair in [x.strip() for x in sensitivity.split(",") if x.strip()]:
        label, _, target = pair.partition("=")
        extra[label.strip()] = pathlib.Path(target.strip())
    path = write(sensitivity_paths=extra or None)
    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)


@dock_app.command("freeze-manifest")
def dock_freeze_manifest(
    runs: str = typer.Option(..., help="Comma-separated run labels to record."),
    provenance: str = typer.Option(..., help="Why this manifest was frozen."),
) -> None:
    """Record every run's artifacts by path and digest, so drift is detectable."""
    import json

    from seq2lead.dock.artifacts import (
        MANIFEST_PATH,
        freeze_manifest,
        run_paths,
        sha256_file,
        sha256_tree,
        verify_manifest_scoped,
    )

    entries = []
    for label in [r.strip() for r in runs.split(",") if r.strip()]:
        paths = run_paths(label)
        if not paths.record.exists():
            typer.secho(f"  {label}: no run record at {paths.record}", fg=typer.colors.RED)
            raise typer.Exit(1)
        record = json.loads(paths.record.read_text(encoding="utf-8"))
        receptor = paths.root / "receptor.pdbqt"
        ligand_digest, ligand_n = sha256_tree(paths.ligand_dir)
        pose_digest, pose_n = sha256_tree(paths.pose_dir)
        entries.append(
            {
                "run": label,
                "experiment": record["experiment"],
                "config_sha256": record["config_sha256"],
                "engine_version": record["engine_version"],
                "engine_sha256": record["engine_sha256"],
                "receptor_pdbqt": str(receptor),
                "receptor_sha256": sha256_file(receptor),
                "cohort_path": record["cohort_path"],
                "cohort_sha256": sha256_file(pathlib.Path(record["cohort_path"])),
                "cohort_membership_sha256": record["cohort_sha256"],
                "scores_path": str(paths.scores),
                "scores_sha256": sha256_file(paths.scores),
                "failures_path": str(paths.failures),
                "failures_sha256": sha256_file(paths.failures),
                "record_path": str(paths.record),
                "record_sha256": sha256_file(paths.record),
                "ligand_dir": str(paths.ligand_dir),
                "ligand_dir_sha256": ligand_digest,
                "ligand_dir_files": ligand_n,
                "pose_dir": str(paths.pose_dir),
                "pose_dir_sha256": pose_digest,
                "pose_dir_files": pose_n,
                "box_size": record["box_size"],
                "exhaustiveness": record["exhaustiveness"],
                "n_scored": record["n_scored"],
                "n_failed": record["n_failed"],
                "wall_seconds": record["wall_seconds"],
            }
        )
    # Bind the published results too, so a decision cannot be edited in place
    # without the manifest noticing.
    published: dict[str, list[dict[str, str]]] = {}
    for key, candidates in (
        ("gate", ["reports/results/m10_gate.json"]),
        ("qa", ["reports/results/m10_qa_redock.json"]),
        (
            "sensitivity",
            sorted(str(p) for p in pathlib.Path("reports/results").glob("m10_sensitivity_*.json")),
        ),
    ):
        rows = []
        for candidate in candidates:
            target = pathlib.Path(candidate)
            if target.exists():
                rows.append({"path": str(target), "sha256": sha256_file(target)})
        if rows:
            published[key] = rows

    contract = pathlib.Path("configs/experiments/m10-docking-v1.yaml")
    current = sha256_file(contract)
    superseded = [
        {"sha256": digest, "note": note}
        for digest, note in sorted(
            {
                e["config_sha256"]: (
                    "the contract as it stood when this run was docked; the only later "
                    "change is the added cohort_pin block, which leaves every protocol, "
                    "gate and cohort field identical"
                )
                for e in entries
                if e["config_sha256"] != current
            }.items()
        )
    ]
    path = freeze_manifest(
        entries,
        provenance,
        published=published,
        config_path=contract,
        superseded_contracts=superseded or None,
    )
    from seq2lead.dock.artifacts import load_verification_contract

    scope = verify_manifest_scoped(path, contract=load_verification_contract())
    if scope.problems:
        for problem in scope.problems:
            typer.secho(f"  {problem}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.secho(f"froze {path} ({len(entries)} runs), {scope.summary()}", fg=typer.colors.GREEN)
    for key, rows in sorted(published.items()):
        typer.echo(f"  bound {key}: {len(rows)} artifact(s)")
    typer.echo(f"  digest {sha256_file(MANIFEST_PATH)[:16]}…")


@dock_app.command("verify-manifest")
def dock_verify_manifest(
    manifest: str = typer.Option(
        "", help="Manifest to verify (default: the project's). Used to check an archive."
    ),
    contract_path: str = typer.Option(
        "", help="Verification contract (default: configs/manifests/m10_verification.json)."
    ),
    allow_missing_trees: bool = typer.Option(
        False,
        help="For a review archive: report absent ligand/pose directories as unavailable "
        "rather than as problems. They are still named, never silently passed.",
    ),
) -> None:
    """Verify the manifest against a contract declared outside it."""
    from seq2lead.dock.artifacts import (
        MANIFEST_PATH,
        VERIFICATION_CONTRACT_PATH,
        VerificationContractError,
        load_verification_contract,
        verify_manifest_scoped,
    )

    target = pathlib.Path(manifest) if manifest else MANIFEST_PATH
    declared = pathlib.Path(contract_path) if contract_path else VERIFICATION_CONTRACT_PATH
    try:
        contract = load_verification_contract(declared)
    except VerificationContractError as exc:
        typer.secho(f"  {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.echo(f"contract {contract.version}")
    typer.echo(f"  expects runs       {list(contract.expected_runs)}")
    typer.echo(f"  expects publications {sorted(contract.required_publications)}")
    typer.echo(f"  verifying          {target}")
    scope = verify_manifest_scoped(
        target, contract=contract, allow_missing_trees=allow_missing_trees
    )
    for item in scope.unavailable:
        typer.secho(f"  unavailable: {item}", fg=typer.colors.YELLOW)
    if scope.problems:
        for problem in scope.problems:
            typer.secho(f"  {problem}", fg=typer.colors.RED)
        raise typer.Exit(1)
    colour = typer.colors.GREEN if scope.complete else typer.colors.YELLOW
    typer.secho(f"  {scope.summary()}", fg=colour)
    if not scope.complete:
        typer.secho(
            "  NOT a complete verification: the artifacts above were not present.",
            fg=typer.colors.YELLOW,
        )


bundle_app = typer.Typer(help="Exportable inference bundles (standalone ranking).")
app.add_typer(bundle_app, name="bundle")


@bundle_app.command("export")
def bundle_export(
    model: str = typer.Option(..., help="Dual-encoder checkpoint to export."),
    out: str = typer.Option(..., help="Directory to write the bundle into."),
    library: str = typer.Option("curated-ki-25k-v1", help="Frozen library version."),
    provenance: str = typer.Option("", help="One line recording why this bundle exists."),
) -> None:
    """Build a standalone inference bundle. Needs the database; users do not run this."""
    from seq2lead.db import connect
    from seq2lead.inference.export import export

    destination = pathlib.Path(out)
    with connect() as conn:
        try:
            manifest = export(
                conn, pathlib.Path(model), destination, library_name=library, provenance=provenance
            )
        except (LookupError, ValueError, RuntimeError) as exc:
            raise _fail(str(exc)) from exc
    counts = manifest["counts"]
    typer.secho(f"wrote {destination}", fg=typer.colors.GREEN)
    typer.echo(f"  {counts['compounds']:,} compounds, projection_dim {counts['projection_dim']}")
    typer.echo(f"  {manifest['total_bytes']:,} bytes across {len(manifest['files'])} files")
    for name, entry in sorted(manifest["files"].items()):
        typer.echo(f"    {name:32} {entry['bytes']:>12,}  {entry['sha256'][:16]}…")


@bundle_app.command("verify")
def bundle_verify(
    bundle: str = typer.Option("", help="Bundle directory. Default: $SEQ2LEAD_BUNDLE."),
) -> None:
    """Check a bundle's manifest and every digest, without loading a model."""
    from seq2lead.inference.bundle import BundleError, resolve_bundle, verify_bundle

    try:
        manifest = verify_bundle(resolve_bundle(bundle or None))
    except BundleError as exc:
        raise _fail(str(exc)) from exc
    typer.secho(f"bundle verified: {manifest['bundle_version']}", fg=typer.colors.GREEN)
    typer.echo(
        f"  library {manifest['library']['name']}  {manifest['counts']['compounds']:,} compounds"
    )
    typer.echo(f"  source checkpoint {manifest['source_checkpoint']['sha256'][:16]}…")
    for name, entry in sorted(manifest["files"].items()):
        typer.echo(f"    ok {name:32} {entry['bytes']:>12,}")


@app.command("prioritise")
def prioritise(
    bundle: str = typer.Option(
        "", help="Bundle directory. Default: $SEQ2LEAD_BUNDLE, ./seq2lead-bundle."
    ),
    sequence_file: str = typer.Option("", help="FASTA file holding ONE protein."),
    sequence: str = typer.Option("", help="Or paste the sequence directly."),
    top_n: int = typer.Option(50, help="How many compounds to show. 0 means all."),
    out_csv: str = typer.Option("", help="Also write the full result here as CSV."),
    device: str = typer.Option("", help="Force a torch device (cpu, mps, cuda)."),
    diverse: int = typer.Option(0, help="Opt in: pick this many chemically diverse compounds."),
    diversity_threshold: float = typer.Option(0.7, help="Max Tanimoto to anything already kept."),
    min_mw: float = typer.Option(0.0, help="Opt in: minimum molecular weight."),
    max_mw: float = typer.Option(0.0, help="Opt in: maximum molecular weight."),
    min_tpsa: float = typer.Option(0.0, help="Opt in: minimum TPSA."),
    max_tpsa: float = typer.Option(0.0, help="Opt in: maximum TPSA."),
) -> None:
    """Rank a bundled compound library against one protein sequence. No database needed.

    Results are PRIORITISED CANDIDATES FOR TESTING. Predicted pKi is a ranking
    score, not a binding probability and not a calibrated confidence.
    """
    from seq2lead.inference.bundle import BundleError, load_bundle, resolve_bundle
    from seq2lead.inference.rank import ProteinEncoder, rank
    from seq2lead.inference.report import render, write_csv
    from seq2lead.inference.sequence import SequenceError, read_query, validate
    from seq2lead.inference.shortlist import (
        ShortlistError,
        filter_by_properties,
        select_diverse,
    )

    if bool(sequence_file) == bool(sequence):
        raise _fail("supply exactly one of --sequence-file or --sequence")
    try:
        query = read_query(
            path=pathlib.Path(sequence_file) if sequence_file else None,
            sequence=sequence or None,
        )
    except SequenceError as exc:
        raise _fail(str(exc)) from exc

    try:
        loaded = load_bundle(resolve_bundle(bundle or None))
    except BundleError as exc:
        raise _fail(str(exc)) from exc

    spec = loaded.protein_spec
    try:
        cleaned, notes = validate(
            query, training_window=spec["training_window"], max_length=spec["max_length"]
        )
    except SequenceError as exc:
        raise _fail(str(exc)) from exc

    try:
        encoder = ProteinEncoder(spec, device or None)
        result = rank(
            loaded, cleaned, header=query.header, top_n=top_n, encoder=encoder, notes=notes
        )
    except BundleError as exc:
        raise _fail(str(exc)) from exc

    shortlist = None
    windows = any(v for v in (min_mw, max_mw, min_tpsa, max_tpsa))
    try:
        if windows:
            shortlist = filter_by_properties(
                result.rows,
                min_mw=min_mw or None,
                max_mw=max_mw or None,
                min_tpsa=min_tpsa or None,
                max_tpsa=max_tpsa or None,
            )
        if diverse > 0:
            source = shortlist.kept if shortlist else result.rows
            picked = select_diverse(source, n=diverse, threshold=diversity_threshold)
            if shortlist:
                picked.removed = shortlist.removed + picked.removed
                picked.applied = shortlist.applied + picked.applied
            shortlist = picked
    except ShortlistError as exc:
        raise _fail(str(exc)) from exc

    typer.echo(render(result, shortlist))
    if out_csv:
        written = write_csv(pathlib.Path(out_csv), result, shortlist)
        typer.secho(f"wrote {written}", fg=typer.colors.GREEN)


@app.command("web")
def web(
    bundle: str = typer.Option(
        "", help="Bundle directory. Default: $SEQ2LEAD_BUNDLE, ./seq2lead-bundle."
    ),
    port: int = typer.Option(8765, help="Port to listen on."),
    host: str = typer.Option("127.0.0.1", help="Loopback only; anything else is refused."),
    device: str = typer.Option("", help="Force a torch device (cpu, mps, cuda)."),
    preload: bool = typer.Option(
        False, help="Load the protein encoder at startup instead of on the first query."
    ),
) -> None:
    """Serve the local browser interface for standalone ranking.

    Localhost only. There is no authentication, no TLS and no rate limiting: this
    is a single-user local tool, not a hosted service.
    """
    from seq2lead.inference.bundle import BundleError, resolve_bundle
    from seq2lead.web.server import serve

    try:
        resolved = resolve_bundle(bundle or None)
        httpd = serve(resolved, host=host, port=port, device=device or None, preload=preload)
    except (BundleError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    except OSError as exc:
        raise _fail(f"could not bind {host}:{port}: {exc}") from exc

    typer.secho(f"Seq2Lead local interface: http://{host}:{port}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  bundle {resolved}")
    typer.echo("  localhost only, no authentication. Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\n  stopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":  # pragma: no cover
    app()
