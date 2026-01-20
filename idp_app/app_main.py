import hashlib
import io
import json
import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from azure.identity import DefaultAzureCredential
from dotenv import find_dotenv, load_dotenv
from PIL import Image, ImageDraw
import streamlit as st

from content_understanding_client import AzureContentUnderstandingClient


logging.basicConfig(level=logging.INFO)
load_dotenv(find_dotenv())

API_VERSION = "2025-11-01"
AZURE_AI_ENDPOINT = os.getenv("AZURE_AI_ENDPOINT", "").strip()
AZURE_AI_API_KEY = os.getenv("AZURE_AI_API_KEY", "").strip()

CLASSIFIER_ID = "classifier_idp"
ANALYZER_INVOICES_ID = "analyzer_invoices"
ANALYZER_BANK_STATEMENTS_ID = "analyzer_bank_statements"
ANALYZER_LOAN_ID = "analyzer_loan"

MAX_FILE_MB = 20


def token_provider() -> str:
    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")
    return token.token


def build_client() -> AzureContentUnderstandingClient:
    if not AZURE_AI_ENDPOINT:
        raise ValueError("AZURE_AI_ENDPOINT is missing.")

    return AzureContentUnderstandingClient(
        endpoint=AZURE_AI_ENDPOINT,
        api_version=API_VERSION,
        subscription_key=AZURE_AI_API_KEY if AZURE_AI_API_KEY else None,
        token_provider=token_provider if not AZURE_AI_API_KEY else None,
        x_ms_useragent="cu-idp-review-ui",
    )


def render_pdf_pages(pdf_bytes: bytes, zoom: float = 2.0) -> List[Image.Image]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages: List[Image.Image] = []
    mat = fitz.Matrix(zoom, zoom)
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        pages.append(img)
    doc.close()
    return pages


def _coerce_polygon_to_bbox(polygon: List[float]) -> Tuple[float, float, float, float]:
    xs = polygon[0::2]
    ys = polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def parse_cu_source_string(source: str) -> Optional[Dict[str, Any]]:
    if not source or not isinstance(source, str):
        return None

    source = source.strip()
    if len(source) < 4 or "(" not in source or not source.endswith(")"):
        return None

    kind = source[0]
    inner = source[source.find("(") + 1 : -1]
    parts = [p.strip() for p in inner.split(",") if p.strip()]
    if len(parts) < 9:
        return None

    try:
        page_num = int(float(parts[0]))
        coords = [float(x) for x in parts[1:]]
        if len(coords) < 8:
            return None
        coords = coords[:8]
    except Exception:
        return None

    bbox = _coerce_polygon_to_bbox(coords)
    return {
        "kind": kind,
        "pageNumber": page_num,
        "polygon": coords,
        "bbox": bbox,
    }


def _normalize_regions_from_source(src: Dict[str, Any]) -> List[Dict[str, Any]]:
    regions: List[Dict[str, Any]] = []

    page_num = src.get("pageNumber") or src.get("page") or src.get("pageIndex")
    bounding_regions = src.get("boundingRegions") or src.get("regions") or []

    if isinstance(bounding_regions, list) and bounding_regions:
        for br in bounding_regions:
            p = br.get("pageNumber") or br.get("page") or page_num
            poly = br.get("polygon") or br.get("points")
            bbox = br.get("boundingBox")
            if poly and isinstance(poly, list) and len(poly) >= 8:
                x0, y0, x1, y1 = _coerce_polygon_to_bbox(poly)
                regions.append({"pageNumber": int(p), "polygon": poly, "bbox": (x0, y0, x1, y1)})
            elif bbox and isinstance(bbox, list) and len(bbox) == 4:
                x0, y0, x1, y1 = bbox
                regions.append({"pageNumber": int(p), "polygon": None, "bbox": (x0, y0, x1, y1)})
    else:
        poly = src.get("polygon") or src.get("points")
        bbox = src.get("boundingBox")
        if poly and isinstance(poly, list) and len(poly) >= 8 and page_num:
            x0, y0, x1, y1 = _coerce_polygon_to_bbox(poly)
            regions.append({"pageNumber": int(page_num), "polygon": poly, "bbox": (x0, y0, x1, y1)})
        elif bbox and isinstance(bbox, list) and len(bbox) == 4 and page_num:
            x0, y0, x1, y1 = bbox
            regions.append({"pageNumber": int(page_num), "polygon": None, "bbox": (x0, y0, x1, y1)})

    return regions


