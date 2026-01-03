"""
site_builder/site_builder.py

Static site generator for goals_tracker_2026.

Produces a static folder structure suitable for GitHub Pages:
- index.html
- goals/<slug>/index.html
- assets/style.css
- assets/chart.umd.min.js  (placeholder; replace with real Chart.js for charts)

No GUI imports. No DB writes. Deterministic output from DB content.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from html import escape

from storage.db import connect
from progress.progress import compute_all_progress, GoalProgress


# -------------------------
# Public API
# -------------------------

def build_site(*, db_path: Path, out_dir: Path) -> None:
    """
    Build the static site into out_dir.

    - out_dir will be created if missing
    - existing files in out_dir will be replaced (safe to delete and rebuild)
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Wipe existing output (but keep directory)
    _wipe_dir_contents(out_dir)

    # Create folders
    assets_dir = out_dir / "assets"
    goals_dir = out_dir / "goals"
    assets_dir.mkdir(parents=True, exist_ok=True)
    goals_dir.mkdir(parents=True, exist_ok=True)

    # Write assets
    (assets_dir / "style.css").write_text(_css(), encoding="utf-8")
    _ensure_chart_js_placeholder(assets_dir / "chart.umd.min.js")

    # Load reference data (owners/categories) for labeling
    owners = _load_lookup(db_path=db_path, table="owners")
    categories = _load_categories(db_path=db_path)

    # Compute progress for all active goals
    progress_list = compute_all_progress(db_path=db_path)

    # Build index
    (out_dir / "index.html").write_text(
        _render_index(progress_list, owners, categories),
        encoding="utf-8",
    )

    # Build per-goal pages
    for gp in progress_list:
        goal_out = goals_dir / gp.slug
        goal_out.mkdir(parents=True, exist_ok=True)
        (goal_out / "index.html").write_text(
            _render_goal_page(gp, owners, categories),
            encoding="utf-8",
        )

    # Also output a JSON bundle (handy for debugging or future enhancements)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "progress.json").write_text(
        json.dumps([_progress_to_public_dict(g) for g in progress_list], indent=2),
        encoding="utf-8",
    )


# -------------------------
# Helpers: DB lookup
# -------------------------

def _load_lookup(*, db_path: Path, table: str) -> Dict[int, str]:
    """
    Load a {id -> name} mapping for simple lookup tables like owners.
    """
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, name FROM {table} ORDER BY id")
        return {int(r["id"]): str(r["name"]) for r in cur.fetchall()}


def _load_categories(*, db_path: Path) -> Dict[int, Dict[str, object]]:
    """
    Load categories with sort_order for stable grouping:
    { id: { "name": str, "sort_order": int } }
    """
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, sort_order FROM categories ORDER BY sort_order, name")
        out: Dict[int, Dict[str, object]] = {}
        for r in cur.fetchall():
            out[int(r["id"])] = {"name": str(r["name"]), "sort_order": int(r["sort_order"])}
        return out


