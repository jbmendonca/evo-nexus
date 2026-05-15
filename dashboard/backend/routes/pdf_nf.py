"""
Blueprint: /api/pdf-nf
Extrai dados de notas fiscais PDF usando Gemini com PDF nativo (sem conversão para imagem).
Fallback: pdfplumber + regex (local, sem API).
"""

import io
import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required

log = logging.getLogger(__name__)

# ─── Diretórios de trabalho ────────────────────────────────────────────────────
WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
PDF_OUTPUT_DIR = WORKSPACE / "dashboard" / "data" / "pdf_nf_output"
PDF_UPLOAD_DIR = WORKSPACE / "dashboard" / "data" / "pdf_nf_uploads"

PDF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PDF_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

bp = Blueprint("pdf_nf", __name__, url_prefix="/api/pdf-nf")

# ─── Constantes ────────────────────────────────────────────────────────────────
MAX_RETRIES = 3
INITIAL_BACKOFF = 2.0
INTER_REQUEST_DELAY = 0.5


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_documentai_client(location: str):
    """Retorna cliente Document AI configurado via JSON ou Application Default Credentials."""
    from google.cloud import documentai
    from google.oauth2 import service_account

    client_options = None
    if location and location != "us":
        client_options = {"api_endpoint": f"{location}-documentai.googleapis.com"}

    # Tentativa 1: Via JSON cru no .env (opcional)
    creds_json = os.environ.get("GCP_CREDENTIALS_JSON")
    if creds_json:
        try:
            creds_info = json.loads(creds_json)
            credentials = service_account.Credentials.from_service_account_info(creds_info)
            return documentai.DocumentProcessorServiceClient(
                credentials=credentials,
                client_options=client_options
            )
        except Exception as e:
            log.warning(f"GCP_CREDENTIALS_JSON malformado, caindo para fallback: {e}")

    # Tentativa 2: Application Default Credentials (ADC) nativo
    # O SDK localizará automaticamente GOOGLE_APPLICATION_CREDENTIALS ou a Service Account da VPS
    return documentai.DocumentProcessorServiceClient(
        client_options=client_options
    )


def _safe_float(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r"[R$\s]", "", str(val).strip())
    if ',' in s and '.' in s:
        s = s.replace(".", "").replace(",", ".")
    elif ',' in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _parse_br_currency(text: str) -> float:
    s = re.sub(r'[R$\s]', '', text.strip())
    if not s:
        return 0.0
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# ─── TIER 1: Google Cloud Document AI ──────────────────────────────────────────

def _extract_via_document_ai(pdf_bytes: bytes, filename: str) -> dict | None:
    """Envia o PDF ao Google Cloud Document AI (Invoice Parser/Custom Extractor)."""
    try:
        from google.cloud import documentai
        
        project_id = os.environ.get("GCP_PROJECT_ID")
        location = os.environ.get("GCP_LOCATION", "us")
        processor_id = os.environ.get("GCP_PROCESSOR_ID")
        
        if not all([project_id, location, processor_id]):
            log.error("Faltam variáveis do Document AI: GCP_PROJECT_ID, GCP_LOCATION, GCP_PROCESSOR_ID")
            return None

        client = _get_documentai_client(location)
        name = client.processor_path(project_id, location, processor_id)

        raw_document = documentai.RawDocument(
            content=pdf_bytes,
            mime_type="application/pdf",
        )
        request = documentai.ProcessRequest(
            name=name,
            raw_document=raw_document,
        )

        log.info(f"Enviando {filename} para Document AI ({processor_id})...")
        result = client.process_document(request=request)
        document = result.document

        extracted = {
            "data_nota": None,
            "valor_bruto": 0.0,
            "valor_liquido": 0.0,
            "total_impostos": 0.0,
            "razao_social_fornecedor": None
        }

        for entity in document.entities:
            entity_type = entity.type_
            entity_val = entity.mention_text or ""
            norm_val = entity.normalized_value.text if entity.normalized_value else None
            
            final_val = norm_val if norm_val else entity_val
            
            if entity_type == "invoice_date":
                extracted["data_nota"] = final_val
            elif entity_type == "total_amount":
                extracted["valor_bruto"] = _parse_br_currency(final_val)
            elif entity_type == "net_amount":
                extracted["valor_liquido"] = _parse_br_currency(final_val)
            elif entity_type == "total_tax_amount":
                extracted["total_impostos"] = _parse_br_currency(final_val)
            elif entity_type == "supplier_name":
                extracted["razao_social_fornecedor"] = final_val

        # Garantia de segurança contra líquidos zerados indevidamente
        if extracted["valor_liquido"] == 0.0 and extracted["valor_bruto"] > 0:
            extracted["valor_liquido"] = extracted["valor_bruto"]
            
        if extracted["valor_bruto"] == 0.0 and extracted["valor_liquido"] > 0:
            extracted["valor_bruto"] = extracted["valor_liquido"]

        log.info(f"✅ Document AI OK: {filename}")
        return extracted

    except Exception as e:
        log.error(f"❌ Document AI falhou para {filename}: {e}")
        return None