def _gather_sources_recursive(node: Any) -> List[Any]:
    sources: List[Any] = []

    if isinstance(node, dict):
        if "source" in node:
            sources.append(node.get("source"))
        if "sources" in node:
            sources.append(node.get("sources"))
        if "evidence" in node:
            sources.append(node.get("evidence"))

        for v in node.values():
            if isinstance(v, (dict, list)):
                sources.extend(_gather_sources_recursive(v))

    elif isinstance(node, list):
        for item in node:
            sources.extend(_gather_sources_recursive(item))

    return sources


def _sources_to_regions(raw_sources: List[Any]) -> List[Dict[str, Any]]:
    regions: List[Dict[str, Any]] = []

    def _consume_source_item(item: Any) -> None:
        if isinstance(item, str):
            parsed = parse_cu_source_string(item)
            if parsed:
                regions.append(parsed)
        elif isinstance(item, dict):
            regions.extend(_normalize_regions_from_source(item))
        elif isinstance(item, list):
            for it in item:
                _consume_source_item(it)

    for raw in raw_sources:
        _consume_source_item(raw)

    deduped = []
    seen = set()
    for r in regions:
        key = (r.get("pageNumber"), r.get("bbox"))
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped


def _pick_value_from_field_obj(field_obj: Dict[str, Any]) -> Any:
    if "valueString" in field_obj:
        return field_obj["valueString"]
    if "valueNumber" in field_obj:
        return field_obj["valueNumber"]
    if "valueBoolean" in field_obj:
        return field_obj["valueBoolean"]
    if "valueDate" in field_obj:
        return field_obj["valueDate"]
    if "valueArray" in field_obj:
        return field_obj["valueArray"]
    if "valueObject" in field_obj:
        return field_obj["valueObject"]
    if "value" in field_obj:
        return field_obj["value"]
    return None