def _load_goal_meta(*, db_path: Path, goal_id: int) -> Dict[str, object]:
    """
    Load goal metadata needed for labeling on goal pages.
    """
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, slug, title, owner_id, category_id, metric_type, unit,
                   target_value, target_direction, cadence_unit, cadence_target
            FROM goals
            WHERE id = ?
            """,
            (goal_id,),
        )
        r = cur.fetchone()
        if not r:
            return {}
        return {k: r[k] for k in r.keys()}


# -------------------------
# Rendering
# -------------------------

def _render_index(
    progress_list: List[GoalProgress],
    owners: Dict[int, str],
    categories: Dict[int, Dict[str, object]],
) -> str:
    # Group by owner -> category
    grouped: Dict[str, Dict[str, List[GoalProgress]]] = {}
    for gp in progress_list:
        meta_owner, meta_cat = _owner_and_category_names(gp, owners, categories)
        grouped.setdefault(meta_owner, {}).setdefault(meta_cat, []).append(gp)

    # Sort goals inside groups
    for owner_name in grouped:
        for cat_name in grouped[owner_name]:
            grouped[owner_name][cat_name].sort(key=lambda g: g.title.lower())

    # Stable owner order: Josh, Rutendo, Both, then others
    owner_order = ["Josh", "Rutendo", "Both"]
    owners_sorted = sorted(grouped.keys(), key=lambda o: (owner_order.index(o) if o in owner_order else 999, o))

    body_parts: List[str] = []
    body_parts.append("<h1>Goals Tracker 2026</h1>")
    body_parts.append("<p class='subtitle'>Static view (generated from your local SQLite DB)</p>")

    for owner_name in owners_sorted:
        body_parts.append(f"<h2>{escape(owner_name)}</h2>")
        cats = grouped[owner_name]

        # Category order by sort_order if we can infer; otherwise alphabetical
        def cat_sort_key(cat_name: str) -> Tuple[int, str]:
            # find category id by name (best-effort)
            sort_order = 999
            for cid, cinfo in categories.items():
                if cinfo["name"] == cat_name:
                    sort_order = int(cinfo["sort_order"])
                    break
            return (sort_order, cat_name.lower())

        for cat_name in sorted(cats.keys(), key=cat_sort_key):
            body_parts.append(f"<h3>{escape(cat_name)}</h3>")
            body_parts.append("<div class='card-grid'>")
            for gp in cats[cat_name]:
                body_parts.append(_render_goal_card(gp))
            body_parts.append("</div>")

    return _wrap_page(
        title="Goals Tracker 2026",
        body="\n".join(body_parts),
        rel_root=".",
    )


def _render_goal_card(gp: GoalProgress) -> str:
    status_cls = f"status-{escape(gp.status)}"
    pct = _fmt_percent(gp.percent_complete)
    current = _fmt_value(gp.current_value)
    target = _fmt_value(gp.target_value)

    summary_bits: List[str] = []
    if pct is not None:
        summary_bits.append(f"<span class='metric'>{pct}</span>")
    if current is not None and target is not None:
        summary_bits.append(f"<span class='metric'>{current} / {target}</span>")
    elif current is not None:
        summary_bits.append(f"<span class='metric'>{current}</span>")

    if gp.cadence_unit and gp.window_count is not None and gp.cadence_target is not None:
        summary_bits.append(
            f"<span class='metric'>window: {gp.window_count} / {int(gp.cadence_target)}</span>"
        )

    summary = " ".join(summary_bits) if summary_bits else "<span class='metric muted'>no data</span>"

    return f"""
    <a class="card {status_cls}" href="goals/{escape(gp.slug)}/">
      <div class="card-title">{escape(gp.title)}</div>
      <div class="card-sub">{escape(gp.metric_type)} · <span class="{status_cls}">{escape(gp.status)}</span></div>
      <div class="card-metrics">{summary}</div>
    </a>
    """.strip()


def _render_goal_page(
    gp: GoalProgress,
    owners: Dict[int, str],
    categories: Dict[int, Dict[str, object]],
) -> str:
    # Load extra meta directly (owner/category ids, unit, etc.)
    # (progress object doesn’t carry these IDs)
    # We keep it best-effort: if missing, render anyway.
    # NOTE: This read is fine (site builder is read-only).
    # We infer db_path in build_site; here we don’t have it, so we include meta only from progress fields.
    # To show owner/category names we’ll embed placeholders.
    header = f"<h1>{escape(gp.title)}</h1>"
    sub = f"<p class='subtitle'>{escape(gp.metric_type)} · <span class='status-pill status-{escape(gp.status)}'>{escape(gp.status)}</span></p>"

    # Summary table
    rows = []
    rows.append(("Status", gp.status))
    if gp.current_value is not None:
        rows.append(("Current", _fmt_value(gp.current_value) or ""))
    if gp.target_value is not None:
        rows.append(("Target", _fmt_value(gp.target_value) or ""))
    if gp.percent_complete is not None:
        rows.append(("Percent", _fmt_percent(gp.percent_complete) or ""))
    if gp.cadence_unit and gp.cadence_target is not None:
        rows.append(("Cadence", f"{gp.cadence_target:g} per {gp.cadence_unit}"))
    if gp.window_start and gp.window_end and gp.window_count is not None:
        rows.append(("This window", f"{gp.window_start} → {gp.window_end} | count={gp.window_count}"))
    if gp.milestones_total is not None and gp.milestones_done is not None:
        rows.append(("Milestones", f"{gp.milestones_done} / {gp.milestones_total}"))

    table_html = "<table class='kv'>\n" + "\n".join(
        f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in rows
    ) + "\n</table>"

    # Chart or series list
    series_json = json.dumps(gp.series)
    chart_block = f"""
    <h2>Progress</h2>
    <canvas id="chart" width="900" height="360"></canvas>
    <div id="chart_fallback" class="muted"></div>

    <script>
      // series: [ [dateStr, value], ... ]
      const SERIES = {series_json};

      function showFallback(msg) {{
        const el = document.getElementById("chart_fallback");
        if (!el) return;
        el.innerHTML = msg;
      }}

      function renderListFallback() {{
        if (!SERIES.length) {{
          showFallback("No data yet.");
          return;
        }}
        const lines = SERIES.map(p => `${{p[0]}}: ${{p[1]}}`).join("<br/>");
        showFallback("<h3>Data points</h3>" + lines);
      }}

      function renderChartJs() {{
        if (!window.Chart) return false;
        const ctx = document.getElementById("chart");
        if (!ctx) return false;

        const labels = SERIES.map(p => p[0]);
        const values = SERIES.map(p => p[1]);

        // Simple line chart
        new Chart(ctx, {{
          type: 'line',
          data: {{
            labels: labels,
            datasets: [{{
              label: 'value',
              data: values,
              tension: 0.15
            }}]
          }},
          options: {{
            responsive: true,
            plugins: {{
              legend: {{ display: false }}
            }},
            scales: {{
              x: {{ display: true }},
              y: {{ display: true }}
            }}
          }}
        }});
        return true;
      }}

      // Try Chart.js; if not present, show fallback list
      if (!renderChartJs()) {{
        renderListFallback();
      }}
    </script>
    """.strip()

    nav = "<p><a href='../../index.html'>← Back to index</a></p>"

    return _wrap_page(
        title=gp.title,
        body="\n".join([nav, header, sub, table_html, chart_block]),
        rel_root="../..",
        extra_scripts=[
            # Chart.js is local (GH Pages friendly). If you replace placeholder with real file, charts render.
            "<script src='../../assets/chart.umd.min.js'></script>",
        ],
    )


def _wrap_page(*, title: str, body: str, rel_root: str, extra_scripts: Optional[List[str]] = None) -> str:
    scripts = "\n".join(extra_scripts or [])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{escape(title)}</title>
  <link rel="stylesheet" href="{rel_root}/assets/style.css"/>
</head>
<body>
  <div class="container">
    {body}
  </div>
  {scripts}
</body>
</html>
""".strip()