# ─── TIER 2: Regex local ──────────────────────────────────────────────────────

def _extract_via_regex(pdf_bytes: bytes, filename: str) -> dict | None:
    """Extrai dados via pdfplumber + regex (sem API)."""
    try:
        import pdfplumber
    except ImportError:
        return None

    text = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages[:5]:
                text += (page.extract_text() or "") + "\n"
    except Exception:
        return None

    if len(text.strip()) < 50:
        return None

    # Data
    data = None
    for pat in [
        r'(?:DATA\s*(?:DE|DA)?\s*EMISS[ÃA]O|DT\.?\s*EMISS[ÃA]O)\s*[:\s]*(\d{2}[/.\-]\d{2}[/.\-]\d{4})',
        r'EMISS[ÃA]O[\s\S]{0,30}?(\d{2}/\d{2}/\d{4})',
        r'(\d{2}/\d{2}/20\d{2})',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            data = m.group(1).replace('-', '/').replace('.', '/')
            break

    # Valor total
    valor = 0.0
    for pat in [
        r'VALOR\s*TOTAL\s*DA\s*NOTA\s*[:\s]*R?\$?\s*([\d.,]+)',
        r'VALOR\s*TOTAL\s*DOS\s*PRODUTOS\s*[:\s]*R?\$?\s*([\d.,]+)',
        r'TOTAL\s*DA\s*NOTA\s*[:\s]*R?\$?\s*([\d.,]+)',
        r'VL\.?\s*TOTAL\s*[:\s]*R?\$?\s*([\d.,]+)',
        r'VALOR\s*TOTAL\s*[:\s=]*R?\$?\s*([\d.,]+)',
    ]:
        for m in re.finditer(pat, text, re.IGNORECASE):
            v = _parse_br_currency(m.group(1))
            if v > valor:
                valor = v

    # Fornecedor
    fornecedor = None
    for pat in [
        r'(?:RAZ[ÃA]O\s*SOCIAL|NOME\s*/?RAZ[ÃA]O\s*SOCIAL)\s*[:\s]*\n?\s*([^\n]{5,80})',
        r'(?:EMITENTE|REMETENTE)\s*[:\s]*\n?\s*([^\n]{5,80})',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            cand = re.sub(r'\s+', ' ', m.group(1).strip()).rstrip(':.-,')
            if len(cand) >= 5 and not re.search(
                r'(TELEFONE|C\.?N\.?P\.?J|CPF|DANFE|NOTA\s*FISCAL|INSCRI|DESTINAT|ENDERE|DATA\s)',
                cand, re.IGNORECASE
            ):
                fornecedor = cand
                break

    # Impostos
    impostos = 0.0
    for pat in [
        r'VALOR\s*TOTAL\s*(?:DOS?\s*)?TRIBUTOS\s*[:\s]*R?\$?\s*([\d.,]+)',
        r'VALOR\s*APROXIMADO\s*(?:DOS?\s*)?TRIBUTOS\s*[:\s]*R?\$?\s*([\d.,]+)',
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            impostos = _parse_br_currency(m.group(1))
            break

    if valor == 0.0 and fornecedor is None and data is None:
        return None

    log.info(f"Regex OK: {filename} bruto={valor}")
    return {
        "data_nota": data,
        "valor_bruto": valor,
        "valor_liquido": valor,
        "total_impostos": impostos,
        "razao_social_fornecedor": fornecedor,
    }


# ─── Orquestrador ─────────────────────────────────────────────────────────────

def _extract_data(pdf_bytes: bytes, filename: str) -> dict:
    """Extrai dados: 1) Document AI nativo, 2) regex fallback."""
    result = {
        "data_nota": None, "valor_bruto": 0.0, "valor_liquido": 0.0,
        "total_impostos": 0.0, "razao_social_fornecedor": None,
        "nome_arquivo": filename, "status": "ok", "erro": None,
    }

    # TIER 1: Google Cloud Document AI
    extracted = _extract_via_document_ai(pdf_bytes, filename)
    if extracted:
        result["data_nota"] = extracted.get("data_nota")
        result["valor_bruto"] = _safe_float(extracted.get("valor_bruto", 0))
        result["valor_liquido"] = _safe_float(extracted.get("valor_liquido", 0))
        result["total_impostos"] = _safe_float(extracted.get("total_impostos", 0))
        result["razao_social_fornecedor"] = extracted.get("razao_social_fornecedor")
        if result["valor_liquido"] == 0.0 and result["valor_bruto"] > 0:
            result["valor_liquido"] = result["valor_bruto"]
        return result

    # TIER 2: Regex local (sem API)
    extracted = _extract_via_regex(pdf_bytes, filename)
    if extracted:
        result.update({k: v for k, v in extracted.items() if k in result})
        return result

    result["status"] = "erro"
    result["erro"] = "Não foi possível extrair dados do PDF via Document AI nem Regex."
    return result


# ─── Processamento do ZIP ──────────────────────────────────────────────────────

def _extract_zip(zip_path: Path, dest_dir: Path) -> list[str]:
    """Extrai ZIP para dest_dir, retorna lista de erros."""
    errors = []
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

                except Exception as e:
                    errors.append(f"[{member.filename}]: {e}")
    except zipfile.BadZipFile as e:
        errors.append(f"ZIP inválido: {e}")
    return errors


def process_pdf_zip(zip_path: Path, session_id: str) -> dict:
    """Processa ZIP de PDFs, extrai dados de cada nota fiscal."""
    out_base = PDF_OUTPUT_DIR / session_id
    extract_dir = out_base / "_extracted"
    out_base.mkdir(parents=True, exist_ok=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "total": 0, "ok": 0, "errors": 0,
        "notas": [], "error_list": [], "extract_errors": [],
    }

    extract_errors = _extract_zip(zip_path, extract_dir)
    results["extract_errors"] = extract_errors

    pdf_files = [p for p in extract_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
    results["total"] = len(pdf_files)

    if not pdf_files:
        results["error_list"].append({"file": zip_path.name, "error": "Nenhum PDF encontrado no ZIP."})
        shutil.rmtree(extract_dir, ignore_errors=True)
        return results

    for i, pdf_path in enumerate(pdf_files):
        if i > 0:
            time.sleep(INTER_REQUEST_DELAY)
        try:
            pdf_bytes = pdf_path.read_bytes()
            nota = _extract_data(pdf_bytes, pdf_path.name)
            results["notas"].append(nota)

            if nota["status"] == "erro":
                results["errors"] += 1
                results["error_list"].append({"file": pdf_path.name, "error": nota.get("erro", "Erro desconhecido")})
            else:
                results["ok"] += 1
        except Exception as e:
            results["errors"] += 1
            results["error_list"].append({"file": pdf_path.name, "error": str(e)})
            results["notas"].append({
                "nome_arquivo": pdf_path.name, "data_nota": None,
                "valor_bruto": 0.0, "valor_liquido": 0.0, "total_impostos": 0.0,
                "razao_social_fornecedor": None, "status": "erro", "erro": str(e),
            })

    result_file = out_base / "result.json"
    result_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(extract_dir, ignore_errors=True)
    return results


# ─── Geração do Excel ─────────────────────────────────────────────────────────

def _generate_excel(notas: list[dict]) -> bytes:
    """Gera arquivo Excel com os dados das notas fiscais."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError("openpyxl não instalada.")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Notas Fiscais"

    header_fill = PatternFill(start_color="1A2B4A", end_color="1A2B4A", fill_type="solid")
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    alt_fill = PatternFill(start_color="F0F4FA", end_color="F0F4FA", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=False)
    left_align = Alignment(horizontal="left", vertical="center", wrap_text=False)
    thin = Side(style="thin", color="D0D5DD")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    headers = ["Data da Nota", "Valor Bruto (R$)", "Valor Líquido (R$)", "Total de Impostos (R$)",
               "Razão Social do Fornecedor", "Nome do Arquivo", "Status"]
    col_widths = [16, 18, 18, 20, 45, 35, 10]

    for col_idx, (header, width) in enumerate(zip(headers, col_widths), start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
        cell.border = border
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[1].height = 20

    for row_idx, nota in enumerate(notas, start=2):
        is_alt = row_idx % 2 == 0
        fill = alt_fill if is_alt else None

        row_data = [
            nota.get("data_nota") or "",
            nota.get("valor_bruto") or 0.0,
            nota.get("valor_liquido") or 0.0,
            nota.get("total_impostos") or 0.0,
            nota.get("razao_social_fornecedor") or "",
            nota.get("nome_arquivo") or "",
            nota.get("status") or "ok",
        ]

        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = border
            if fill:
                cell.fill = fill
            if col_idx in (2, 3, 4):
                cell.number_format = '#,##0.00'
                cell.alignment = center_align
            elif col_idx == 1:
                cell.alignment = center_align
            else:
                cell.alignment = left_align

        ws.row_dimensions[row_idx].height = 18

    total_row = len(notas) + 2
    total_fill = PatternFill(start_color="00D17E", end_color="00D17E", fill_type="solid")
    total_font = Font(name="Calibri", bold=True, color="000000", size=11)

    ws.cell(row=total_row, column=1, value="TOTAL").font = total_font
    ws.cell(row=total_row, column=1).fill = total_fill
    ws.cell(row=total_row, column=1).alignment = center_align

    for col_idx in (2, 3, 4):
        cell = ws.cell(
            row=total_row, column=col_idx,
            value=f"=SUM({get_column_letter(col_idx)}2:{get_column_letter(col_idx)}{total_row-1})"
        )
        cell.number_format = '#,##0.00'
        cell.font = total_font
        cell.fill = total_fill
        cell.alignment = center_align
        cell.border = border

    ws.cell(row=total_row, column=5, value=f"{len(notas)} nota(s)").font = total_font
    ws.cell(row=total_row, column=5).fill = total_fill
    ws.cell(row=total_row, column=5).alignment = left_align

    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ─── Rotas ────────────────────────────────────────────────────────────────────

@bp.route("/upload", methods=["POST"])
@login_required
def upload():
    """Recebe ZIP com PDFs, processa e retorna JSON."""
    files = request.files.getlist("zipfile")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    zips = [f for f in files if f.filename.lower().endswith(".zip")]
    if not zips:
        return jsonify({"error": "Envie ao menos um arquivo .zip com PDFs."}), 400

    session_id = str(uuid.uuid4())
    saved_zips = []

    try:
        for i, f in enumerate(zips):
            zip_path = PDF_UPLOAD_DIR / f"{session_id}_{i}.zip"
            f.save(zip_path)
            saved_zips.append(zip_path)

        if len(saved_zips) == 1:
            stats = process_pdf_zip(saved_zips[0], session_id)
        else:
            combined_path = PDF_UPLOAD_DIR / f"{session_id}_combined.zip"
            with zipfile.ZipFile(combined_path, "w", zipfile.ZIP_STORED) as czf:
                for zp in saved_zips:
                    czf.write(zp, zp.name)
            stats = process_pdf_zip(combined_path, session_id)
            combined_path.unlink(missing_ok=True)

    except Exception as e:
        log.exception("Erro no processamento de NF PDF")
        return jsonify({"error": f"Erro interno: {e}"}), 500
    finally:
        for zp in saved_zips:
            zp.unlink(missing_ok=True)

    return jsonify({"session_id": session_id, "stats": stats})


@bp.route("/export/<session_id>", methods=["GET"])
@login_required
def export_excel(session_id):
    """Gera e retorna planilha Excel com os dados extraídos."""
    if not re.match(r"^[a-f0-9\-]{36}$", session_id):
        return jsonify({"error": "ID inválido"}), 400

    result_file = PDF_OUTPUT_DIR / session_id / "result.json"
    if not result_file.exists():
        return jsonify({"error": "Sessão não encontrada ou expirada."}), 404

    try:
        data = json.loads(result_file.read_text(encoding="utf-8"))
        notas = data.get("notas", [])

        excel_bytes = _generate_excel(notas)
        buf = io.BytesIO(excel_bytes)
        buf.seek(0)

        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="notas_fiscais.xlsx",
        )
    except Exception as e:
        log.exception("Erro ao gerar Excel")
        return jsonify({"error": f"Erro ao gerar Excel: {e}"}), 500


@bp.route("/cleanup/<session_id>", methods=["DELETE"])
@login_required
def cleanup(session_id):
    """Remove arquivos temporários de uma sessão."""
    if not re.match(r"^[a-f0-9\-]{36}$", session_id):
        return jsonify({"error": "ID inválido"}), 400
    shutil.rmtree(PDF_OUTPUT_DIR / session_id, ignore_errors=True)
    return jsonify({"ok": True})
