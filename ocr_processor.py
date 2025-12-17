from PIL import Image, ImageEnhance
from google.cloud import vision
from io import BytesIO
import json


GOOGLE_CLOUD_PROJECT_ID = "turing-diode-480814-f3"
TEXTO_ALVO = "SUCATA E BORRACHAS"
DEBUG_JSON_PATH = "cloud_vision_spatial_debug.json" 

def get_vision_client():
    return vision.ImageAnnotatorClient(
        client_options={'quota_project_id': GOOGLE_CLOUD_PROJECT_ID}
    )

def extrair_quantidade_sucata(img: Image):
    print(f"\n--- DEBUG OCR INICIADO (Modo HD: Upscale + Contraste) ---")
    
    try:
        
        SCALE_FACTOR = 2 
        
      
        original_w, original_h = img.size
        new_size = (original_w * SCALE_FACTOR, original_h * SCALE_FACTOR)
        img_processed = img.resize(new_size, Image.Resampling.LANCZOS)
        
        
        enhancer = ImageEnhance.Contrast(img_processed)
        img_processed = enhancer.enhance(1.5) 
        
        
        client = get_vision_client()
        img_bytes = BytesIO()
        img_processed.save(img_bytes, format='PNG')
        image = vision.Image(content=img_bytes.getvalue())
        
       
        response = client.document_text_detection(image=image)
        words = response.text_annotations
        
        if not words:
            print("ERRO: Nenhuma palavra encontrada.")
            return 0

        word_list = words[1:] 
        
       
        try:
            debug_data = [{'text': w.description, 'box': [(v.x, v.y) for v in w.bounding_poly.vertices]} for w in word_list]
            with open(DEBUG_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(debug_data, f, indent=4)
        except: pass

        
        
        sucata_word = None
        for w in word_list:
            text = w.description.upper()
            if "SUCATA" in text:
                sucata_word = w
                break
        
        if not sucata_word:
            print("ERRO: Palavra 'SUCATA' não encontrada.")
            return 0
            
      
        s_verts = sucata_word.bounding_poly.vertices
        sucata_x_min = min(v.x for v in s_verts)
        sucata_x_max = max(v.x for v in s_verts)
        sucata_y_min = min(v.y for v in s_verts) 
        sucata_y_max = max(v.y for v in s_verts) 
        
        print(f"ÂNCORA (HD): 'SUCATA' em X={sucata_x_min}, Y={sucata_y_min}")

       
        SEARCH_Y_TOP = sucata_y_min - (90 * SCALE_FACTOR) 
        SEARCH_Y_BOTTOM = sucata_y_max 
        
        
        SEARCH_X_LEFT = sucata_x_min - (60 * SCALE_FACTOR) 
        SEARCH_X_RIGHT = sucata_x_max + (20 * SCALE_FACTOR)
        
        print(f"ZONA HD: X[{SEARCH_X_LEFT}:{SEARCH_X_RIGHT}], Y[{SEARCH_Y_TOP}:{SEARCH_Y_BOTTOM}]")

        candidates = []
        
        for w in word_list:
            raw_text = w.description.lower()
            
            
            if '.' in raw_text or ',' in raw_text: continue 
            if 'kg' in raw_text or (raw_text.endswith('g') and raw_text[:-1].isdigit()): continue 

           
            clean_text = raw_text.replace('x', '').replace('l', '1').replace('o', '0')
            
            if clean_text.isdigit():
                w_verts = w.bounding_poly.vertices
                w_cx = (w_verts[0].x + w_verts[1].x) / 2
                w_cy = (w_verts[0].y + w_verts[2].y) / 2
                
                
                if (SEARCH_X_LEFT <= w_cx <= SEARCH_X_RIGHT and 
                    SEARCH_Y_TOP <= w_cy <= SEARCH_Y_BOTTOM):
                    
                    val = int(clean_text)
                    if val < 5000:
                        candidates.append({
                            'text': raw_text,
                            'value': val,
                            'y': w_cy
                        })

        if candidates:
           
            candidates.sort(key=lambda c: (
                'x' in c['text'], 
                c['y']
            ), reverse=True)
            
            best_match = candidates[0]
            print(f"✅ QUANTIDADE EXTRAÍDA: {best_match['value']} (Original: {best_match['text']})")
            print("--- DEBUG OCR CONCLUÍDO ---\n")
            return best_match['value']
        else:
            print("ERRO: Nenhum número encontrado na zona do slot (mesmo com Upscale).")
            return 0

    except Exception as e:
        print(f"ERRO CRÍTICO: {e}")
        return 0