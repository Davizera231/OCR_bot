from PIL import Image
import pytesseract
import re


pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


RECORTE_SUCATA_FIXO = (25, 210, 80, 250)
TEXTO_ALVO = "SUCATA E BORRACHAS" 


def extrair_quantidade_sucata(img: Image):
    
    
    try:
        
        img_recortada = img.crop(RECORTE_SUCATA_FIXO)
        
       
        texto_ocr = pytesseract.image_to_string(img_recortada, config='--psm 7').strip()
    except Exception as e:
        print(f"Erro ao recortar ou processar imagem: {e}")
        return 0

   
    quantidade_str = re.sub(r'[^0-9]', '', texto_ocr)
    
    try:
        quantidade_atual = int(quantidade_str)
        return quantidade_atual
    except ValueError:
        
        return 0