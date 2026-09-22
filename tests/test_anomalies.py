import math


def ingest(client, device_id, events):
    return client.post("/ingest", json={"device_id": device_id, "events": events})


def anomalies(client, device_id, window_start, **params):
    return client.get(
        f"/devices/{device_id}/anomalies",
        params={"window_start": window_start, **params},
    )


def seal_first_window(client, device_id="dev-1"):
    """推进水位线到 00:02:00，封存默认 60s/30s 配置下的 [00:00, 00:01) 窗口。"""
    ingest(client, device_id, [
        {"event_id": "seal", "event_time": "2026-09-22T00:02:00Z", "value": 0.0},
    ])


def test_median_mad_scores_flag_outliers(client):
    # [10, 11, 12, 11]：median=11，MAD=1；50 远超默认阈值 3.5
    ingest(client, "dev-1", [
        {"event_id": "e1", "event_time": "2026-09-22T00:00:05Z", "value": 10.0},
        {"event_id": "e2", "event_time": "2026-09-22T00:00:15Z", "value": 11.0},
        {"event_id": "e3", "event_time": "2026-09-22T00:00:25Z", "value": 12.0},
        {"event_id": "e4", "event_time": "2026-09-22T00:00:35Z", "value": 11.0},
        {"event_id": "e5", "event_time": "2026-09-22T00:00:45Z", "value": 50.0},
    ])
    seal_first_window(client)

    r = anomalies(client, "dev-1", "2026-09-22T00:00:00Z")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    out = body[0]
    assert out["event_id"] == "e5"
    assert out["event_time"] == "2026-09-22T00:00:45Z"
    assert out["value"] == 50.0
    # score = 0.6745 * (50 - 11) / 1 = 26.3055
    assert math.isclose(out["score"], 0.6745 * 39, rel_tol=1e-9)


def test_threshold_param_filters_scores(client):
    # [10, 10, 11, 12, 12]：median=11，MAD=1；10/12 的 |score| 均为 0.6745
    ingest(client, "dev-1", [
        {"event_id": "e1", "event_time": "2026-09-22T00:00:05Z", "value": 10.0},
        {"event_id": "e2", "event_time": "2026-09-22T00:00:15Z", "value": 10.0},
        {"event_id": "e3", "event_time": "2026-09-22T00:00:25Z", "value": 11.0},
        {"event_id": "e4", "event_time": "2026-09-22T00:00:35Z", "value": 12.0},
        {"event_id": "e5", "event_time": "2026-09-22T00:00:45Z", "value": 12.0},
    ])
    seal_first_window(client)

    # 默认阈值 3.5：无异常
    assert anomalies(client, "dev-1", "2026-09-22T00:00:00Z").json() == []

    # 阈值降到 0.5：两个 10 与两个 12 均异常，严格大于，按 event_time 升序
    r = anomalies(client, "dev-1", "2026-09-22T00:00:00Z", threshold=0.5)
    body = r.json()
    assert [a["event_id"] for a in body] == ["e1", "e2", "e4", "e5"]
    assert all(math.isclose(abs(a["score"]), 0.6745, rel_tol=1e-9) for a in body)
    # 方向保留在带符号 score 上：10 在中位值之下，12 在中位值之上
    assert [a["score"] < 0 for a in body] == [True, True, False, False]

    # 恰好等于阈值不算异常（严格大于）
    r = anomalies(client, "dev-1", "2026-09-22T00:00:00Z", threshold=0.6745)
    assert r.json() == []


def test_zero_mad_marks_all_non_median_values(client):
    # [10, 10, 10, 99]：median=10，MAD=0 => 所有非中位值异常
    ingest(client, "dev-1", [
        {"event_id": "e1", "event_time": "2026-09-22T00:00:05Z", "value": 10.0},
        {"event_id": "e2", "event_time": "2026-09-22T00:00:15Z", "value": 10.0},
        {"event_id": "e3", "event_time": "2026-09-22T00:00:25Z", "value": 10.0},
        {"event_id": "e4", "event_time": "2026-09-22T00:00:35Z", "value": 99.0},
    ])
    seal_first_window(client)

    # 即使阈值给得极大，MAD=0 时非中位值仍判异常
    r = anomalies(client, "dev-1", "2026-09-22T00:00:00Z", threshold=1000000.0)
    body = r.json()
    assert len(body) == 1
    assert body[0]["event_id"] == "e4"
    assert body[0]["value"] == 99.0
    assert math.isinf(body[0]["score"])