def _find_fields_map(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        if obj and all(isinstance(v, dict) for v in obj.values()):
            sample = next(iter(obj.values()))
            if isinstance(sample, dict) and (
                "value" in sample or "valueString" in sample or "sources" in sample or "source" in sample
            ):
                return obj
        for v in obj.values():
            found = _find_fields_map(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _find_fields_map(it)
            if found:
                return found
    return None


def extract_fields_with_locations(analysis_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    contents = (analysis_result.get("result") or {}).get("contents") or []
    if not contents:
        contents = analysis_result.get("contents") or []

    content = None
    for c in contents:
        if isinstance(c, dict) and c.get("kind") in ("document", "text", None):
            content = c
            break
    if content is None and contents:
        content = contents[0]

    candidate_fields = []
    if isinstance(content, dict):
        for k in ("fields", "extractedFields", "output", "data"):
            if k in content and isinstance(content[k], dict):
                candidate_fields.append(content[k])
        extraction = content.get("extraction") or content.get("result") or {}
        if isinstance(extraction, dict) and isinstance(extraction.get("fields"), dict):
            candidate_fields.append(extraction["fields"])

    fields_map = candidate_fields[0] if candidate_fields else _find_fields_map(analysis_result)
    if not fields_map or not isinstance(fields_map, dict):
        return []

    extracted: List[Dict[str, Any]] = []
    for field_name, field_obj in fields_map.items():
        if not isinstance(field_obj, dict):
            continue

        value = _pick_value_from_field_obj(field_obj)

        sources = field_obj.get("sources") or field_obj.get("source") or field_obj.get("evidence") or []
        if not isinstance(sources, list):
            sources = [sources]
        regions = _sources_to_regions(sources)

        if not regions:
            nested_sources = _gather_sources_recursive(field_obj)
            regions = _sources_to_regions(nested_sources)

        extracted.append(
            {
                "name": field_name,
                "value": value,
                "regions": regions,
            }
        )

    return extracted


def draw_regions_on_page(
    page_img: Image.Image,
    regions: List[Dict[str, Any]],
    page_width_doc: Optional[float] = None,
    page_height_doc: Optional[float] = None,
) -> Image.Image:
    img = page_img.copy()
    draw = ImageDraw.Draw(img)
    W, H = img.size
    sx = (W / page_width_doc) if page_width_doc else 1.0
    sy = (H / page_height_doc) if page_height_doc else 1.0

    for r in regions:
        x0, y0, x1, y1 = r["bbox"]
        x0p, y0p, x1p, y1p = x0 * sx, y0 * sy, x1 * sx, y1 * sy
        draw.rectangle([x0p, y0p, x1p, y1p], outline="red", width=3)

    return img


def try_get_page_dimensions(result: Dict[str, Any], page_num: int) -> Tuple[Optional[float], Optional[float]]:
    try:
        contents = (result.get("result") or {}).get("contents") or []
        if not contents:
            return None, None
        content0 = contents[0]
        pages = content0.get("pages") or []
        if isinstance(pages, list) and 1 <= page_num <= len(pages):
            page_meta = pages[page_num - 1]
            return page_meta.get("width"), page_meta.get("height")
    except Exception:
        pass
    return None, None


def _pretty_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def _summarize_value(value: Any) -> str:
    if isinstance(value, list):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return "Details"
    return _pretty_value(value) or "(empty)"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _find_first_category(obj: Any) -> Optional[Tuple[str, Optional[float]]]:
    if isinstance(obj, dict):
        if "category" in obj and isinstance(obj["category"], str):
            conf = obj.get("confidence") or obj.get("score") or obj.get("probability")
            try:
                conf = float(conf) if conf is not None else None
            except Exception:
                conf = None
            return obj["category"], conf
        if "label" in obj and isinstance(obj["label"], str):
            conf = obj.get("confidence") or obj.get("score") or obj.get("probability")
            try:
                conf = float(conf) if conf is not None else None
            except Exception:
                conf = None
            return obj["label"], conf
        for v in obj.values():
            found = _find_first_category(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _find_first_category(it)
            if found:
                return found
    return None


def parse_classifier_output(result: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    found = _find_first_category(result)
    if found:
        return found
    return None, None


def _find_usage_block(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        if "usage" in obj and isinstance(obj["usage"], dict):
            return obj["usage"]
        for v in obj.values():
            found = _find_usage_block(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _find_usage_block(it)
            if found:
                return found
    return None


def _extract_usage_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    usage = _find_usage_block(result) or {}
    tokens = usage.get("tokens") or {}
    model_names = []
    total_input = 0
    total_output = 0

    if isinstance(tokens, dict):
        for k, v in tokens.items():
            if isinstance(v, (int, float)):
                if k.endswith("-input"):
                    total_input += int(v)
                    model_names.append(k.replace("-input", ""))
                elif k.endswith("-output"):
                    total_output += int(v)
                    model_names.append(k.replace("-output", ""))
    model_names = sorted({m for m in model_names if m})

    return {
        "models": model_names,
        "input_tokens": total_input,
        "output_tokens": total_output,
        "raw_usage": usage,
    }


def _estimate_cost_usd(input_tokens: int, output_tokens: int) -> Optional[float]:
    try:
        in_price = float(os.getenv("CU_PRICE_PER_1K_INPUT", "").strip())
        out_price = float(os.getenv("CU_PRICE_PER_1K_OUTPUT", "").strip())
    except Exception:
        return None
    return (input_tokens / 1000.0) * in_price + (output_tokens / 1000.0) * out_price


def _ensure_session_state() -> None:
    if "analysis_cache_key" not in st.session_state:
        st.session_state.analysis_cache_key = None
    if "analysis_result" not in st.session_state:
        st.session_state.analysis_result = None
    if "analysis_fields" not in st.session_state:
        st.session_state.analysis_fields = None
    if "analysis_meta" not in st.session_state:
        st.session_state.analysis_meta = None
    if "pdf_bytes" not in st.session_state:
        st.session_state.pdf_bytes = None
    if "render_cache_key" not in st.session_state:
        st.session_state.render_cache_key = None
    if "page_images" not in st.session_state:
        st.session_state.page_images = None


_ensure_session_state()


st.set_page_config(page_title="Intelligent Document Review", layout="wide")
st.title("Intelligent Document Review and Validation")

with st.sidebar:
    st.header("Inputs")
    mode = st.radio("Mode", ["Live (Azure)", "Offline (saved JSON)"])

    if mode == "Live (Azure)":
        classifier_id = st.text_input("Classifier ID", value=CLASSIFIER_ID, disabled=True)
        st.subheader("Analyzer mapping")
        label_a = st.text_input("Label for Type A", value="Invoices", disabled=True)
        analyzer_a = st.text_input("Analyzer ID for Type A", value=ANALYZER_INVOICES_ID, disabled=True)
        label_b = st.text_input("Label for Type B", value="Bank Statements", disabled=True)
        analyzer_b = st.text_input("Analyzer ID for Type B", value=ANALYZER_BANK_STATEMENTS_ID, disabled=True)
        label_c = st.text_input("Label for Type C", value="Loan Application Form", disabled=True)
        analyzer_c = st.text_input("Analyzer ID for Type C", value=ANALYZER_LOAN_ID, disabled=True)
    else:
        json_path = st.text_input("Path to saved JSON result", value="test_output/sample_result.json")
        pdf_path = st.text_input("Path to source PDF", value="data/sample.pdf")

    zoom = st.slider("PDF render zoom", min_value=1.0, max_value=4.0, value=2.0, step=0.5)
    run_btn = st.button("Run", type="primary")

uploaded = None
if mode == "Live (Azure)":
    uploaded = st.file_uploader("Upload a PDF", type=["pdf"])

if run_btn:
    if mode == "Live (Azure)":
        if not uploaded:
            st.error("Please upload a PDF.")
            st.stop()
        if not classifier_id.strip():
            st.error("Classifier ID is required.")
            st.stop()

        analyzer_map = {
            label_a.strip(): analyzer_a.strip(),
            label_b.strip(): analyzer_b.strip(),
            label_c.strip(): analyzer_c.strip(),
        }
        if not all(analyzer_map.values()):
            st.error("Analyzer IDs are required for all document types.")
            st.stop()

        pdf_bytes = uploaded.read()
        file_size_mb = len(pdf_bytes) / (1024 * 1024)
        if file_size_mb > MAX_FILE_MB:
            st.error(f"File is too large. Max size is {MAX_FILE_MB} MB.")
            st.stop()

        pdf_hash = _hash_bytes(pdf_bytes)
        analysis_key = f"live:{pdf_hash}:{classifier_id}:{sorted(analyzer_map.items())}"

        if st.session_state.analysis_cache_key != analysis_key:
            with st.spinner("Classifying document..."):
                client = build_client()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                try:
                    classify_resp = client.begin_analyze_binary(
                        analyzer_id=classifier_id.strip(),
                        file_location=tmp_path,
                    )
                    classify_result = client.poll_result(classify_resp)
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

            doc_label, doc_conf = parse_classifier_output(classify_result)
            if not doc_label:
                st.error("Could not determine document type from classifier output.")
                st.stop()

            analyzer_id = analyzer_map.get(doc_label)
            if not analyzer_id:
                st.error(f"No analyzer mapped for document label '{doc_label}'.")
                st.stop()

            with st.spinner("Extracting fields..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                try:
                    analyze_resp = client.begin_analyze_binary(analyzer_id=analyzer_id, file_location=tmp_path)
                    analyze_result = client.poll_result(analyze_resp)
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

            fields = extract_fields_with_locations(analyze_result)
            if not fields:
                st.error("No fields found in analyzer output.")
                st.stop()

            st.session_state.analysis_cache_key = analysis_key
            st.session_state.analysis_result = analyze_result
            st.session_state.analysis_fields = fields
            usage_summary = _extract_usage_summary(analyze_result)
            st.session_state.analysis_meta = {
                "doc_label": doc_label,
                "doc_conf": doc_conf,
                "analyzer_id": analyzer_id,
                "classification_result": classify_result,
                "usage_summary": usage_summary,
            }
            st.session_state.pdf_bytes = pdf_bytes

        if st.session_state.render_cache_key != f"{_hash_bytes(st.session_state.pdf_bytes)}:{zoom}":
            with st.spinner("Rendering PDF pages..."):
                st.session_state.page_images = render_pdf_pages(st.session_state.pdf_bytes, zoom=float(zoom))
                st.session_state.render_cache_key = f"{_hash_bytes(st.session_state.pdf_bytes)}:{zoom}"

    else:
        if not os.path.exists(json_path):
            st.error(f"JSON file not found: {json_path}")
            st.stop()
        if not os.path.exists(pdf_path):
            st.error(f"PDF file not found: {pdf_path}")
            st.stop()

        with open(json_path, "r", encoding="utf-8") as f:
            analyze_result = json.load(f)
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        fields = extract_fields_with_locations(analyze_result)
        if not fields:
            st.error("No fields found in JSON output.")
            st.stop()

        st.session_state.analysis_cache_key = f"offline:{json_path}"
        st.session_state.analysis_result = analyze_result
        st.session_state.analysis_fields = fields
        st.session_state.analysis_meta = {
            "doc_label": "Offline",
            "analyzer_id": "Offline",
            "usage_summary": _extract_usage_summary(analyze_result),
        }
        st.session_state.pdf_bytes = pdf_bytes

        if st.session_state.render_cache_key != f"{_hash_bytes(pdf_bytes)}:{zoom}":
            with st.spinner("Rendering PDF pages..."):
                st.session_state.page_images = render_pdf_pages(pdf_bytes, zoom=float(zoom))
                st.session_state.render_cache_key = f"{_hash_bytes(pdf_bytes)}:{zoom}"

    st.success("Document loaded.")

if st.session_state.analysis_fields and st.session_state.page_images:
    fields = st.session_state.analysis_fields
    result = st.session_state.analysis_result
    page_images = st.session_state.page_images
    meta = st.session_state.analysis_meta or {}

    doc_label = meta.get("doc_label", "Unknown")
    doc_conf = meta.get("doc_conf")
    conf_txt = f"{doc_conf:.2f}" if isinstance(doc_conf, (int, float)) else "N/A"
    st.caption(f"Document type: {doc_label} (confidence: {conf_txt}) | Analyzer: {meta.get('analyzer_id', 'Unknown')}")
    usage = meta.get("usage_summary") or {}
    if usage:
        models = ", ".join(usage.get("models") or []) or "Unknown"
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens
        cost = _estimate_cost_usd(input_tokens, output_tokens)
        if cost is None:
            cost_txt = "Set CU_PRICE_PER_1K_INPUT and CU_PRICE_PER_1K_OUTPUT to estimate cost."
        else:
            cost_txt = f"Estimated cost: ${cost:.4f}"
        st.caption(
            f"Model(s): {models} | Tokens: {total_tokens} (in {input_tokens} / out {output_tokens}) | {cost_txt}"
        )

    left, right = st.columns([0.35, 0.65], gap="large")

    with left:
        st.subheader("Extracted Fields")
        field_labels: List[str] = []
        for fobj in fields:
            preview = _summarize_value(fobj.get("value"))
            if len(preview) > 80:
                preview = preview[:77] + "..."
            field_labels.append(f"{fobj['name']}: {preview}")

        selected_idx = st.radio(
            "Select a field to highlight",
            list(range(len(fields))),
            format_func=lambda i: field_labels[i],
            key="selected_field_idx",
        )
        selected_field = fields[selected_idx]

        st.markdown("#### Selected Field")
        st.write("Field:", selected_field["name"])
        value = selected_field.get("value")
        st.write("Value:", _summarize_value(value))

        if selected_field.get("regions"):
            pages_for_field = sorted({r["pageNumber"] for r in selected_field["regions"]})
            st.caption("Found on page(s): " + ", ".join(map(str, pages_for_field)))
        else:
            st.caption("No visible location available to highlight for this field.")

    with right:
        st.subheader("Document Viewer")
        default_page = 1
        if selected_field["regions"]:
            default_page = selected_field["regions"][0]["pageNumber"]
        if "page_num" not in st.session_state:
            st.session_state.page_num = int(default_page)

        page_num = st.number_input(
            "Page",
            min_value=1,
            max_value=len(page_images),
            value=int(st.session_state.page_num),
            step=1,
            key="page_num_input",
        )
        st.session_state.page_num = int(page_num)

        page_img = page_images[page_num - 1]
        page_regions = [r for r in selected_field["regions"] if r.get("pageNumber") == page_num]
        page_w, page_h = try_get_page_dimensions(result, int(page_num))

        if page_regions:
            overlay_img = draw_regions_on_page(page_img, page_regions, page_width_doc=page_w, page_height_doc=page_h)
            st.image(overlay_img, use_container_width=True)
        else:
            st.image(page_img, use_container_width=True)
            st.info("No regions for this field on the selected page.")
else:
    st.info("Use the Run button after providing inputs to load a document.")
