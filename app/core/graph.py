import asyncio
import math
from typing import Any, Dict, List, Optional, Tuple
from neo4j import exceptions as neo4j_exceptions

from app.config import FREEZE_RETRIEVAL, MAX_GRAPH_NODES, log
from app.core.exceptions import GraphRetrievalError
from app.clients.pool import pool, cache_key, get_cache, set_cache


def rank_papers(papers: List[Dict], anchors: List[str]) -> List[Dict]:
    """Score and sort papers by relevance to the search anchors."""
    if not anchors:
        # If no anchors, we should still sort by citation count to surface the most important papers
        return sorted(
            papers,
            key=lambda p: float(p.get("in_citations") or p.get("n_citation") or 0),
            reverse=True,
        )

    def score(p: Dict) -> float:
        title = (p.get("title") or "").lower()
        s = 0.0
        for anchor in anchors:
            a = anchor.lower()
            if title == a:
                s += 100.0
            elif title.startswith(a) or a in title:
                s += 60.0
            else:
                # word overlap
                t_words = set(title.split())
                a_words = set(a.split())
                overlap = len(t_words & a_words)
                s += overlap * 10.0
        # recency bonus (papers from last 5 years get up to +5)
        try:
            year = int(p.get("year", 2000))
            s += max(0, (year - 2018)) * 0.5
        except (TypeError, ValueError):
            pass
        # seed papers get a graph-score boost
        s += (p.get("score", 1) - 1) * 5.0
        # citation boost (log-scaled)
        citations = float(p.get("in_citations") or p.get("n_citation") or 0)
        s += math.log1p(citations) * 5.0
        return s

    return sorted(papers, key=score, reverse=True)


def _build_filters(filters: Optional[Dict]) -> Tuple[str, str, Dict]:
    year_val = filters.get("year") if filters else None
    domain_val = filters.get("domain") if filters else None
    extra: Dict[str, Any] = {}
    yf = df = ""
    if year_val:
        yf = "AND p.year = $year"
        extra["year"] = year_val
    if domain_val:
        df = "AND toLower(p.domain) = toLower($domain)"
        extra["domain"] = domain_val
    return yf, df, extra


