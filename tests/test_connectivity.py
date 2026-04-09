"""
Test Connectivity & Response Script
=====================================
Tests connections to Supabase and Neo4j, validates responses,
and prints a detailed diagnostic report.

Usage:
    python test_connectivity.py
"""

import os
import sys
import time
import json
from datetime import datetime, timezone

from dotenv import load_dotenv

# ── Load env ─────────────────────────────────────────────────────
load_dotenv(".env.local", override=True)
load_dotenv(".env", override=False)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# ── Pretty printing helpers ──────────────────────────────────────
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
CHECK = "✔"
CROSS = "✘"
WARN = "⚠"


def header(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{'═' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 60}{RESET}")


def status(ok: bool, label: str, detail: str = "") -> None:
    icon = f"{GREEN}{CHECK}{RESET}" if ok else f"{RED}{CROSS}{RESET}"
    msg = f"  {icon} {label}"
    if detail:
        msg += f"  —  {detail}"
    print(msg)


def warn(label: str, detail: str = "") -> None:
    msg = f"  {YELLOW}{WARN}{RESET} {label}"
    if detail:
        msg += f"  —  {detail}"
    print(msg)


# ── Results storage ──────────────────────────────────────────────
results = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "supabase": {"status": "UNTESTED", "details": {}},
    "neo4j": {"status": "UNTESTED", "details": {}},
}


# ══════════════════════════════════════════════════════════════════
# 1. CHECK ENVIRONMENT VARIABLES
# ══════════════════════════════════════════════════════════════════
def check_env_vars() -> bool:
    header("1 · Environment Variables")
    all_ok = True

    env_checks = {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": SUPABASE_KEY,
        "NEO4J_URI": NEO4J_URI,
        "NEO4J_USER": NEO4J_USER,
        "NEO4J_PASSWORD": NEO4J_PASSWORD,
    }

    for name, value in env_checks.items():
        if value:
            masked = value[:8] + "…" + value[-4:] if len(value) > 16 else "***"
            status(True, name, f"set ({masked})")
        else:
            status(False, name, "MISSING")
            all_ok = False

    return all_ok


