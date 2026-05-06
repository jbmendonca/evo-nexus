import io
import json
import logging
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required

# Ensure the EvoNexus workspace path
WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
OUTPUT_DIR = WORKSPACE / "dashboard" / "data" / "nfe_output"
UPLOAD_DIR = WORKSPACE / "dashboard" / "data" / "nfe_uploads"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger(__name__)

bp = Blueprint("nfe", __name__, url_prefix="/api/nfe")

# ─── Parser NF-e ──────────────────────────────────────────────────────────────

def _strip_ns(tag: str) -> str:
    """Remove namespace de uma tag XML."""
    return re.sub(r"\{[^}]+\}", "", tag)

def _find_ie(root) -> tuple[str, str]:
    """
    Extrai (CNPJ, IE) do emitente de uma NF-e.
    Tenta namespace oficial, sem namespace e varredura completa da árvore.
    """
    NF_NS = "http://www.portalfiscal.inf.br/nfe"

    # Estratégia 1 e 2: caminhos explícitos com e sem namespace
    for c_path, i_path in [
        (f".//{{{NF_NS}}}emit/{{{NF_NS}}}CNPJ", f".//{{{NF_NS}}}emit/{{{NF_NS}}}IE"),
        (".//emit/CNPJ",                          (".//emit/IE")),
    ]:
        c_el = root.find(c_path)
        i_el = root.find(i_path)
        if c_el is not None or i_el is not None:
            cnpj = (c_el.text or "").strip() if c_el is not None else ""
            ie   = (i_el.text or "").strip() if i_el is not None else ""
            if cnpj or ie:
                return cnpj, ie

    # Estratégia 3: varredura completa — procura elemento <emit> em qualquer nível
    for elem in root.iter():
        if _strip_ns(elem.tag) == "emit":
            cnpj = ie = ""
            for child in elem:
                ctag = _strip_ns(child.tag)
                if ctag == "CNPJ":
                    cnpj = (child.text or "").strip()
                elif ctag == "IE":
                    ie   = (child.text or "").strip()
            if cnpj or ie:
                return cnpj, ie

    return "", ""

def _is_xml(path: Path) -> bool:
    return path.suffix.lower() == ".xml"

# ─── Extração recursiva ───────────────────────────────────────────────────────

def _extract_all_zips(zip_path: Path, dest_dir: Path, depth: int = 0) -> list[str]:
    """Extrai um ZIP (e ZIPs aninhados dentro dele) para dest_dir."""
    errors = []
    if depth > 5:
        return ["Nível de ZIP aninhado muito profundo (>5)"]

    extracted_nested_zips: list[Path] = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                try:
                    try:
                        fname = member.filename.encode("cp437").decode("utf-8")
                    except (UnicodeDecodeError, UnicodeEncodeError):
                        fname = member.filename

                    parts = Path(fname).parts
                    safe_parts = [p for p in parts if p not in ("", ".", "..")]
                    if not safe_parts:
                        continue
                    target = dest_dir.joinpath(*safe_parts)

                    if member.is_dir() or fname.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                        continue

                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)

                    if target.suffix.lower() == ".zip":
                        extracted_nested_zips.append(target)

                except Exception as e:
                    errors.append(f"[extração] {member.filename}: {e}")

    except zipfile.BadZipFile as e:
        errors.append(f"ZIP inválido ({zip_path.name}): {e}")
        return errors

    for nested_zip in extracted_nested_zips:
        if not nested_zip.exists():
            continue
        nested_dest = nested_zip.parent / nested_zip.stem
        nested_dest.mkdir(exist_ok=True)
        nested_errors = _extract_all_zips(nested_zip, nested_dest, depth + 1)
        errors.extend(nested_errors)
        try:
            nested_zip.unlink()
        except OSError:
            pass

    return errors

# ─── Processamento principal ──────────────────────────────────────────────────

