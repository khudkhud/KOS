from robotos.app import run_demo


def test_demo_success():
    out = run_demo(cancel_midway=False)
    assert out["session"]["state"] == "SUCCEEDED"
    assert any(e["type"] == "ACTION_RESULT" for e in out["events"])


def test_demo_cancel():
    out = run_demo(cancel_midway=True)
    assert out["session"]["state"] == "CANCELED"