async def retrieve_graph_papers(
    keywords: Optional[List[str]] = None,
    filters: Optional[Dict] = None,
    limit: int = MAX_GRAPH_NODES,
    anchors: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Full graph retrieval:
      1. Seed: papers whose title/author matches keywords
      2. Expand: CITES, CITED_BY, WRITTEN_BY co-authors, PUBLISHED_IN venue peers
      3. Rank by relevance to anchors
    """
    if FREEZE_RETRIEVAL:
        log.info("Database retrieval is frozen. Skipping retrieve_graph_papers.")
        return []

    if not pool.neo4j:
        raise GraphRetrievalError("Neo4j not connected")

    safe_kw = (keywords or [])[:5]
    ck = cache_key(str(safe_kw), str(filters), limit)
    cached = get_cache("graph", ck)
    if cached:
        log.debug(f"Graph cache hit: {safe_kw}")
        return cached

    yf, df, extra = _build_filters(filters)
    params: Dict[str, Any] = {"limit": limit, "keywords": safe_kw, **extra}

    # ── Seed query ──────────────────────────────────────────────────
    seed_cypher = f"""
    WITH $keywords AS kws
    UNWIND kws AS kw
    MATCH (p:Publication)
    WHERE (toLower(p.title) CONTAINS toLower(kw)
       OR EXISTS {{
           MATCH (p)-[:WRITTEN_BY]->(a:Author)
           WHERE toLower(a.name) CONTAINS toLower(kw)
       }})
       {yf} {df}
    WITH DISTINCT p
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(a)
    OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(v)
    OPTIONAL MATCH (p)-[:HAS_TOPIC]->(t)
    WITH p, collect(DISTINCT a.name) AS authors,
         v.name AS venue,
         collect(DISTINCT t.name) AS topics,
         COUNT {{ (p)-[:CITES]->() }} AS out_citations,
         COUNT {{ ()-[:CITES]->(p) }} AS in_citations
    RETURN p.research_id  AS research_id,
           p.title        AS title,
           p.year         AS year,
           p.domain       AS domain,
           p.abstract     AS abstract,
           authors        AS authors,
           venue          AS venue,
           topics         AS topics,
           in_citations   AS in_citations,
           out_citations  AS out_citations,
           2              AS score,
           'seed'         AS source
    ORDER BY in_citations DESC, p.year DESC
    LIMIT $limit
    """

    # ── Expand query ────────────────────────────────────────────────
    expand_cypher = f"""
    WITH $keywords AS kws
    UNWIND kws AS kw
    MATCH (p:Publication)
    WHERE (toLower(p.title) CONTAINS toLower(kw)
       OR EXISTS {{
           MATCH (p)-[:WRITTEN_BY]->(a:Author)
           WHERE toLower(a.name) CONTAINS toLower(kw)
       }})
       {yf} {df}
    WITH collect(DISTINCT p) AS seeds

    UNWIND seeds AS seed
    OPTIONAL MATCH (seed)-[:CITES]->(cited:Publication)
    OPTIONAL MATCH (citing:Publication)-[:CITES]->(seed)
    OPTIONAL MATCH (seed)-[:WRITTEN_BY]->(author:Author)<-[:WRITTEN_BY]-(sibling:Publication)
    OPTIONAL MATCH (seed)-[:PUBLISHED_IN]->(venue:Venue)<-[:PUBLISHED_IN]-(peer:Publication)
    OPTIONAL MATCH (seed)-[:SIMILAR_TO]->(similar:Publication)

    WITH seeds,
         collect(DISTINCT cited)   AS cited_list,
         collect(DISTINCT citing)  AS citing_list,
         collect(DISTINCT sibling) AS sibling_list,
         collect(DISTINCT peer)    AS peer_list,
         collect(DISTINCT similar) AS similar_list

    WITH seeds,
         [p IN cited_list + citing_list + sibling_list + peer_list + similar_list
          WHERE NOT p IN seeds AND p IS NOT NULL] AS expanded

    UNWIND expanded AS ep
    WITH DISTINCT ep
    OPTIONAL MATCH (ep)-[:WRITTEN_BY]->(a)
    OPTIONAL MATCH (ep)-[:PUBLISHED_IN]->(v)
    WITH ep, collect(DISTINCT a.name) AS authors,
         v.name AS venue,
         COUNT {{ ()-[:CITES]->(ep) }} AS in_citations
    RETURN ep.research_id  AS research_id,
           ep.title        AS title,
           ep.year         AS year,
           ep.domain       AS domain,
           ep.abstract     AS abstract,
           authors         AS authors,
           venue           AS venue,
           []              AS topics,
           in_citations    AS in_citations,
           0               AS out_citations,
           1               AS score,
           'expanded'      AS source
    ORDER BY in_citations DESC, ep.year DESC
    LIMIT $limit
    """

    try:
        def _fetch():
            with pool.neo4j.session() as session:
                s_rows = [dict(r) for r in session.run(seed_cypher, params)]
                e_rows = [dict(r) for r in session.run(expand_cypher, params)]
            return s_rows, e_rows

        seed_rows, expanded_rows = await asyncio.to_thread(_fetch)

        seen: set = set()
        merged: List[Dict] = []
        for row in seed_rows + expanded_rows:
            rid = row.get("research_id")
            if rid and rid not in seen:
                seen.add(rid)
                merged.append(row)

        log.info(
            f"Graph [{safe_kw}]: {len(seed_rows)} seed + {len(expanded_rows)} expanded = {len(merged)} unique"
        )

        # Rank by anchor relevance
        ranked = rank_papers(merged, anchors or keywords or [])
        result = ranked[:limit]

        # Query citation links among the final top result IDs to trace research lineage
        top_ids = [r["research_id"] for r in result if r.get("research_id")]
        if top_ids:
            try:
                def _fetch_links():
                    cypher = """
                    MATCH (p1:Publication)-[:CITES]->(p2:Publication)
                    WHERE p1.research_id IN $ids AND p2.research_id IN $ids
                    RETURN p1.research_id AS source, p2.research_id AS target
                    """
                    with pool.neo4j.session() as session:
                        return [dict(r) for r in session.run(cypher, {"ids": top_ids})]

                links = await asyncio.to_thread(_fetch_links)

                # Map links back to the papers
                id_to_paper = {p["research_id"]: p for p in result}
                for link in links:
                    src_id = link["source"]
                    tgt_id = link["target"]
                    if src_id in id_to_paper and tgt_id in id_to_paper:
                        src_paper = id_to_paper[src_id]
                        tgt_paper = id_to_paper[tgt_id]

                        if "cites_retrieved_papers" not in src_paper:
                            src_paper["cites_retrieved_papers"] = []
                        if "cited_by_retrieved_papers" not in tgt_paper:
                            tgt_paper["cited_by_retrieved_papers"] = []

                        src_paper["cites_retrieved_papers"].append(tgt_paper["title"])
                        tgt_paper["cited_by_retrieved_papers"].append(src_paper["title"])
            except Exception as e:
                log.warning(f"Failed to fetch citation relationships: {e}")

        set_cache("graph", ck, result)
        return result

    except neo4j_exceptions.ServiceUnavailable as e:
        raise GraphRetrievalError(f"Neo4j unavailable: {e}")
    except neo4j_exceptions.CypherSyntaxError as e:
        raise GraphRetrievalError(f"Cypher syntax error: {e}")
    except Exception as e:
        raise GraphRetrievalError(f"Graph query error: {e}")


async def get_paper_full(paper_id_or_title: str) -> Optional[Dict]:
    """Fetch a single paper with all its relationships from Neo4j."""
    if FREEZE_RETRIEVAL:
        return None

    if not pool.neo4j:
        return None

    ck = cache_key("paper_full", paper_id_or_title)
    cached = get_cache("relations", ck)
    if cached:
        return cached

    cypher = """
    MATCH (p:Publication)
    WHERE p.research_id = $id OR toLower(p.title) CONTAINS toLower($id)
    WITH p LIMIT 1
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(a:Author)
    OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(v:Venue)
    OPTIONAL MATCH (p)-[:HAS_TOPIC]->(t:Topic)
    OPTIONAL MATCH (p)-[:CITES]->(cited:Publication)
    OPTIONAL MATCH (citing:Publication)-[:CITES]->(p)
    OPTIONAL MATCH (p)-[:SIMILAR_TO]->(sim:Publication)
    RETURN p.research_id  AS research_id,
           p.title        AS title,
           p.year         AS year,
           p.domain       AS domain,
           p.abstract     AS abstract,
           collect(DISTINCT a.name)           AS authors,
           collect(DISTINCT a.affiliation)    AS affiliations,
           v.name                             AS venue,
           collect(DISTINCT t.name)           AS topics,
           collect(DISTINCT cited.title)      AS cites,
           collect(DISTINCT citing.title)     AS cited_by,
           collect(DISTINCT sim.title)        AS similar_to,
           COUNT {{ ()-[:CITES]->(p) }}        AS citation_count
    """
    try:
        def _run():
            with pool.neo4j.session() as session:
                rows = list(session.run(cypher, {"id": paper_id_or_title}))
                return dict(rows[0]) if rows else None

        result = await asyncio.to_thread(_run)
        if result:
            set_cache("relations", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_paper_full error: {e}")
        return None


async def get_author_network(author_name: str) -> Dict:
    """Get an author's ego-network: papers, co-authors, venues."""
    if FREEZE_RETRIEVAL:
        return {}

    if not pool.neo4j:
        return {}

    ck = cache_key("author", author_name)
    cached = get_cache("relations", ck)
    if cached:
        return cached

    cypher = """
    MATCH (a:Author)
    WHERE toLower(a.name) CONTAINS toLower($name)
    WITH a LIMIT 1
    OPTIONAL MATCH (a)<-[:WRITTEN_BY]-(p:Publication)
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(coauthor:Author)
    WHERE coauthor <> a
    OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(v:Venue)
    RETURN a.name           AS author_name,
           a.affiliation    AS affiliation,
           collect(DISTINCT {title: p.title, year: p.year, domain: p.domain}) AS papers,
           collect(DISTINCT coauthor.name)  AS coauthors,
           collect(DISTINCT v.name)         AS venues,
           count(DISTINCT p)                AS paper_count
    """
    try:
        def _run():
            with pool.neo4j.session() as session:
                rows = list(session.run(cypher, {"name": author_name}))
                return dict(rows[0]) if rows else {}

        result = await asyncio.to_thread(_run)
        set_cache("relations", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_author_network error: {e}")
        return {}


async def get_citation_path(from_title: str, to_title: str, max_depth: int = 4) -> Dict:
    """Find shortest citation path between two papers."""
    if FREEZE_RETRIEVAL:
        return {"path_titles": [], "path_length": -1}

    if not pool.neo4j:
        return {}

    ck = cache_key("citepath", from_title, to_title)
    cached = get_cache("relations", ck)
    if cached:
        return cached

    cypher = """
    MATCH (a:Publication), (b:Publication)
    WHERE toLower(a.title) CONTAINS toLower($from_title)
      AND toLower(b.title) CONTAINS toLower($to_title)
    WITH a, b LIMIT 1
    MATCH path = shortestPath((a)-[:CITES*..{max_depth}]->(b))
    RETURN [node IN nodes(path) | node.title] AS path_titles,
           length(path) AS path_length
    LIMIT 1
    """.replace("{max_depth}", str(max_depth))

    try:
        def _run():
            with pool.neo4j.session() as session:
                rows = list(session.run(cypher, {"from_title": from_title, "to_title": to_title}))
                return dict(rows[0]) if rows else {"path_titles": [], "path_length": -1}

        result = await asyncio.to_thread(_run)
        set_cache("relations", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_citation_path error: {e}")
        return {"path_titles": [], "path_length": -1, "error": str(e)}


async def get_trending_papers(limit: int = 10) -> List[Dict]:
    """Papers with high recent citation velocity (cited in last 2 years)."""
    if FREEZE_RETRIEVAL:
        return []

    if not pool.neo4j:
        return []

    ck = cache_key("trending", limit)
    cached = get_cache("graph", ck)
    if cached:
        return cached

    cypher = """
    MATCH (p:Publication)<-[:CITES]-(citing:Publication)
    WHERE citing.year >= 2022
    WITH p, count(citing) AS recent_citations
    ORDER BY recent_citations DESC
    LIMIT $limit
    OPTIONAL MATCH (p)-[:WRITTEN_BY]->(a:Author)
    RETURN p.research_id AS research_id,
           p.title       AS title,
           p.year        AS year,
           p.domain      AS domain,
           collect(a.name) AS authors,
           recent_citations
    ORDER BY recent_citations DESC
    """
    try:
        def _run():
            with pool.neo4j.session() as session:
                return [dict(r) for r in session.run(cypher, {"limit": limit})]

        result = await asyncio.to_thread(_run)
        set_cache("graph", ck, result)
        return result
    except Exception as e:
        log.warning(f"get_trending_papers error: {e}")
        return []


async def get_graph_stats() -> Dict:
    """Database statistics from Neo4j and Supabase."""
    if FREEZE_RETRIEVAL:
        return {}

    if not pool.neo4j:
        return {}

    ck = cache_key("stats")
    cached = get_cache("graph", ck)
    if cached:
        return cached

    cypher = """
    MATCH (p:Publication) WITH count(p) AS papers
    MATCH (a:Author)      WITH papers, count(a) AS authors
    MATCH (v:Venue)       WITH papers, authors, count(v) AS venues
    OPTIONAL MATCH ()-[r:CITES]->() WITH papers, authors, venues, count(r) AS citations
    RETURN papers, authors, venues, citations
    """
    try:
        def _run():
            with pool.neo4j.session() as session:
                rows = list(session.run(cypher))
                return dict(rows[0]) if rows else {}

        stats = await asyncio.to_thread(_run)
        set_cache("graph", ck, stats)
        return stats
    except Exception as e:
        log.warning(f"get_graph_stats error: {e}")
        return {}


async def get_co_citation_cluster(paper_ids: List[str], limit: int = 10) -> List[Dict]:
    """Find papers frequently cited together with the given papers (co-citation)."""
    if FREEZE_RETRIEVAL:
        return []

    if not pool.neo4j or not paper_ids:
        return []

    cypher = """
    MATCH (p:Publication)-[:CITES]->(ref:Publication)
    WHERE p.research_id IN $ids
    WITH ref, count(p) AS co_citation_count
    WHERE co_citation_count > 1
    ORDER BY co_citation_count DESC
    LIMIT $limit
    OPTIONAL MATCH (ref)-[:WRITTEN_BY]->(a:Author)
    RETURN ref.research_id AS research_id,
           ref.title       AS title,
           ref.year        AS year,
           collect(a.name) AS authors,
           co_citation_count
    """
    try:
        def _run():
            with pool.neo4j.session() as session:
                return [dict(r) for r in session.run(cypher, {"ids": paper_ids, "limit": limit})]

        return await asyncio.to_thread(_run)
    except Exception as e:
        log.warning(f"co_citation_cluster error: {e}")
        return []


def build_relationship_context(graph_nodes: List[Dict]) -> str:
    """Convert graph node relationships into a human-readable narrative for the LLM."""
    if not graph_nodes:
        return ""

    lines = ["=== GRAPH RELATIONSHIP CONTEXT ==="]

    # Group by source
    seeds = [n for n in graph_nodes if n.get("source") == "seed"]
    expanded = [n for n in graph_nodes if n.get("source") == "expanded"]

    if seeds:
        lines.append(f"\nDIRECTLY MATCHED PAPERS ({len(seeds)}):")
        for n in seeds[:5]:
            authors_str = ", ".join(a for a in (n.get("authors") or []) if a) or "Unknown"
            venue = n.get("venue") or "Unknown venue"
            cites_in = n.get("in_citations", 0)
            topics_str = ", ".join(n.get("topics") or []) or "N/A"
            abstract = (n.get("abstract") or "")[:200]
            lines.append(
                f"  • {n.get('title', '?')} ({n.get('year', '?')})\n"
                f"    Authors: {authors_str}\n"
                f"    Venue: {venue} | Citations received: {cites_in}\n"
                f"    Topics: {topics_str}\n"
                f"    Abstract: {abstract}{'...' if len(n.get('abstract') or '') > 200 else ''}"
            )

    if expanded:
        lines.append(f"\nRELATED PAPERS VIA GRAPH TRAVERSAL ({len(expanded)}):")
        for n in expanded[:8]:
            authors_str = ", ".join(a for a in (n.get("authors") or []) if a) or "Unknown"
            lines.append(
                f"  • {n.get('title', '?')} ({n.get('year', '?')}) — "
                f"by {authors_str} — {n.get('in_citations', 0)} citations"
            )

    # Append direct citation lineage among retrieved papers
    lineage_lines = []
    for n in graph_nodes:
        cites = n.get("cites_retrieved_papers")
        if cites:
            cites_str = ", ".join(f'"{title}"' for title in cites)
            lineage_lines.append(f'  • "{n.get("title")}" ({n.get("year")}) cites: {cites_str}')

    if lineage_lines:
        lines.append("\nCITATION PATHWAYS & RESEARCH LINEAGE (cites relationships):")
        lines.extend(lineage_lines)

    return "\n".join(lines)
