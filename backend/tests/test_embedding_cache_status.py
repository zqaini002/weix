from app.ai import embeddings


def test_partial_huggingface_snapshot_is_not_reported_cached(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    snapshot = (
        hub
        / "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(embeddings, "_sentence_transformers_cache_dirs", lambda: [hub])

    assert embeddings.can_load_local_embedding() is False
    assert embeddings.get_local_embedding_cache_status() == "未缓存，将在后台自动下载"


def test_huggingface_snapshot_with_model_weights_is_reported_cached(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    snapshot = (
        hub
        / "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2"
        / "snapshots"
        / "revision"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.safetensors").write_bytes(b"model weights")
    monkeypatch.setattr(embeddings, "_sentence_transformers_cache_dirs", lambda: [hub])

    assert embeddings.can_load_local_embedding() is True