def test_zero_mad_all_equal_values_no_anomalies(client):
    # 全部相等：median=10，MAD=0，但不存在非中位值 => 无异常
    ingest(client, "dev-1", [
        {"event_id": "e1", "event_time": "2026-09-22T00:00:05Z", "value": 10.0},
        {"event_id": "e2", "event_time": "2026-09-22T00:00:15Z", "value": 10.0},
        {"event_id": "e3", "event_time": "2026-09-22T00:00:25Z", "value": 10.0},
    ])
    seal_first_window(client)

    r = anomalies(client, "dev-1", "2026-09-22T00:00:00Z")
    assert r.status_code == 200
    assert r.json() == []


def test_error_codes(client):
    # 设备不存在
    assert anomalies(client, "ghost", "2026-09-22T00:00:00Z").status_code == 404

    ingest(client, "dev-1", [
        {"event_id": "e1", "event_time": "2026-09-22T00:00:10Z", "value": 1.0},
    ])
    # 窗口存在但未封存
    assert anomalies(client, "dev-1", "2026-09-22T00:00:00Z").status_code == 409
    # 设备上不存在该窗口
    assert anomalies(client, "dev-1", "2026-09-22T00:05:00Z").status_code == 404

    seal_first_window(client)
    # 封存后恢复正常
    assert anomalies(client, "dev-1", "2026-09-22T00:00:00Z").status_code == 200

    # threshold 必须大于 0
    assert anomalies(client, "dev-1", "2026-09-22T00:00:00Z", threshold=0).status_code == 422
    assert anomalies(client, "dev-1", "2026-09-22T00:00:00Z", threshold=-1).status_code == 422
    # window_start 必填
    assert client.get("/devices/dev-1/anomalies").status_code == 422


def test_detection_is_read_only_and_includes_late_events(client):
    ingest(client, "dev-1", [
        {"event_id": "e1", "event_time": "2026-09-22T00:00:05Z", "value": 10.0},
        {"event_id": "e2", "event_time": "2026-09-22T00:00:15Z", "value": 10.0},
        {"event_id": "e3", "event_time": "2026-09-22T00:00:25Z", "value": 10.0},
    ])
    seal_first_window(client)

    # 封存后的迟到事件同样纳入该窗口检测
    ingest(client, "dev-1", [
        {"event_id": "late1", "event_time": "2026-09-22T00:00:45Z", "value": 42.0},
    ])

    before = client.get("/devices/dev-1/windows").json()
    r = anomalies(client, "dev-1", "2026-09-22T00:00:00Z")
    after = client.get("/devices/dev-1/windows").json()
    assert before == after  # 检测不改变任何聚合

    body = r.json()
    assert [a["event_id"] for a in body] == ["late1"]
    assert math.isinf(body[0]["score"])

    # 窗口聚合只反映迟到修正本身，检测调用未追加修正
    win = next(w for w in after if w["window_start"] == "2026-09-22T00:00:00Z")
    assert win["status"] == "sealed"
    assert win["count"] == 4
    assert win["correction_count"] == 1
    corrections = client.get("/devices/dev-1/corrections").json()
    assert [c["event_id"] for c in corrections] == ["late1"]


def test_window_start_must_be_aligned_grid_for_existing_window(client):
    ingest(client, "dev-1", [
        {"event_id": "e1", "event_time": "2026-09-22T00:00:10Z", "value": 1.0},
    ])
    seal_first_window(client)
    # 非网格对齐的起点不会命中已存在的窗口
    assert anomalies(client, "dev-1", "2026-09-22T00:00:30Z").status_code == 404
