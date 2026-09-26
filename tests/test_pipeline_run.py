# tests/test_pipeline_run.py
from pathlib import Path

from pypdf import PdfWriter

from creditcard.pipeline import run


def _seed_config(root: Path, cards: str | None = None):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "cards.csv").write_text(
        cards
        or (
            "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
            "c1,HDFC,Regalia,4321,hdfc,HDFC_PW,200000,18\n"
        ),
        encoding="utf-8",
    )
    (root / "config" / "categories.csv").write_text(
        "category_group,subcategory,is_discretionary,sort_order\n"
        "Others,Uncategorised,1,90\n",
        encoding="utf-8",
    )
    (root / "config" / "category_rules.csv").write_text(
        "priority,pattern,subcategory\n", encoding="utf-8"
    )
    (root / "statements").mkdir(exist_ok=True)


def _blank_pdf(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        writer.write(fh)


def test_run_with_no_statements_still_writes_all_four_outputs(tmp_path):
    _seed_config(tmp_path)
    result = run(root=tmp_path)
    for name in ("transactions.csv", "statements.csv",
                 "review_queue.csv", "ingest_log.csv"):
        assert (tmp_path / "output" / name).exists(), f"{name} not written"
    assert result["files_processed"] == 0


def test_run_is_idempotent(tmp_path):
    _seed_config(tmp_path)
    run(root=tmp_path)
    first = (tmp_path / "output" / "transactions.csv").read_text(encoding="utf-8-sig")
    run(root=tmp_path)
    assert (tmp_path / "output" / "transactions.csv").read_text(
        encoding="utf-8-sig"
    ) == first


def test_unregistered_parser_is_recorded_as_failed_not_raised(tmp_path):
    """No issuer parser exists yet, so this is the real first-run path."""
    _seed_config(tmp_path)
    _blank_pdf(tmp_path / "statements" / "c1" / "2026-08.pdf")
    result = run(root=tmp_path)
    assert result["files_processed"] == 1
    assert result["failed"] == 1
    log = (tmp_path / "output" / "ingest_log.csv").read_text(encoding="utf-8-sig")
    assert "hdfc" in log


def test_statements_are_read_per_card_id_not_per_issuer(tmp_path):
    """Two SBI cards share an issuer. Folders keyed by issuer would make each
    card claim the other's statements and mislabel half the rows."""
    _seed_config(
        tmp_path,
        cards=(
            "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
            "sbi_bpcl,SBI,Cashback,1111,sbi,SBI_A_PW,100000,14\n"
            "sbi_simplyclick,SBI,SimplyCLICK,2222,sbi,SBI_B_PW,100000,14\n"
        ),
    )
    _blank_pdf(tmp_path / "statements" / "sbi_bpcl" / "2026-08.pdf")
    result = run(root=tmp_path)
    assert result["files_processed"] == 1


def test_missing_config_raises_rather_than_writing_empty_output(tmp_path):
    """A missing cards.csv is a setup error, not a zero-transaction month."""
    import pytest

    (tmp_path / "config").mkdir()
    with pytest.raises(FileNotFoundError):
        run(root=tmp_path)


def test_unknown_statements_folder_is_reported(tmp_path):
    """A typo'd folder name (sbi_bpcl vs sbi_simplyclick is an easy one)
    means the PDFs inside are never seen by the card loop. That must not be
    silent - it should show up in the summary."""
    _seed_config(tmp_path)
    _blank_pdf(tmp_path / "statements" / "c1_typo" / "2026-08.pdf")
    result = run(root=tmp_path)
    assert "c1_typo" in result["unknown_folders"]


def test_empty_folder_is_not_reported_as_unknown(tmp_path):
    """A folder with no PDFs at all is not an error - do not flag it."""
    _seed_config(tmp_path)
    (tmp_path / "statements" / "some_empty_folder").mkdir(
        parents=True, exist_ok=True
    )
    result = run(root=tmp_path)
    assert "some_empty_folder" not in result["unknown_folders"]


def test_summary_reports_refund_and_reversal_matching(tmp_path):
    _seed_config(tmp_path)
    result = run(root=tmp_path)
    assert result["unmatched_refunds"] == 0
    assert result["unmatched_reversals"] == 0


def test_summary_says_whether_the_workbook_was_skipped(tmp_path):
    _seed_config(tmp_path)
    result = run(root=tmp_path)
    assert result["workbook_skipped"] is False
    assert result["excel_file"] == tmp_path / "output" / "credit_card_data.xlsx"
