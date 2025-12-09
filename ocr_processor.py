from PIL import Image
import pytesseract
import re
import os


pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'



RECORTE_SUCATA_FIXO = (25, 210, 80, 250) 
TEXTO_ALVO = "SUCATA E BORRACHAS" 

DEBUG_IMAGE_PATH = "recorte_teste_ocr_final.png"


BINARIZATION_THRESHOLD = 150 


def extrair_quantidade_sucata(img: Image):
    
    print(f"\n--- DEBUG OCR INICIADO ---")
    print(f"Coordenadas de Recorte: {RECORTE_SUCATA_FIXO}")
    
    try:
        
        img_recortada = img.crop(RECORTE_SUCATA_FIXO)
        
        
        
      
        largura = img_recortada.width * 4
        altura = img_recortada.height * 4
        img_processada = img_recortada.resize((largura, altura), Image.Resampling.LANCZOS)
        
     
        img_processada = img_processada.convert('L') 
        
        
        img_processada = img_processada.point(lambda x: 255 if x > BINARIZATION_THRESHOLD else 0)
        img_processada = img_processada.convert('L') 
        
        
        img_processada.save(DEBUG_IMAGE_PATH)
        print(f"IMAGEM DE DEBUG SALVA: Verifique o arquivo '{DEBUG_IMAGE_PATH}' (Binarizado).")
        
        
        tesseract_config = '--psm 7 -c tessedit_char_whitelist=0123456789xX' 
        texto_ocr = pytesseract.image_to_string(img_processada, config=tesseract_config).strip()
        print(f"TEXTO OCR BRUTO LIDO: '{texto_ocr}'")
        
    except Exception as e:
        print(f"Erro ao recortar ou processar imagem: {e}")
        print("--- DEBUG OCR FALHOU ---")
        return 0

    
    quantidade_str = re.sub(r'[^0-9]', '', texto_ocr)
    
    try:
        if not quantidade_str:
            
            raise ValueError("String vazia após limpeza.")
            
        quantidade_atual = int(quantidade_str)
        print(f"QUANTIDADE FINAL EXTRAÍDA: {quantidade_atual}")
        print("--- DEBUG OCR CONCLUÍDO ---\n")
        return quantidade_atual
    except ValueError as ve:
        print(f"ERRO: OCR não retornou um número válido após limpeza (Resultado Limpo: '{quantidade_str}').")
        print(f"Detalhe do Erro: {ve}")
        print("--- DEBUG OCR FALHOU ---\n")
        return 0