def process_zip(zip_path: Path, session_id: str) -> dict:
    """Extrai ZIP(s), lê XMLs e organiza por IE do emitente."""
    out_base = OUTPUT_DIR / session_id
    extract_dir = out_base / "_extracted"
    out_base.mkdir(parents=True, exist_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "total": 0,
        "ok": 0,
        "errors": 0,
        "skipped": 0,
        "groups": {},
        "error_list": [],
        "extract_errors": [],
    }

    extract_errors = _extract_all_zips(zip_path, extract_dir)
    stats["extract_errors"] = extract_errors

    xml_files = [p for p in extract_dir.rglob("*") if p.is_file() and _is_xml(p)]
    stats["total"] = len(xml_files)

    for xf in xml_files:
        try:
            content = xf.read_bytes()
            if content.startswith(b"\xef\xbb\xbf"):
                content = content[3:]
            root = ET.fromstring(content)

            cnpj, ie = _find_ie(root)
            ie_upper = (ie or "").upper().strip()
            if not ie_upper or ie_upper in ("ISENTO", "0", "00", "N/A", "NA", "SEM IE"):
                ie = "SEM_IE"
                ie_safe = "SEM_IE"
            else:
                ie_safe = re.sub(r"[^\w\-]", "", ie)

            folder_name = f"IE_{ie_safe}"
            dest_dir = out_base / folder_name
            dest_dir.mkdir(exist_ok=True)

            dest_file = dest_dir / xf.name
            if dest_file.exists():
                dest_file = dest_dir / f"{xf.stem}_{uuid.uuid4().hex[:6]}.xml"

            shutil.copy2(xf, dest_file)

            if folder_name not in stats["groups"]:
                stats["groups"][folder_name] = {
                    "cnpj": cnpj, "ie": ie, "count": 0, "files": []
                }
            stats["groups"][folder_name]["count"] += 1
            stats["groups"][folder_name]["files"].append(xf.name)
            stats["ok"] += 1

        except Exception as e:
            stats["errors"] += 1
            stats["error_list"].append({"file": xf.name, "error": str(e)})

    shutil.rmtree(extract_dir, ignore_errors=True)
    return stats

# ─── Rotas ────────────────────────────────────────────────────────────────────

@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    files = request.files.getlist("zipfile")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    zips = [f for f in files if f.filename.lower().endswith(".zip")]
    if not zips:
        return jsonify({"error": "Envie ao menos um arquivo .zip válido."}), 400

    session_id = str(uuid.uuid4())
    saved_zips = []

    try:
        for i, f in enumerate(zips):
            zip_path = UPLOAD_DIR / f"{session_id}_{i}.zip"
            f.save(zip_path)
            saved_zips.append(zip_path)

        if len(saved_zips) == 1:
            stats = process_zip(saved_zips[0], session_id)
        else:
            combined_path = UPLOAD_DIR / f"{session_id}_combined.zip"
            with zipfile.ZipFile(combined_path, "w", zipfile.ZIP_STORED) as czf:
                for zp in saved_zips:
                    czf.write(zp, zp.name)
            stats = process_zip(combined_path, session_id)
            combined_path.unlink(missing_ok=True)

    except zipfile.BadZipFile as e:
        return jsonify({"error": f"Arquivo ZIP inválido: {e}"}), 400
    except Exception as e:
        log.exception("Erro no processamento de NFe")
        return jsonify({"error": f"Erro interno: {e}"}), 500
    finally:
        for zp in saved_zips:
            zp.unlink(missing_ok=True)

    return jsonify({"session_id": session_id, "stats": stats})

@bp.route("/download/<session_id>", methods=["GET"])
@login_required
def download_all(session_id):
    if not re.match(r"^[a-f0-9\-]{36}$", session_id):
        return "ID inválido", 400
    out_base = OUTPUT_DIR / session_id
    if not out_base.is_dir():
        return "Sessão não encontrada", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in out_base.rglob("*"):
            if fpath.is_file() and _is_xml(fpath):
                zf.write(fpath, fpath.relative_to(out_base))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name="nfe_separadas.zip")

@bp.route("/download/<session_id>/<ie_folder>", methods=["GET"])
@login_required
def download_group(session_id, ie_folder):
    if not re.match(r"^[a-f0-9\-]{36}$", session_id):
        return "ID inválido", 400
    if not re.match(r"^IE_[\w\-]+$", ie_folder):
        return "Pasta inválida", 400
    dest_dir = OUTPUT_DIR / session_id / ie_folder
    if not dest_dir.is_dir():
        return "Grupo não encontrado", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in dest_dir.iterdir():
            if fpath.is_file() and _is_xml(fpath):
                zf.write(fpath, fpath.name)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"{ie_folder}.zip")

@bp.route("/cleanup/<session_id>", methods=["DELETE"])
@login_required
def cleanup(session_id):
    if not re.match(r"^[a-f0-9\-]{36}$", session_id):
        return jsonify({"error": "ID inválido"}), 400
    shutil.rmtree(OUTPUT_DIR / session_id, ignore_errors=True)
    return jsonify({"ok": True})
