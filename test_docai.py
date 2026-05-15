import os, sys
try:
    from google.cloud import documentai
    from google.oauth2 import service_account
    print("SDK version:", documentai.__version__)

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    print("Creds path:", creds_path)
    print("File exists:", os.path.isfile(creds_path) if creds_path else "N/A")

    pid = os.environ.get("GCP_PROJECT_ID", "")
    loc = os.environ.get("GCP_LOCATION", "us")
    proc = os.environ.get("GCP_PROCESSOR_ID", "")
    print(f"Project: {pid}, Location: {loc}, Processor: {proc}")

    if creds_path and os.path.isfile(creds_path):
        creds = service_account.Credentials.from_service_account_file(creds_path)
        client = documentai.DocumentProcessorServiceClient(credentials=creds)
    else:
        client = documentai.DocumentProcessorServiceClient()

    name = client.processor_path(pid, loc, proc)
    print("Processor path:", name)

    # Teste com PDF dummy (1 pixel)
    import struct
    dummy_pdf = b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 3 3]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"

    raw_doc = documentai.RawDocument(content=dummy_pdf, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=name, raw_document=raw_doc)
    result = client.process_document(request=request)
    print("API CALL OK! Entities:", len(result.document.entities))
    print("Text:", result.document.text[:200] if result.document.text else "(empty)")
except Exception as e:
    print(f"ERRO: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