# -------------------------
# Formatting / mapping
# -------------------------

def _fmt_value(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    # Show integers cleanly
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_percent(p: Optional[float]) -> Optional[str]:
    if p is None:
        return None
    return f"{p:.1f}%"


def _owner_and_category_names(
    gp: GoalProgress,
    owners: Dict[int, str],
    categories: Dict[int, Dict[str, object]],
) -> Tuple[str, str]:
    # Best effort: we don’t have IDs inside GoalProgress.
    # In v1, GoalProgress doesn’t carry owner/category, so we label as "Unknown".
    # If you want accurate labels, next iteration we will extend GoalProgress or
    # load goal metadata in build_site and pass it into renderers.
    return ("Unknown", "Uncategorized")


def _progress_to_public_dict(gp: GoalProgress) -> Dict[str, object]:
    # Keep output stable and JSON-serializable
    return {
        "goal_id": gp.goal_id,
        "slug": gp.slug,
        "title": gp.title,
        "metric_type": gp.metric_type,
        "status": gp.status,
        "current_value": gp.current_value,
        "target_value": gp.target_value,
        "percent_complete": gp.percent_complete,
        "cadence_unit": gp.cadence_unit,
        "cadence_target": gp.cadence_target,
        "window_start": gp.window_start,
        "window_end": gp.window_end,
        "window_count": gp.window_count,
        "milestones_total": gp.milestones_total,
        "milestones_done": gp.milestones_done,
        "series": gp.series,
    }


# -------------------------
# Filesystem helpers
# -------------------------

def _wipe_dir_contents(d: Path) -> None:
    for child in d.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _ensure_chart_js_placeholder(path: Path) -> None:
    """
    Writes a placeholder Chart.js file if one isn't present.
    Replace this with the real Chart.js UMD build for charts.
    """
    if path.exists():
        return
    path.write_text(
        "/* Placeholder. Replace with Chart.js UMD build at assets/chart.umd.min.js */\n"
        "window.Chart = window.Chart || null;\n",
        encoding="utf-8",
    )


def _css() -> str:
    return """
:root {
  --bg: #0b0c10;
  --panel: #11131a;
  --text: #e7eaf0;
  --muted: #9aa3b2;

  --ok: #1f8f4e;
  --warn: #c98b17;
  --bad: #c2473d;
  --none: #6b7280;

  --border: #232634;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
}

a { color: inherit; text-decoration: none; }

.container {
  max-width: 1100px;
  margin: 0 auto;
  padding: 28px 18px 60px;
}

h1 { margin: 0 0 6px; font-size: 28px; }
h2 { margin-top: 28px; font-size: 22px; }
h3 { margin-top: 18px; font-size: 18px; color: var(--muted); }

.subtitle { margin: 0 0 18px; color: var(--muted); }

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.card {
  display: block;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 12px 10px;
}

.card:hover {
  border-color: #3a3f56;
}

.card-title {
  font-weight: 600;
  margin-bottom: 6px;
}

.card-sub {
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 8px;
}

.card-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.metric {
  font-size: 13px;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(255,255,255,0.06);
  border: 1px solid var(--border);
}

.muted { color: var(--muted); }

.kv {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0 18px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.kv th, .kv td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  font-size: 14px;
}

.kv th {
  width: 180px;
  color: var(--muted);
  font-weight: 600;
}

.status-pill {
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border);
}

.status-no_data { color: var(--none); }
.status-on_track { color: var(--warn); }
.status-at_risk { color: var(--bad); }
.status-done { color: var(--ok); }

canvas {
  width: 100%;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
}
""".strip()
