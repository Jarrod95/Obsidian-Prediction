from datetime import datetime, timedelta, timezone
from obsidian_prediction.parity_paper import closing_at, prioritize_markets

def market(mid, end):
    return {"id": mid, "outcomes": '["Yes","No"]',
            "clobTokenIds": '["123","456"]', "active": True,
            "endDate": end.isoformat()}

def test_nearest_first_and_horizon():
    now = datetime.now(timezone.utc)
    a = market(1, now + timedelta(hours=24))
    b = market(2, now + timedelta(hours=2))
    c = market(3, now + timedelta(days=10))
    assert [x["id"] for x in prioritize_markets([a,c,b], now, 48)] == [2,1]

def test_unknown_end_excluded():
    now = datetime.now(timezone.utc)
    m = market(1, now + timedelta(hours=1))
    del m["endDate"]
    assert closing_at(m) is None
    assert prioritize_markets([m], now, 48) == []
