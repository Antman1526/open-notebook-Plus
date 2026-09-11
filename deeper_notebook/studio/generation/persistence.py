"""Artifact payload and export persistence helpers for Evidence Studio."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from loguru import logger

from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import InvalidInputError
from deeper_notebook.identity import ACTIVITY_URN_PREFIX, PRODUCT_NAME
from deeper_notebook.studio.exporters import (
    export_document,
    export_infographic,
    export_slide_deck,
    export_spreadsheet,
)
from deeper_notebook.studio.exporters.charts import ChartDocument, render_svg_chart
from deeper_notebook.studio.exporters.epub import write_course_pack_epub
from deeper_notebook.studio.exporters.pdf import write_course_pack_pdf
from deeper_notebook.studio.exporters.research_bundle import build_research_bundle
from deeper_notebook.studio.payloads import parse_payload_document
from deeper_notebook.studio.schemas import (
    CoursePackDocument,
    DataTableDocument,
    GenericDocument,
    InfographicDocument,
    ResearchRunDocument,
    SlideDeckDocument,
)

_MAX_WARNING_LEN = 200
_COURSE_PACK_ARTIFACT_TYPES = {"course_pack", "training_guide"}


def _artifact_export_dir() -> Path:
    raw = resolve_env("DEEPER_NOTEBOOK_ARTIFACT_EXPORT_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()

    home = os.environ.get("HOME") or os.environ.get("USERPROFILE", "")
    if home:
        return (
            Path(home)
            / "BrainPulseKnowledge"
            / "deeper-notebook-imports"
            / "evidence-studio"
        )
    return Path.cwd() / "deeper-notebook-imports" / "evidence-studio"


def _artifact_export_slug(value: object, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    chars: list[str] = []
    previous_dash = False
    for char in text:
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    slug = "".join(chars).strip("-")
    return slug or fallback


def _artifact_export_path(export_dir: Path, stem: str, suffix: str) -> Path:
    candidate = export_dir / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate

    for index in range(2, 1000):
        candidate = export_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not allocate export path for {stem}{suffix}")


def _artifact_markdown_export(artifact: StudioArtifact, content: str) -> str:
    source_ids = [str(source_id) for source_id in artifact.source_ids]
    lines = [
        "---",
        f"artifact_id: {json.dumps(str(artifact.id), ensure_ascii=False)}",
        f"notebook_id: {json.dumps(str(artifact.notebook_id), ensure_ascii=False)}",
        f"title: {json.dumps(artifact.title, ensure_ascii=False)}",
        f"artifact_type: {json.dumps(artifact.artifact_type, ensure_ascii=False)}",
        f"status: {json.dumps(artifact.status, ensure_ascii=False)}",
        "source_ids:",
    ]
    if source_ids:
        lines.extend(
            f"  - {json.dumps(source_id, ensure_ascii=False)}"
            for source_id in source_ids
        )
    else:
        lines[-1] = "source_ids: []"
    lines.extend(["---", "", content.strip(), ""])

    if artifact.citations:
        lines.extend(["", "## Stored Citations", ""])
        for citation in artifact.citations:
            title = (
                citation.get("title") or citation.get("source_id") or "Untitled source"
            )
            source_id = citation.get("source_id") or ""
            marker = citation.get("marker") or ""
            preview = citation.get("preview") or ""
            marker_label = f" {marker}" if marker else ""
            lines.append(f"- **{title}**{marker_label} (`{source_id}`): {preview}")
        lines.append("")

    return "\n".join(lines)


def _strip_artifact_markdown_line(line: str) -> str:
    text = line.strip()
    while text.startswith("#"):
        text = text[1:].lstrip()
    for prefix in ("- ", "* "):
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip()
            break
    return text.replace("**", "").strip()


def _research_run_stages(content: str) -> list[dict[str, object]]:
    stages: list[dict[str, object]] = []
    current_title = ""
    current_items: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_items
        if current_title and current_items:
            stages.append({"title": current_title, "items": current_items})
        current_title = ""
        current_items = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("##"):
            flush()
            current_title = _strip_artifact_markdown_line(line)
            continue
        if current_title:
            item = _strip_artifact_markdown_line(line)
            if item:
                current_items.append(item)

    flush()
    return stages


def _split_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    cells = stripped.strip("|").split("|")
    return [_strip_artifact_markdown_line(cell).strip() for cell in cells]


def _is_markdown_table_separator(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _data_table_rows(content: str) -> list[dict[str, str]]:
    header: list[str] = []
    rows: list[dict[str, str]] = []

    for raw_line in content.splitlines():
        cells = _split_markdown_table_row(raw_line)
        if not cells:
            if rows:
                break
            continue
        if _is_markdown_table_separator(cells):
            continue
        if not header:
            header = [cell or f"Column {index + 1}" for index, cell in enumerate(cells)]
            continue
        normalized = {
            header[index] if index < len(header) else f"Column {index + 1}": cell
            for index, cell in enumerate(cells)
        }
        if any(value for value in normalized.values()):
            rows.append(normalized)

    return rows


def _data_table_csv(content: str) -> str:
    rows = _data_table_rows(content)
    if not rows:
        return ""

    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _markdown_heading_level(line: str) -> int:
    match = re.match(r"^(#{1,6})\s+", line.strip())
    return len(match.group(1)) if match else 0


def _markdown_heading_title(line: str) -> str:
    return _strip_artifact_markdown_line(line)


def _course_pack_modules(content: str) -> list[dict[str, object]]:
    modules: list[dict[str, object]] = []
    current_title = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if not current_title:
            return
        summary = next(
            (
                _strip_artifact_markdown_line(line)
                for line in current_lines
                if _strip_artifact_markdown_line(line)
                and not _strip_artifact_markdown_line(line)
                .lower()
                .startswith(
                    (
                        "learner handout",
                        "hands-on exercise",
                        "knowledge check",
                        "facilitator notes",
                        "instructor notes",
                    )
                )
            ),
            "",
        )
        module_content = "\n".join(current_lines)
        modules.append(
            {
                "title": current_title,
                "summary": summary,
                "has_facilitator_notes": bool(
                    re.search(
                        r"(facilitator notes?|instructor notes?|demo script)",
                        module_content,
                        re.IGNORECASE,
                    )
                ),
            }
        )
        current_title = ""
        current_lines = []

    for raw_line in content.splitlines():
        title = _markdown_heading_title(raw_line)
        if _markdown_heading_level(raw_line) in {2, 3} and re.match(
            r"module\s*\d*(?:[:\-.]\s*)?",
            title,
            re.IGNORECASE,
        ):
            flush()
            current_title = title
            current_lines = []
            continue
        if current_title:
            current_lines.append(raw_line)

    flush()
    return modules


def _course_pack_learner_markdown(content: str) -> str:
    visible: list[str] = []
    hidden_level = 0

    for raw_line in content.splitlines():
        level = _markdown_heading_level(raw_line)
        title = _markdown_heading_title(raw_line)
        if level:
            hidden_level = 0
            if re.search(
                r"(facilitator notes?|instructor notes?|demo script)",
                title,
                re.IGNORECASE,
            ):
                hidden_level = level
                continue
        if hidden_level:
            if level and level <= hidden_level:
                hidden_level = 0
            else:
                continue
        visible.append(raw_line)

    return "\n".join(visible).strip()


def _course_pack_assessment_markdown(content: str) -> str:
    selected: list[str] = []
    collecting_level = 0

    for raw_line in content.splitlines():
        level = _markdown_heading_level(raw_line)
        title = _markdown_heading_title(raw_line)
        if level:
            if collecting_level and level <= collecting_level:
                collecting_level = 0
            if re.search(
                r"(knowledge check|assessment|quiz|final assessment)",
                title,
                re.IGNORECASE,
            ):
                collecting_level = level
                selected.append(raw_line)
                continue
        if collecting_level:
            selected.append(raw_line)

    body = "\n".join(selected).strip()
    if body:
        return "# Course Pack Assessment\n\n" + body + "\n"
    return (
        "# Course Pack Assessment\n\nNo dedicated assessment sections were generated.\n"
    )


def _citation_warnings(
    content: str,
    citations: list[dict[str, str]] | None,
) -> dict[str, list[str]]:
    valid_markers = {
        str(citation.get("marker"))
        for citation in (citations or [])
        if citation.get("marker")
    }
    seen_markers = set(re.findall(r"\[S[1-9]\d*\]", content))
    unsupported_markers = sorted(
        seen_markers - valid_markers,
        key=lambda marker: int(marker.removeprefix("[S").removesuffix("]")),
    )
    warnings: dict[str, list[str]] = {}
    if unsupported_markers:
        warnings["unsupported_markers"] = unsupported_markers
    return warnings


def _artifact_output_payload(
    artifact: StudioArtifact,
    content: str,
    citations: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"content": content}
    if artifact.artifact_type == "data_table":
        rows = _data_table_rows(content)
        if rows:
            payload["data_table_rows"] = rows
    if artifact.artifact_type == "research_run":
        stages = _research_run_stages(content)
        if stages:
            payload["research_stages"] = stages
    if artifact.artifact_type in _COURSE_PACK_ARTIFACT_TYPES:
        modules = _course_pack_modules(content)
        if modules:
            payload["course_pack_modules"] = modules
    citation_warnings = _citation_warnings(content, citations)
    if citation_warnings:
        payload["citation_warnings"] = citation_warnings
    return payload


def _course_pack_checklist_export(
    artifact: StudioArtifact, modules: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "artifact_id": str(artifact.id),
        "notebook_id": str(artifact.notebook_id),
        "title": artifact.title,
        "artifact_type": artifact.artifact_type,
        "modules": [
            {
                "title": module["title"],
                "summary": module.get("summary", ""),
                "has_facilitator_notes": module.get("has_facilitator_notes", False),
                "complete": False,
            }
            for module in modules
        ],
    }


def _course_pack_lms_index_html(
    artifact: StudioArtifact,
    content: str,
    modules: list[dict[str, object]],
) -> str:
    module_items = (
        "\n".join(f"<li>{html.escape(str(module['title']))}</li>" for module in modules)
        or "<li>Course Pack content</li>"
    )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8" />',
            f"  <title>{html.escape(artifact.title)}</title>",
            '  <meta name="viewport" content="width=device-width, initial-scale=1" />',
            "  <style>",
            "    body { font-family: system-ui, sans-serif; line-height: 1.55; margin: 2rem; max-width: 920px; }",
            "    pre { white-space: pre-wrap; border: 1px solid #ddd; padding: 1rem; overflow-wrap: anywhere; }",
            "  </style>",
            "</head>",
            "<body>",
            f"  <h1>{html.escape(artifact.title)}</h1>",
            f"  <p>{PRODUCT_NAME} Course Pack export.</p>",
            "  <h2>Modules</h2>",
            f"  <ol>{module_items}</ol>",
            "  <h2>Course Pack Markdown</h2>",
            f"  <pre>{html.escape(content)}</pre>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _course_pack_scorm_manifest(artifact: StudioArtifact) -> str:
    identifier = html.escape(_artifact_export_slug(artifact.id, fallback="course-pack"))
    title = html.escape(artifact.title)
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<manifest identifier="{identifier}" version="1.0"',
            '  xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"',
            '  xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"',
            '  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
            '  xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">',
            "  <metadata>",
            "    <schema>ADL SCORM</schema>",
            "    <schemaversion>1.2</schemaversion>",
            "  </metadata>",
            '  <organizations default="deeper-notebook-course-pack">',
            '    <organization identifier="deeper-notebook-course-pack">',
            f"      <title>{title}</title>",
            '      <item identifier="course-pack-launch" identifierref="course-pack-resource">',
            f"        <title>{title}</title>",
            "      </item>",
            "    </organization>",
            "  </organizations>",
            "  <resources>",
            '    <resource identifier="course-pack-resource" type="webcontent" adlcp:scormtype="sco" href="index.html">',
            '      <file href="index.html" />',
            '      <file href="instructor-guide.md" />',
            '      <file href="learner-handout.md" />',
            '      <file href="module-checklist.json" />',
            '      <file href="assessment.md" />',
            "    </resource>",
            "  </resources>",
            "</manifest>",
            "",
        ]
    )


def _course_pack_tincan_xml(artifact: StudioArtifact) -> str:
    title = html.escape(artifact.title)
    activity_id = html.escape(f"{ACTIVITY_URN_PREFIX}{artifact.id}")
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<tincan xmlns="http://projecttincan.com/tincan.xsd">',
            "  <activities>",
            f'    <activity id="{activity_id}" type="http://adlnet.gov/expapi/activities/course">',
            f"      <name>{title}</name>",
            f"      <description>{PRODUCT_NAME} Course Pack export.</description>",
            '      <launch lang="en-US">index.html</launch>',
            "    </activity>",
            "  </activities>",
            "</tincan>",
            "",
        ]
    )


def _course_pack_xapi_statements(
    artifact: StudioArtifact,
    modules: list[dict[str, object]],
) -> dict[str, object]:
    activity_id = f"{ACTIVITY_URN_PREFIX}{artifact.id}"
    return {
        "activity": {
            "id": activity_id,
            "name": artifact.title,
            "type": "http://adlnet.gov/expapi/activities/course",
        },
        "actor_placeholder": {
            "objectType": "Agent",
            "name": "Learner Name",
            "mbox": "mailto:learner@example.com",
        },
        "verbs": {
            "launched": "http://adlnet.gov/expapi/verbs/launched",
            "completed": "http://adlnet.gov/expapi/verbs/completed",
            "answered": "http://adlnet.gov/expapi/verbs/answered",
        },
        "modules": modules,
    }


def _course_pack_asset_contents(
    artifact: StudioArtifact,
    content: str,
    modules: list[dict[str, object]],
) -> dict[str, str]:
    return {
        "instructor-guide.md": _artifact_markdown_export(artifact, content),
        "learner-handout.md": _artifact_markdown_export(
            artifact, _course_pack_learner_markdown(content)
        ),
        "module-checklist.json": json.dumps(
            _course_pack_checklist_export(artifact, modules),
            ensure_ascii=False,
            indent=2,
        ),
        "assessment.md": _artifact_markdown_export(
            artifact, _course_pack_assessment_markdown(content)
        ),
    }


def _write_course_pack_scorm_package(
    *,
    artifact: StudioArtifact,
    content: str,
    modules: list[dict[str, object]],
    scorm_path: Path,
    assets: dict[str, Path | str] | None = None,
) -> None:
    index_html = _course_pack_lms_index_html(artifact, content, modules)
    resolved_assets: dict[str, Path | str] = (
        assets
        if assets is not None
        else _course_pack_asset_contents(artifact, content, modules)
    )

    with zipfile.ZipFile(scorm_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("imsmanifest.xml", _course_pack_scorm_manifest(artifact))
        package.writestr("index.html", index_html)
        for arcname, item in resolved_assets.items():
            if isinstance(item, Path):
                package.write(item, arcname)
            else:
                package.writestr(arcname, str(item))


def _write_course_pack_xapi_package(
    *,
    artifact: StudioArtifact,
    content: str,
    modules: list[dict[str, object]],
    xapi_path: Path,
    assets: dict[str, Path | str] | None = None,
) -> None:
    index_html = _course_pack_lms_index_html(artifact, content, modules)
    resolved_assets: dict[str, Path | str] = (
        assets
        if assets is not None
        else _course_pack_asset_contents(artifact, content, modules)
    )

    with zipfile.ZipFile(xapi_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("tincan.xml", _course_pack_tincan_xml(artifact))
        package.writestr("index.html", index_html)
        package.writestr(
            "xapi-statements.json",
            json.dumps(
                _course_pack_xapi_statements(artifact, modules),
                ensure_ascii=False,
                indent=2,
            ),
        )
        for arcname, item in resolved_assets.items():
            if isinstance(item, Path):
                package.write(item, arcname)
            else:
                package.writestr(arcname, str(item))


def _write_course_pack_lms_packages(
    *,
    artifact: StudioArtifact,
    content: str,
    modules: list[dict[str, object]],
    scorm_path: Path,
    xapi_path: Path,
    assets: dict[str, Path | str] | None = None,
) -> None:
    _write_course_pack_scorm_package(
        artifact=artifact,
        content=content,
        modules=modules,
        scorm_path=scorm_path,
        assets=assets,
    )
    _write_course_pack_xapi_package(
        artifact=artifact,
        content=content,
        modules=modules,
        xapi_path=xapi_path,
        assets=assets,
    )


def _set_visual_export_warning(
    artifact: StudioArtifact,
    exc: Exception | None,
) -> None:
    payload = artifact.output_payload
    if not isinstance(payload, dict):
        return
    existing = payload.get("export_warnings")
    warnings = dict(existing) if isinstance(existing, dict) else {}
    if exc is None:
        warnings.pop("visual", None)
    else:
        warnings["visual"] = {
            "type": type(exc).__name__[:120],
            "message": (
                "Visual export could not be rendered. "
                "Markdown and JSON exports remain available."
            ),
        }
    if warnings:
        payload["export_warnings"] = warnings
    else:
        payload.pop("export_warnings", None)


def _persist_visual_exports(
    *,
    artifact: StudioArtifact,
    export_dir: Path,
    stem: str,
) -> dict[str, str]:
    try:
        document = parse_payload_document(
            artifact.artifact_type,
            artifact.output_payload,
        )
    except (InvalidInputError, ValueError):
        return {}

    paths: list[Path] = []
    try:
        if isinstance(document, SlideDeckDocument):
            pptx_path = _artifact_export_path(export_dir, stem, ".pptx")
            pdf_path = _artifact_export_path(export_dir, stem, ".pdf")
            paths = [pptx_path, pdf_path]
            export_slide_deck(document, pptx_path, pdf_path)
            result = {"pptx": str(pptx_path), "pdf": str(pdf_path)}
        elif isinstance(document, InfographicDocument):
            png_path = _artifact_export_path(export_dir, stem, ".png")
            pdf_path = _artifact_export_path(export_dir, stem, ".pdf")
            paths = [png_path, pdf_path]
            export_infographic(document, png_path, pdf_path)
            result = {"png": str(png_path), "pdf": str(pdf_path)}
        else:
            return {}
    except Exception as exc:
        for path in paths:
            path.unlink(missing_ok=True)
        _set_visual_export_warning(artifact, exc)
        logger.warning(
            "Evidence Studio visual export failed for artifact {} ({})",
            artifact.id,
            type(exc).__name__,
        )
        return {}

    _set_visual_export_warning(artifact, None)
    return result


def _persist_office_exports(
    *,
    artifact: StudioArtifact,
    export_dir: Path,
    stem: str,
) -> dict[str, str]:
    """Persist editable Office files only from validated structured documents."""
    try:
        document = parse_payload_document(
            artifact.artifact_type, artifact.output_payload
        )
    except (InvalidInputError, ValueError):
        return {}
    if document is None:
        return {}

    results: dict[str, str] = {}

    if isinstance(document, (GenericDocument, CoursePackDocument, ResearchRunDocument)):
        docx_path = _artifact_export_path(export_dir, stem, ".docx")
        try:
            export_document(document, docx_path)
            results["docx"] = str(docx_path)
        except Exception as exc:
            docx_path.unlink(missing_ok=True)
            logger.warning(
                "Evidence Studio Office export failed for artifact {} ({})",
                artifact.id,
                type(exc).__name__,
            )

        if isinstance(document, CoursePackDocument):
            epub_path = _artifact_export_path(export_dir, stem, ".epub")
            try:
                write_course_pack_epub(document, epub_path)
                results["epub"] = str(epub_path)
            except Exception as exc:
                epub_path.unlink(missing_ok=True)
                logger.warning(
                    "Evidence Studio EPUB export failed for artifact {} ({})",
                    artifact.id,
                    type(exc).__name__,
                )

            pdf_path = _artifact_export_path(export_dir, stem, ".pdf")
            try:
                write_course_pack_pdf(document, pdf_path)
                results["pdf"] = str(pdf_path)
            except Exception as exc:
                pdf_path.unlink(missing_ok=True)
                logger.warning(
                    "Evidence Studio PDF export failed for artifact {} ({})",
                    artifact.id,
                    type(exc).__name__,
                )
        return results

    if isinstance(document, DataTableDocument):
        xlsx_path = _artifact_export_path(export_dir, stem, ".xlsx")
        try:
            export_spreadsheet(document, xlsx_path)
            results["xlsx"] = str(xlsx_path)
        except Exception as exc:
            xlsx_path.unlink(missing_ok=True)
            logger.warning(
                "Evidence Studio Office export failed for artifact {} ({})",
                artifact.id,
                type(exc).__name__,
            )

    return results


def _persist_trusted_svg_chart(
    *, artifact: StudioArtifact, export_dir: Path, stem: str
) -> dict[str, str]:
    """Export only an explicitly schema-validated chart payload."""
    payload = artifact.output_payload
    chart_payload = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart_payload, dict):
        return {}
    try:
        chart = ChartDocument.model_validate(chart_payload)
        svg_path = _artifact_export_path(export_dir, f"{stem}-chart", ".svg")
        svg_path.write_text(render_svg_chart(chart), encoding="utf-8")
        return {"svg_chart": str(svg_path)}
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Evidence Studio SVG export rejected for {} ({})",
            artifact.id,
            type(exc).__name__,
        )
        return {}


def _selected_source_metadata(artifact: StudioArtifact) -> list[dict[str, object]]:
    """Keep the bundle source-grounded without copying full source bodies."""
    metadata: list[dict[str, object]] = []
    citations_by_source = {
        str(citation.get("source_id")): citation
        for citation in artifact.citations
        if isinstance(citation, dict) and citation.get("source_id")
    }
    for source_id in artifact.source_ids:
        citation = citations_by_source.get(str(source_id), {})
        metadata.append(
            {
                "source_id": str(source_id),
                "title": citation.get("title"),
                "marker": citation.get("marker"),
            }
        )
    return metadata


def _bundle_generated_files(
    export_paths: dict[str, str], bundle_path: Path
) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for export_type, raw_path in export_paths.items():
        candidate = Path(raw_path)
        if candidate == bundle_path or not candidate.is_file():
            continue
        files[f"generated/{export_type}/{candidate.name}"] = candidate
    return files


def _persist_research_bundle(
    *,
    artifact: StudioArtifact,
    content: str,
    export_dir: Path,
    stem: str,
    export_paths: dict[str, str],
) -> dict[str, str]:
    """Create an immutable integrity bundle after every other export succeeds."""
    try:
        # A bundle is a source-of-record export, so only structured artifacts
        # that pass the existing Studio payload validation are eligible.
        parse_payload_document(artifact.artifact_type, artifact.output_payload)
        bundle_path = _artifact_export_path(
            export_dir, f"{stem}-research-bundle", ".zip"
        )
        result = {"research_bundle": str(bundle_path)}
        artifact.export_paths = {**export_paths, **result}
        report = artifact.output_payload.get("evaluation_report", {})
        evaluation_report = report if isinstance(report, dict) else {}
        build_research_bundle(
            bundle_path,
            artifact=_artifact_export_payload(artifact),
            markdown=_artifact_markdown_export(artifact, content),
            citations=[
                citation
                for citation in artifact.citations
                if isinstance(citation, dict)
            ],
            source_metadata=_selected_source_metadata(artifact),
            evaluation_report=evaluation_report,
            generated_files=_bundle_generated_files(export_paths, bundle_path),
        )
        return result
    except (InvalidInputError, TypeError, ValueError) as exc:
        logger.warning(
            "Evidence Studio research bundle skipped for {} ({})",
            artifact.id,
            type(exc).__name__,
        )
        return {}


def persist_artifact_exports(artifact: StudioArtifact, content: str) -> dict[str, str]:
    export_dir = _artifact_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)

    artifact_slug = _artifact_export_slug(artifact.id, fallback="artifact")
    title_slug = _artifact_export_slug(artifact.title, fallback=artifact.artifact_type)
    stem = f"{artifact_slug}-{title_slug}"

    markdown_path = _artifact_export_path(export_dir, stem, ".md")
    json_path = _artifact_export_path(export_dir, stem, ".json")
    export_paths = {
        "markdown": str(markdown_path),
        "json": str(json_path),
    }
    course_pack_modules = _course_pack_modules(content)
    if artifact.artifact_type in _COURSE_PACK_ARTIFACT_TYPES:
        instructor_path = _artifact_export_path(
            export_dir, f"{stem}-instructor-guide", ".md"
        )
        learner_path = _artifact_export_path(
            export_dir, f"{stem}-learner-handout", ".md"
        )
        checklist_path = _artifact_export_path(
            export_dir, f"{stem}-module-checklist", ".json"
        )
        assessment_path = _artifact_export_path(export_dir, f"{stem}-assessment", ".md")
        scorm_path = _artifact_export_path(export_dir, f"{stem}-scorm", ".zip")
        xapi_path = _artifact_export_path(export_dir, f"{stem}-xapi", ".zip")
        export_paths.update(
            {
                "instructor_guide": str(instructor_path),
                "learner_handout": str(learner_path),
                "module_checklist": str(checklist_path),
                "assessment": str(assessment_path),
                "scorm_package": str(scorm_path),
                "xapi_package": str(xapi_path),
            }
        )
    data_table_csv = (
        _data_table_csv(content) if artifact.artifact_type == "data_table" else ""
    )
    if data_table_csv:
        csv_path = _artifact_export_path(export_dir, f"{stem}-data-table", ".csv")
        export_paths["csv"] = str(csv_path)
    markdown_path.write_text(
        _artifact_markdown_export(artifact, content), encoding="utf-8"
    )
    if data_table_csv:
        csv_path.write_text(data_table_csv, encoding="utf-8")
    if artifact.artifact_type in _COURSE_PACK_ARTIFACT_TYPES:
        instructor_path.write_text(
            _artifact_markdown_export(artifact, content),
            encoding="utf-8",
        )
        learner_path.write_text(
            _artifact_markdown_export(artifact, _course_pack_learner_markdown(content)),
            encoding="utf-8",
        )
        checklist_path.write_text(
            json.dumps(
                _course_pack_checklist_export(artifact, course_pack_modules),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        assessment_path.write_text(
            _artifact_markdown_export(
                artifact, _course_pack_assessment_markdown(content)
            ),
            encoding="utf-8",
        )
        _write_course_pack_lms_packages(
            artifact=artifact,
            content=content,
            modules=course_pack_modules,
            scorm_path=scorm_path,
            xapi_path=xapi_path,
            assets={
                "instructor-guide.md": instructor_path,
                "learner-handout.md": learner_path,
                "module-checklist.json": checklist_path,
                "assessment.md": assessment_path,
            },
        )
    export_paths.update(
        _persist_visual_exports(
            artifact=artifact,
            export_dir=export_dir,
            stem=stem,
        )
    )
    export_paths.update(
        _persist_office_exports(
            artifact=artifact,
            export_dir=export_dir,
            stem=stem,
        )
    )
    export_paths.update(
        _persist_trusted_svg_chart(
            artifact=artifact,
            export_dir=export_dir,
            stem=stem,
        )
    )
    export_paths.update(
        _persist_research_bundle(
            artifact=artifact,
            content=content,
            export_dir=export_dir,
            stem=stem,
            export_paths=export_paths,
        )
    )
    content_hash = artifact_content_hash(artifact, content)
    if isinstance(artifact.output_payload, dict):
        export_hashes = dict(artifact.output_payload.get("export_hashes") or {})
        for fmt in export_paths:
            export_hashes[fmt] = content_hash
        artifact.output_payload["export_hashes"] = export_hashes

    artifact.export_paths = export_paths
    json_path.write_text(
        json.dumps(_artifact_export_payload(artifact), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return export_paths


# v0.8.119 — single-format export regeneration for POST
# /studio/artifacts/{artifact_id}/exports/{format}. Reuses the same
# per-format writers and document-building step as `persist_artifact_exports`
# above rather than duplicating them.
def _document_for_artifact(artifact: StudioArtifact):
    try:
        return parse_payload_document(artifact.artifact_type, artifact.output_payload)
    except (InvalidInputError, ValueError):
        return None


_CANONICAL_FORMAT_ALIASES: dict[str, str] = {
    "scorm": "scorm_package",
    "xapi": "xapi_package",
    "bundle": "research_bundle",
    "svg": "svg_chart",
    "checklist": "module_checklist",
}


def canonical_export_format(export_format: str) -> str:
    """Resolve an export format name or alias to its canonical format string."""
    clean = str(export_format or "").strip().lower()
    return _CANONICAL_FORMAT_ALIASES.get(clean, clean)


def producible_export_formats(
    artifact: StudioArtifact,
    *,
    include_aliases: bool = True,
) -> set[str]:
    """The export formats `persist_artifact_exports` can produce for this artifact.

    Derived from the same per-format writers `persist_artifact_exports` calls
    so it stays in sync with that function instead of duplicating a separately
    maintained list.
    """
    formats = {"markdown", "json"}
    document = _document_for_artifact(artifact)

    if artifact.artifact_type in _COURSE_PACK_ARTIFACT_TYPES:
        formats.update(
            {
                "docx",
                "epub",
                "pdf",
                "instructor_guide",
                "learner_handout",
                "module_checklist",
                "assessment",
                "scorm_package",
                "xapi_package",
            }
        )
        if document is not None:
            formats.add("research_bundle")
    else:
        if isinstance(document, (GenericDocument, ResearchRunDocument)):
            formats.add("docx")
        elif isinstance(document, DataTableDocument):
            formats.add("xlsx")
        elif isinstance(document, SlideDeckDocument):
            formats.update({"pptx", "pdf"})
        elif isinstance(document, InfographicDocument):
            formats.update({"png", "pdf"})

        if document is not None:
            formats.add("research_bundle")

        if artifact.artifact_type == "data_table":
            content = str((artifact.output_payload or {}).get("content") or "")
            if _data_table_csv(content):
                formats.add("csv")

    payload = (
        artifact.output_payload if isinstance(artifact.output_payload, dict) else {}
    )
    if isinstance(payload.get("chart"), dict):
        try:
            ChartDocument.model_validate(payload["chart"])
            formats.add("svg_chart")
        except (TypeError, ValueError):
            pass

    if include_aliases:
        alias_matches = {
            alias
            for alias, canonical in _CANONICAL_FORMAT_ALIASES.items()
            if canonical in formats
        }
        formats.update(alias_matches)

    return formats


_producible_export_formats = producible_export_formats


def artifact_content_hash(
    artifact: StudioArtifact, content: str | None = None
) -> str:
    """Compute sha256 hash of the artifact content for staleness tracking."""
    if content is None:
        payload = (
            artifact.output_payload
            if isinstance(artifact.output_payload, dict)
            else {}
        )
        content = str(payload.get("content") or "")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def is_export_stale(artifact: StudioArtifact, export_format: str) -> bool:
    """Return True if the recorded export for `export_format` is missing or out-of-date.

    An export is considered stale when:
      * No export path exists on the artifact for this format (or its alias).
      * The file at the export path does not exist on disk.
      * No export hash was recorded, or the recorded export hash does not match
        the current artifact content hash.
    """
    canonical = canonical_export_format(export_format)
    export_paths = (
        artifact.export_paths if isinstance(artifact.export_paths, dict) else {}
    )
    path_str = export_paths.get(canonical) or export_paths.get(export_format)
    if not path_str:
        return True
    if not Path(path_str).is_file():
        return True

    payload = (
        artifact.output_payload if isinstance(artifact.output_payload, dict) else {}
    )
    hashes = payload.get("export_hashes")
    if not isinstance(hashes, dict):
        return True

    recorded_hash = hashes.get(canonical) or hashes.get(export_format)
    if not recorded_hash:
        return True

    current_hash = artifact_content_hash(artifact)
    return recorded_hash != current_hash


def get_stale_export_formats(artifact: StudioArtifact) -> list[str]:
    """Return all recorded export formats that are currently stale."""
    export_paths = (
        artifact.export_paths if isinstance(artifact.export_paths, dict) else {}
    )
    return [fmt for fmt in export_paths if is_export_stale(artifact, fmt)]


def persist_single_export(artifact: StudioArtifact, export_format: str) -> str | None:
    """Regenerate exactly one export format for an already-generated artifact.

    Reuses `persist_artifact_exports`'s per-format writers and its
    document-building step (`parse_payload_document` / the markdown stored in
    `output_payload["content"]`), but rebuilds only `export_format`. Writes
    over the artifact's existing path for that format when one is already on
    record, so a link a caller already has stays valid; otherwise allocates a
    fresh path with the same `_artifact_export_path` convention
    `persist_artifact_exports` uses. Returns ``None`` when `export_format` is
    not producible for this artifact (the caller should treat that as an
    unsupported-format request).
    """
    canonical = canonical_export_format(export_format)
    if canonical not in producible_export_formats(artifact, include_aliases=False):
        return None

    payload = (
        artifact.output_payload if isinstance(artifact.output_payload, dict) else {}
    )
    content = str(payload.get("content") or "")

    export_dir = _artifact_export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)

    artifact_slug = _artifact_export_slug(artifact.id, fallback="artifact")
    title_slug = _artifact_export_slug(artifact.title, fallback=artifact.artifact_type)
    stem = f"{artifact_slug}-{title_slug}"

    existing = (
        artifact.export_paths if isinstance(artifact.export_paths, dict) else {}
    )

    def _target(key: str, suffix: str, *, stem_suffix: str = "") -> Path:
        current = existing.get(key)
        if current:
            return Path(current)
        return _artifact_export_path(export_dir, f"{stem}{stem_suffix}", suffix)

    target_path: Path | None = None

    if canonical == "markdown":
        path = _target("markdown", ".md")
        path.write_text(_artifact_markdown_export(artifact, content), encoding="utf-8")
        target_path = path

    elif canonical == "json":
        path = _target("json", ".json")
        path.write_text(
            json.dumps(
                _artifact_export_payload(artifact), ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        target_path = path

    elif canonical == "csv":
        csv_text = _data_table_csv(content)
        if not csv_text:
            return None
        path = _target("csv", ".csv", stem_suffix="-data-table")
        path.write_text(csv_text, encoding="utf-8")
        target_path = path

    elif canonical == "instructor_guide":
        if artifact.artifact_type not in _COURSE_PACK_ARTIFACT_TYPES:
            return None
        path = _target("instructor_guide", ".md", stem_suffix="-instructor-guide")
        path.write_text(_artifact_markdown_export(artifact, content), encoding="utf-8")
        target_path = path

    elif canonical == "learner_handout":
        if artifact.artifact_type not in _COURSE_PACK_ARTIFACT_TYPES:
            return None
        path = _target("learner_handout", ".md", stem_suffix="-learner-handout")
        path.write_text(
            _artifact_markdown_export(
                artifact, _course_pack_learner_markdown(content)
            ),
            encoding="utf-8",
        )
        target_path = path

    elif canonical == "module_checklist":
        if artifact.artifact_type not in _COURSE_PACK_ARTIFACT_TYPES:
            return None
        path = _target("module_checklist", ".json", stem_suffix="-module-checklist")
        course_pack_modules = _course_pack_modules(content)
        path.write_text(
            json.dumps(
                _course_pack_checklist_export(artifact, course_pack_modules),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        target_path = path

    elif canonical == "assessment":
        if artifact.artifact_type not in _COURSE_PACK_ARTIFACT_TYPES:
            return None
        path = _target("assessment", ".md", stem_suffix="-assessment")
        path.write_text(
            _artifact_markdown_export(
                artifact, _course_pack_assessment_markdown(content)
            ),
            encoding="utf-8",
        )
        target_path = path

    elif canonical == "scorm_package":
        if artifact.artifact_type not in _COURSE_PACK_ARTIFACT_TYPES:
            return None
        path = _target("scorm_package", ".zip", stem_suffix="-scorm")
        course_pack_modules = _course_pack_modules(content)
        _write_course_pack_scorm_package(
            artifact=artifact,
            content=content,
            modules=course_pack_modules,
            scorm_path=path,
        )
        target_path = path

    elif canonical == "xapi_package":
        if artifact.artifact_type not in _COURSE_PACK_ARTIFACT_TYPES:
            return None
        path = _target("xapi_package", ".zip", stem_suffix="-xapi")
        course_pack_modules = _course_pack_modules(content)
        _write_course_pack_xapi_package(
            artifact=artifact,
            content=content,
            modules=course_pack_modules,
            xapi_path=path,
        )
        target_path = path

    elif canonical == "svg_chart":
        chart_payload = payload.get("chart")
        if not isinstance(chart_payload, dict):
            return None
        try:
            chart = ChartDocument.model_validate(chart_payload)
            path = _target("svg_chart", ".svg", stem_suffix="-chart")
            path.write_text(render_svg_chart(chart), encoding="utf-8")
            target_path = path
        except (TypeError, ValueError):
            return None

    elif canonical == "research_bundle":
        document = _document_for_artifact(artifact)
        if document is None:
            return None
        path = _target("research_bundle", ".zip", stem_suffix="-research-bundle")
        report = payload.get("evaluation_report", {})
        evaluation_report = report if isinstance(report, dict) else {}
        current_export_paths = dict(existing)
        current_export_paths["research_bundle"] = str(path)
        try:
            build_research_bundle(
                path,
                artifact=_artifact_export_payload(artifact),
                markdown=_artifact_markdown_export(artifact, content),
                citations=[
                    citation
                    for citation in artifact.citations
                    if isinstance(citation, dict)
                ],
                source_metadata=_selected_source_metadata(artifact),
                evaluation_report=evaluation_report,
                generated_files=_bundle_generated_files(current_export_paths, path),
            )
            target_path = path
        except Exception as exc:
            logger.warning(
                "Evidence Studio research bundle generation failed for {}: {}",
                artifact.id,
                exc,
            )
            return None

    else:
        document = _document_for_artifact(artifact)
        if document is None:
            return None

        if canonical == "docx":
            if not isinstance(
                document, (GenericDocument, CoursePackDocument, ResearchRunDocument)
            ):
                return None
            path = _target("docx", ".docx")
            export_document(document, path)
            target_path = path

        elif canonical == "epub":
            if not isinstance(document, CoursePackDocument):
                return None
            path = _target("epub", ".epub")
            write_course_pack_epub(document, path)
            target_path = path

        elif canonical == "xlsx":
            if not isinstance(document, DataTableDocument):
                return None
            path = _target("xlsx", ".xlsx")
            export_spreadsheet(document, path)
            target_path = path

        elif canonical == "pdf":
            if isinstance(document, CoursePackDocument):
                path = _target("pdf", ".pdf")
                write_course_pack_pdf(document, path)
                target_path = path
            elif isinstance(document, SlideDeckDocument):
                pptx_path = _target("pptx", ".pptx")
                pdf_path = _target("pdf", ".pdf")
                export_slide_deck(document, pptx_path, pdf_path)
                target_path = pdf_path
            elif isinstance(document, InfographicDocument):
                png_path = _target("png", ".png")
                pdf_path = _target("pdf", ".pdf")
                export_infographic(document, png_path, pdf_path)
                target_path = pdf_path
            else:
                return None

        elif canonical == "pptx":
            if not isinstance(document, SlideDeckDocument):
                return None
            pptx_path = _target("pptx", ".pptx")
            pdf_path = _target("pdf", ".pdf")
            export_slide_deck(document, pptx_path, pdf_path)
            target_path = pptx_path

        elif canonical == "png":
            if not isinstance(document, InfographicDocument):
                return None
            png_path = _target("png", ".png")
            pdf_path = _target("pdf", ".pdf")
            export_infographic(document, png_path, pdf_path)
            target_path = png_path

    if target_path is None:
        return None

    # Track content hash for staleness detection
    content_hash = artifact_content_hash(artifact, content)
    hashes = dict(payload.get("export_hashes") or {})
    hashes[canonical] = content_hash
    if export_format != canonical:
        hashes[export_format] = content_hash
    payload["export_hashes"] = hashes
    artifact.output_payload = payload

    updated_export_paths = dict(existing)
    updated_export_paths[canonical] = str(target_path)
    if export_format != canonical:
        updated_export_paths[export_format] = str(target_path)
    artifact.export_paths = updated_export_paths

    return str(target_path)


def _brief(exc: BaseException) -> str:
    """Truncate exception text for safe inclusion in user-visible warnings.

    v0.7.132 — Area for Review #10. The previous version did a flat
    character truncation at byte ~199, which on multi-line exceptions
    (PyMuPDF stack traces, mammoth error blocks with embedded paths,
    LangChain provider errors with chained-cause sections) would cut
    in the middle of line 1 and lose the rest entirely. The operator
    saw "could not parse foo.pdf: TypeError: cannot conver…" — no
    indication that the actual cause was 4 lines down.

    New behavior:
      * If exception text is single-line: same as before — truncate
        at _MAX_WARNING_LEN with the ellipsis suffix.
      * If exception text is multi-line: take the first line VERBATIM
        (up to _MAX_WARNING_LEN-32 to leave room for the suffix), then
        suffix with " (… N more lines)". The operator sees the actual
        error head and knows how much was elided.

    The 32-char headroom is sized for the longest realistic suffix
    "(… 999 more lines)" with a leading space. We could be tighter
    but 32 is a clean number and the loss is negligible for messages
    that need this branch (they're invariably hundreds-of-chars long).
    """
    s = str(exc)
    # Multi-line path first — the more interesting case.
    if "\n" in s:
        lines = s.split("\n")
        first = lines[0]
        extra = len(lines) - 1
        suffix = f" (… {extra} more line{'s' if extra != 1 else ''})"
        # Leave room for the suffix when truncating the first line.
        head_budget = _MAX_WARNING_LEN - len(suffix)
        if len(first) > head_budget:
            first = first[: head_budget - 1] + "…"
        return first + suffix

    # Single-line: original behavior.
    if len(s) <= _MAX_WARNING_LEN:
        return s
    return s[: _MAX_WARNING_LEN - 1] + "…"


def _iso(value) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _artifact_export_payload(artifact: StudioArtifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "notebook_id": str(artifact.notebook_id),
        "artifact_type": artifact.artifact_type,
        "title": artifact.title,
        "status": artifact.status,
        "source_ids": [str(source_id) for source_id in artifact.source_ids],
        "prompt": artifact.prompt,
        "model_id": artifact.model_id,
        "provider": artifact.provider,
        "output_format": artifact.output_format,
        "output_payload": artifact.output_payload,
        "citations": artifact.citations,
        "export_paths": artifact.export_paths,
        "revision_of_id": (
            str(artifact.revision_of_id)
            if artifact.revision_of_id is not None
            else None
        ),
        "created": _iso(getattr(artifact, "created", None)),
        "updated": _iso(getattr(artifact, "updated", None)),
    }


# v0.8.124 — single-zip bundle of every *completed* artifact's existing
# export files, for POST /studio/notebooks/{notebook_id}/exports/bundle.
@dataclass
class BundleReport:
    """Result of `write_notebook_artifact_bundle`."""

    file_count: int
    total_bytes: int
    artifact_count: int
    skipped: int
    warnings: list[str] = field(default_factory=list)


def write_notebook_artifact_bundle(
    artifacts: list[StudioArtifact],
    target_zip: Path,
    compression: int,
) -> BundleReport:
    """Bundle every *completed* artifact's already-persisted export files
    into one zip at `target_zip`.

    Layout: ``{artifact_slug}/{format}/{filename}`` per export file, where
    `artifact_slug` is `str(artifact.id).replace(":", "-")` — the same slug
    `StudioArtifact._cleanup_export_files` uses — plus a top-level
    ``manifest.json`` describing the notebook, each bundled artifact's
    formats/sizes, and the ids of artifacts skipped for not being
    'completed'.

    Does NOT regenerate stale exports itself; callers that want fresh
    output should call `persist_single_export` per stale format first.
    An export path recorded on the artifact but missing on disk is warned
    about and skipped rather than failing the whole bundle. Raises OSError
    on write failure — callers should clean up a half-written `target_zip`
    themselves (mirrors the notebook-export zip endpoints).
    """
    warnings: list[str] = []
    completed = [a for a in artifacts if a.status == "completed"]
    skipped_ids = [str(a.id) for a in artifacts if a.status != "completed"]
    notebook_id = str(artifacts[0].notebook_id) if artifacts else None

    manifest_artifacts: list[dict[str, object]] = []
    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(target_zip, "w", compression=compression) as zf:
        for artifact in completed:
            slug = str(artifact.id).replace(":", "-")
            export_paths = (
                artifact.export_paths if isinstance(artifact.export_paths, dict) else {}
            )
            formats_entry: list[dict[str, object]] = []
            for export_format, path_str in export_paths.items():
                path = Path(path_str) if path_str else None
                if not path or not path.is_file():
                    warnings.append(
                        f"Skipping missing export file for {artifact.id} "
                        f"({export_format}): {path_str}"
                    )
                    continue
                size = path.stat().st_size
                zf.write(path, f"{slug}/{export_format}/{path.name}")
                formats_entry.append(
                    {"format": export_format, "filename": path.name, "bytes": size}
                )
                file_count += 1
                total_bytes += size
            manifest_artifacts.append(
                {
                    "id": str(artifact.id),
                    "title": artifact.title,
                    "artifact_type": artifact.artifact_type,
                    "formats": formats_entry,
                }
            )

        manifest = {
            "notebook_id": notebook_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": manifest_artifacts,
            "skipped": skipped_ids,
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        zf.writestr("manifest.json", manifest_bytes)
        file_count += 1
        total_bytes += len(manifest_bytes)

    return BundleReport(
        file_count=file_count,
        total_bytes=total_bytes,
        artifact_count=len(completed),
        skipped=len(skipped_ids),
        warnings=warnings,
    )
