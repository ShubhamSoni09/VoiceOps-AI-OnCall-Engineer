from rag_memory_harness import RagSmokeReport, print_rag_smoke_report, run_rag_memory_smoke


def test_rag_memory_smoke_runs_cited_trace_flow(tmp_path):
    report = run_rag_memory_smoke(base_dir=tmp_path)

    assert isinstance(report, RagSmokeReport)
    assert report.room_id == "main"
    assert report.provider == "local_sparse"
    assert report.indexed_documents >= 5
    assert {"memory", "timeline", "action", "code"}.issubset(report.indexed_sources)
    assert "app.py" in report.file_answer
    assert report.action_id
    assert "app.py" in report.provenance_answer
    assert "ontology" in report.provenance_sources
    assert report.provenance_ontology_hits > 0
    assert report.provenance_trace["provider"] == "local_provenance"
    assert report.provenance_trace["rag_provider"] == "local_sparse"
    assert report.provenance_trace["memory_tiers"]["ontology"] > 0
    assert "memory" in report.citation_sources
    assert "code" in report.indexed_sources
    assert report.trace["short_memory_hits"] > 0
    assert report.trace["long_memory_hits"] > 0
    assert report.voice_citation_count > 0
    assert report.voice_trace["provider"] == "local_sparse"
    assert report.memory_health_status == "needs_review"
    assert report.memory_source_coverage["message"] > 0
    assert report.memory_source_coverage["action"] > 0


def test_rag_memory_smoke_prints_json_report(tmp_path, capsys):
    report = run_rag_memory_smoke(base_dir=tmp_path)

    print_rag_smoke_report(report, as_json=True)

    output = capsys.readouterr().out
    assert '"room_id": "main"' in output
    assert '"provider": "local_sparse"' in output
    assert '"provenance_ontology_hits"' in output
    assert '"voice_citation_count"' in output
    assert '"memory_health_status"' in output
