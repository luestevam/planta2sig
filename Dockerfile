# GeoDoc SIG — imagem para Hugging Face Spaces (Docker) ou qualquer host de contêiner.
FROM python:3.11-slim

# Tesseract: usado pelo OCR do PyMuPDF (dados de idioma em models/tessdata)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# O Hugging Face executa o contêiner com o usuário 1000
RUN useradd -m -u 1000 user
USER user
ENV PATH=/home/user/.local/bin:$PATH \
    GEODOC_DATA=/tmp/geodoc-data \
    GEODOC_CACHE=/tmp/geodoc-cache \
    OMP_NUM_THREADS=2
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

EXPOSE 7860
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
