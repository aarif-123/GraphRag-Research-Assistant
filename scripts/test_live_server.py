import httpx


def test_live_server():
    base = "http://127.0.0.1:8000"
    endpoints = [
        ("/", 200),
        ("/app", 200),
        ("/index.html", 200),
        ("/landing.html", 200),
        ("/styles.css", 200),
        ("/app.js", 200),
        ("/js/state.js", 200),
        ("/js/chat.js", 200),
        ("/js/auth.js", 200),
        ("/js/sources.js", 200),
        ("/js/health.js", 200),
        ("/api/health", 200),
        ("/api/health/full", [200, 207]),
        ("/api/stats", 200),
        ("/api/models", 200),
        ("/api/config", 200),
    ]

    print("=== Testing Live Server & Frontend Asset Delivery ===")
    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        all_passed = True
        for path, expected_status in endpoints:
            try:
                r = client.get(f"{base}{path}")
                is_ok = (
                    r.status_code == expected_status
                    if isinstance(expected_status, int)
                    else r.status_code in expected_status
                )
                status_label = "PASS" if is_ok else f"FAIL (got {r.status_code})"
                if not is_ok:
                    all_passed = False
                print(f"[{status_label}] {path} -> {r.status_code} ({len(r.content)} bytes)")
            except Exception as e:
                print(f"[ERROR] {path} -> {e}")
                all_passed = False

    print("\nServer asset test summary: " + ("ALL PASSED!" if all_passed else "SOME FAILED!"))


if __name__ == "__main__":
    test_live_server()