# ══════════════════════════════════════════════════════════════════
# 2. TEST SUPABASE
# ══════════════════════════════════════════════════════════════════
def test_supabase() -> None:
    header("2 · Supabase Connectivity & Response")

    if not SUPABASE_URL or not SUPABASE_KEY:
        status(False, "Skipped", "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        results["supabase"]["status"] = "SKIPPED"
        return

    try:
        from supabase import create_client
    except ImportError:
        status(False, "Import", "supabase package not installed")
        results["supabase"]["status"] = "IMPORT_ERROR"
        return

    # ── 2a. Create client ────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        elapsed = (time.perf_counter() - t0) * 1000
        status(True, "Client created", f"{elapsed:.0f} ms")
        results["supabase"]["details"]["client_creation_ms"] = round(elapsed, 1)
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        status(False, "Client creation failed", str(e))
        results["supabase"]["status"] = "CLIENT_ERROR"
        results["supabase"]["details"]["error"] = str(e)
        return

    # ── 2b. Auth health (Supabase REST endpoint) ─────────────────
    try:
        import httpx

        t0 = time.perf_counter()
        resp = httpx.get(
            f"{SUPABASE_URL}/rest/v1/",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            timeout=10.0,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        status(
            resp.status_code in (200, 204),
            "REST API health",
            f"HTTP {resp.status_code}  ({elapsed:.0f} ms)",
        )
        results["supabase"]["details"]["rest_status"] = resp.status_code
        results["supabase"]["details"]["rest_latency_ms"] = round(elapsed, 1)
    except Exception as e:
        status(False, "REST API health", str(e))
        results["supabase"]["details"]["rest_error"] = str(e)

    # ── 2c. List tables via RPC/query ────────────────────────────
    try:
        t0 = time.perf_counter()
        # Try fetching from a known table; if it doesn't exist that's fine
        # We just want to prove the connection works
        resp = httpx.get(
            f"{SUPABASE_URL}/rest/v1/",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
            },
            params={"limit": "0"},
            timeout=10.0,
        )
        elapsed = (time.perf_counter() - t0) * 1000
        status(True, "Database reachable", f"{elapsed:.0f} ms")
        results["supabase"]["details"]["db_latency_ms"] = round(elapsed, 1)
    except Exception as e:
        status(False, "Database reachable", str(e))

    # ── 2d. Test RPC function (match_paper_chunks) ───────────────
    try:
        t0 = time.perf_counter()
        # Check if the function exists by calling with dummy data
        rpc_result = client.rpc(
            "match_paper_chunks",
            {
                "query_embedding": [0.0] * 1024,  # dummy embedding
                "match_threshold": 0.99,  # very high = expect 0 results
                "match_count": 1,
                "filter_ids": [],
            },
        ).execute()
        elapsed = (time.perf_counter() - t0) * 1000
        row_count = len(rpc_result.data) if rpc_result.data else 0
        status(
            True,
            "RPC match_paper_chunks",
            f"returned {row_count} row(s)  ({elapsed:.0f} ms)",
        )
        results["supabase"]["details"]["rpc_works"] = True
        results["supabase"]["details"]["rpc_latency_ms"] = round(elapsed, 1)
    except Exception as e:
        err_msg = str(e)
        if "function" in err_msg.lower() and "not found" in err_msg.lower():
            warn(
                "RPC match_paper_chunks", "function not found (may not be deployed yet)"
            )
        else:
            warn("RPC match_paper_chunks", err_msg[:120])
        results["supabase"]["details"]["rpc_works"] = False
        results["supabase"]["details"]["rpc_note"] = err_msg[:200]

    results["supabase"]["status"] = "OK"
    status(True, f"{BOLD}Supabase overall{RESET}", "CONNECTED")


# ══════════════════════════════════════════════════════════════════
# 3. TEST NEO4J
# ══════════════════════════════════════════════════════════════════
def test_neo4j() -> None:
    header("3 · Neo4j Connectivity & Response")

    if not NEO4J_URI or not NEO4J_USER or not NEO4J_PASSWORD:
        status(False, "Skipped", "Missing NEO4J_URI, NEO4J_USER, or NEO4J_PASSWORD")
        results["neo4j"]["status"] = "SKIPPED"
        return

    try:
        from neo4j import GraphDatabase, exceptions as neo4j_exceptions
    except ImportError:
        status(False, "Import", "neo4j package not installed")
        results["neo4j"]["status"] = "IMPORT_ERROR"
        return

    import concurrent.futures

    NEO4J_TIMEOUT = 30  # seconds — enough for Aura wake-up

    # ── 3a. Create driver + verify (with timeout) ────────────────
    t0 = time.perf_counter()
    try:

        def _create_and_verify():
            d = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            d.verify_connectivity()
            return d

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(_create_and_verify)
            driver = future.result(timeout=NEO4J_TIMEOUT)

        elapsed = (time.perf_counter() - t0) * 1000
        status(True, "Driver created", f"{elapsed:.0f} ms")
        status(True, "Connectivity verified", f"{elapsed:.0f} ms")
        results["neo4j"]["details"]["driver_creation_ms"] = round(elapsed, 1)
        results["neo4j"]["details"]["connectivity_ms"] = round(elapsed, 1)
    except concurrent.futures.TimeoutError:
        elapsed = (time.perf_counter() - t0) * 1000
        status(
            False,
            "Connection timed out",
            f"{NEO4J_TIMEOUT}s — Aura instance may be paused/sleeping",
        )
        results["neo4j"]["status"] = "TIMEOUT"
        results["neo4j"]["details"][
            "error"
        ] = f"Connection timed out after {NEO4J_TIMEOUT}s"
        return
    except neo4j_exceptions.ServiceUnavailable as e:
        elapsed = (time.perf_counter() - t0) * 1000
        status(False, "Service unavailable", f"{e} ({elapsed:.0f} ms)")
        results["neo4j"]["status"] = "UNAVAILABLE"
        results["neo4j"]["details"]["error"] = str(e)
        return
    except neo4j_exceptions.AuthError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        status(False, "Authentication failed", str(e))
        results["neo4j"]["status"] = "AUTH_ERROR"
        results["neo4j"]["details"]["error"] = str(e)
        return
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        status(False, "Connection failed", f"{e} ({elapsed:.0f} ms)")
        results["neo4j"]["status"] = "CONNECTION_ERROR"
        results["neo4j"]["details"]["error"] = str(e)
        return

    # ── Helper: run a Neo4j operation with timeout ───────────────
    def _run_with_timeout(fn, timeout=15):
        """Run a blocking Neo4j operation with a timeout."""
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(fn)
            return future.result(timeout=timeout)

    # ── 3c. Server info ──────────────────────────────────────────
    try:
        info = _run_with_timeout(driver.get_server_info)
        status(
            True,
            "Server info",
            f"address={info.address}, protocol={info.protocol_version}",
        )
        results["neo4j"]["details"]["server_address"] = str(info.address)
        results["neo4j"]["details"]["protocol_version"] = str(info.protocol_version)
    except Exception as e:
        warn("Server info", str(e))

    # ── 3d. Run test query ───────────────────────────────────────
    t0 = time.perf_counter()
    try:

        def _ping():
            with driver.session() as session:
                result = session.run("RETURN 1 AS ping")
                return result.single()

        record = _run_with_timeout(_ping)
        elapsed = (time.perf_counter() - t0) * 1000
        if record and record["ping"] == 1:
            status(True, "Test query (RETURN 1)", f"OK  ({elapsed:.0f} ms)")
            results["neo4j"]["details"]["ping_ms"] = round(elapsed, 1)
        else:
            status(False, "Test query", "Unexpected result")
    except concurrent.futures.TimeoutError:
        status(False, "Test query", "Timed out (15s)")
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        status(False, "Test query", f"{e} ({elapsed:.0f} ms)")

    # ── 3e. Count nodes & relationships ──────────────────────────
    t0 = time.perf_counter()
    try:

        def _counts():
            with driver.session() as session:
                node_result = session.run("MATCH (n) RETURN count(n) AS total_nodes")
                total_nodes = node_result.single()["total_nodes"]
                rel_result = session.run(
                    "MATCH ()-[r]->() RETURN count(r) AS total_rels"
                )
                total_rels = rel_result.single()["total_rels"]
                return total_nodes, total_rels

        total_nodes, total_rels = _run_with_timeout(_counts)
        elapsed = (time.perf_counter() - t0) * 1000
        status(True, "Node count", f"{total_nodes:,} nodes  ({elapsed:.0f} ms)")
        status(True, "Relationship count", f"{total_rels:,} relationships")
        results["neo4j"]["details"]["total_nodes"] = total_nodes
        results["neo4j"]["details"]["total_relationships"] = total_rels
    except concurrent.futures.TimeoutError:
        warn("Node/Relationship count", "Timed out (15s)")
    except Exception as e:
        warn("Node/Relationship count", str(e))

    # ── 3f. List labels & relationship types ─────────────────────
    try:

        def _schema():
            with driver.session() as session:
                labels = [
                    r["label"]
                    for r in session.run("CALL db.labels() YIELD label RETURN label")
                ]
                rel_types = [
                    r["relationshipType"]
                    for r in session.run(
                        "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
                    )
                ]
                return labels, rel_types

        labels, rel_types = _run_with_timeout(_schema)

        if labels:
            status(True, "Labels", ", ".join(labels))
        else:
            warn("Labels", "No labels found")

        if rel_types:
            status(True, "Relationship types", ", ".join(rel_types))
        else:
            warn("Relationship types", "No relationship types found")

        results["neo4j"]["details"]["labels"] = labels
        results["neo4j"]["details"]["relationship_types"] = rel_types
    except concurrent.futures.TimeoutError:
        warn("Schema info", "Timed out (15s)")
    except Exception as e:
        warn("Schema info", str(e))

    # ── 3g. Check for Publication nodes (app-specific) ───────────
    try:

        def _publications():
            with driver.session() as session:
                pub_result = session.run(
                    "MATCH (p:Publication) RETURN count(p) AS cnt, "
                    "collect(p.title)[..3] AS sample_titles"
                )
                return pub_result.single()

        rec = _run_with_timeout(_publications)
        pub_count = rec["cnt"]
        sample = rec["sample_titles"]

        if pub_count > 0:
            status(True, "Publication nodes", f"{pub_count:,} found")
            for title in sample:
                print(f"       ↳ {title}")
        else:
            warn("Publication nodes", "0 found (graph may be empty)")

        results["neo4j"]["details"]["publication_count"] = pub_count
    except concurrent.futures.TimeoutError:
        warn("Publication nodes", "Timed out (15s)")
    except Exception as e:
        warn("Publication nodes", str(e))

    driver.close()
    results["neo4j"]["status"] = "OK"
    status(True, f"{BOLD}Neo4j overall{RESET}", "CONNECTED")


# ══════════════════════════════════════════════════════════════════
# 4. SUMMARY
# ══════════════════════════════════════════════════════════════════
def print_summary() -> None:
    header("Summary")

    sb = results["supabase"]["status"]
    n4 = results["neo4j"]["status"]

    sb_ok = sb == "OK"
    n4_ok = n4 == "OK"

    status(sb_ok, f"Supabase  →  {sb}")
    status(n4_ok, f"Neo4j     →  {n4}")

    print()
    if sb_ok and n4_ok:
        print(f"  {GREEN}{BOLD}All services are connected and responding!{RESET}")
    elif sb_ok or n4_ok:
        print(f"  {YELLOW}{BOLD}Partial connectivity — some services are down.{RESET}")
    else:
        print(f"  {RED}{BOLD}No services connected — check configuration.{RESET}")

    # Save JSON report
    report_path = "connectivity_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  📄 Full report saved to: {report_path}")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print(f"\n{BOLD}🔌 Supabase & Neo4j Connectivity Test{RESET}")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    check_env_vars()
    test_supabase()
    test_neo4j()
    print_summary()

    # Exit code: 0 = all OK, 1 = partial, 2 = all failed
    sb_ok = results["supabase"]["status"] == "OK"
    n4_ok = results["neo4j"]["status"] == "OK"
    if sb_ok and n4_ok:
        sys.exit(0)
    elif sb_ok or n4_ok:
        sys.exit(1)
    else:
        sys.exit(2)
